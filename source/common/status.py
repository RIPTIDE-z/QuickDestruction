"""阶段状态 - 由资产上记录的生效配置与场景现状判定各阶段成果, 面板显示与重跑判断共用

状态只有三种: 未完成, 已过期 (附原因), 输入未变; 判定只读数据, 不写任何东西, 可以在绘制期调用
手动修改刚体属性这类不经插件的改动不会被追踪, 所以最好的状态叫"输入未变"而不是"最新"
"""

import hashlib
from pathlib import Path
from typing import NamedTuple

from . import asset, drivers, shards

# 三种状态
NONE = "NONE"
OUTDATED = "OUTDATED"
CURRENT = "CURRENT"

# 物理过期原因: 碎块数与应用时不同
SHARD_COUNT_MESSAGE = "The number of shards changed"
# 物理过期原因: 有碎块的刚体数据被删除
LOST_BODY_MESSAGE = "Some shards lost their rigid body"
# 资产本身不可用, 原因见报错
INVALID_ASSET_MESSAGE = "The asset is not usable"


class Status(NamedTuple):
    """一个阶段的状态"""

    # NONE, OUTDATED 或 CURRENT
    state: str
    # 已过期时的原因, 英文消息键, 按框架上下文翻译
    reason: str = ""


def physics_status(root):
    """资产的物理状态: 没有生效记录为未完成; 碎块数变了或刚体数据丢了为已过期"""
    record = asset.physics_record(root)
    if record is None:
        return Status(NONE)
    try:
        pieces = asset.shards_of(root)
    except asset.AssetError:
        return Status(OUTDATED, INVALID_ASSET_MESSAGE)
    if record.get("shards") != len(pieces):
        return Status(OUTDATED, SHARD_COUNT_MESSAGE)
    if any(piece.rigid_body is None for piece in pieces):
        return Status(OUTDATED, LOST_BODY_MESSAGE)
    return Status(CURRENT)


# 模拟过期原因: 物理已过期
PHYSICS_OUTDATED_MESSAGE = "Physics is outdated"
# 模拟过期原因: 烘焙之后又应用过物理
PHYSICS_REAPPLIED_MESSAGE = "Physics was applied again"
# 模拟过期原因: 场景里找不到这次模拟的会话记录
RUN_MISSING_MESSAGE = "The simulation record is missing"
# 模拟过期原因: 一起模拟的另一个资产变了或不在了
PARTNER_MESSAGE = "Another asset of the same simulation changed"
# 模拟过期原因: 驱动变了
DRIVERS_MESSAGE = "Drivers changed"
# 模拟过期原因: 场景里其他刚体变了
ENVIRONMENT_MESSAGE = "Other rigid bodies changed"
# 模拟过期原因: 帧率, 单位或重力变了
SCENE_MESSAGE = "Frame rate, units or gravity changed"


# 绑定过期原因: 资产没有模拟成果
RIG_NO_SIMULATION_MESSAGE = "The simulation was deleted"
# 绑定过期原因: 绑定之后又烘焙过
RIG_SIMULATION_CHANGED_MESSAGE = "The simulation was baked again"
# 绑定过期原因: 模拟本身已过期
RIG_SIMULATION_OUTDATED_MESSAGE = "The simulation is outdated"
# 绑定过期原因: 导出集合被删除
RIG_COLLECTION_MISSING_MESSAGE = "The export collection is missing"
# 导出过期原因: 绑定之后又重建过
EXPORT_RIG_REBUILT_MESSAGE = "The rig was built again"
# 导出过期原因: 写出的文件不见了
EXPORT_FILE_MISSING_MESSAGE = "An exported file is missing"


def _number(value):
    """签名里的浮点数格式, 与驱动摘要相同的量化规则"""
    return drivers.quantize(value)


def _hash(parts):
    """一组字符串的摘要"""
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


def scene_signature(scene):
    """影响模拟结果的场景设置: 帧率, 帧率基数, 单位比例与重力"""
    gravity = ",".join(_number(value) for value in scene.gravity)
    unit = scene.unit_settings.scale_length
    render = scene.render
    return _hash(
        [str(render.fps), _number(render.fps_base), _number(unit), gravity, str(scene.use_gravity)]
    )


def environment_signature(scene):
    """刚体世界里不属于任何资产的刚体: 名字, 自身变换与主要刚体属性; 没有刚体世界时为空串

    用 matrix_basis 而不是 matrix_world: 动力学刚体的世界矩阵随模拟逐帧变化, 自身变换不受模拟影响
    """
    world = scene.rigidbody_world
    if world is None or world.collection is None:
        return ""
    parts = []
    for obj in sorted(world.collection.objects, key=lambda item: item.name):
        if shards.asset_of(obj) is not None:
            continue
        matrix = ",".join(_number(value) for row in obj.matrix_basis for value in row)
        body = obj.rigid_body
        values = (
            ()
            if body is None
            else (body.type, body.collision_shape, body.mass, body.friction, body.restitution)
        )
        parts.append(f"{obj.name}:{matrix}:{values}")
    return _hash(parts)


def drivers_signature(scene):
    """场景里启用中的驱动的摘要"""
    return drivers.digest([drivers.snapshot(obj) for obj in drivers.driver_objects(scene)])


def _run_partners_changed(run, run_id):
    """会话里任一参与资产不在了, 物理修订号与会话记录不同, 或已不再保留这次会话的成果

    最后一条: 同一会话的资产互相碰撞过, 其中一个被复位或单独重新烘焙后, 另一个的成果就和它对不上了
    """
    index = asset.roots_by_id()
    for asset_id, revision in run["assets"].items():
        roots = index.get(asset_id)
        record = asset.physics_record(roots[0]) if roots else None
        if record is None or record.get("rev") != revision:
            return True
        result = asset.sim_record(roots[0])
        if result is None or result.get("run") != run_id:
            return True
    return False


def _run_status(run, run_id, scene):
    """会话层面的过期判定: 参与资产, 驱动, 环境刚体与场景设置"""
    if _run_partners_changed(run, run_id):
        return Status(OUTDATED, PARTNER_MESSAGE)
    if run["drivers"] != drivers_signature(scene):
        return Status(OUTDATED, DRIVERS_MESSAGE)
    if run["environment"] != environment_signature(scene):
        return Status(OUTDATED, ENVIRONMENT_MESSAGE)
    if run["scene"] != scene_signature(scene):
        return Status(OUTDATED, SCENE_MESSAGE)
    return Status(CURRENT)


def simulation_status(root, scene):
    """资产的模拟状态: 没有成果为未完成; 物理过期或重新应用, 会话输入变化时为已过期"""
    record = asset.sim_record(root)
    if record is None or asset.sim_action(root) is None:
        return Status(NONE)
    if physics_status(root).state != CURRENT:
        return Status(OUTDATED, PHYSICS_OUTDATED_MESSAGE)
    if asset.physics_record(root).get("rev") != record.get("physics_rev"):
        return Status(OUTDATED, PHYSICS_REAPPLIED_MESSAGE)
    run = asset.runs(scene).get(record.get("run"))
    if run is None:
        return Status(OUTDATED, RUN_MISSING_MESSAGE)
    return _run_status(run, record.get("run"), scene)


def rig_status(root, scene):
    """资产的绑定状态: 没有构建为未完成; 模拟成果变了或不在了, 物理重新应用过, 导出集合被删为已过期"""
    record = asset.rig_record(root)
    if record is None:
        return Status(NONE)
    if asset.rig_collection(root) is None:
        return Status(OUTDATED, RIG_COLLECTION_MISSING_MESSAGE)
    result = asset.sim_record(root)
    if result is None or asset.sim_action(root) is None:
        return Status(OUTDATED, RIG_NO_SIMULATION_MESSAGE)
    if result.get("run") != record.get("run"):
        return Status(OUTDATED, RIG_SIMULATION_CHANGED_MESSAGE)
    physics = asset.physics_record(root)
    if physics is None or physics.get("rev") != record.get("physics_rev"):
        return Status(OUTDATED, PHYSICS_REAPPLIED_MESSAGE)
    if simulation_status(root, scene).state != CURRENT:
        return Status(OUTDATED, RIG_SIMULATION_OUTDATED_MESSAGE)
    return Status(CURRENT)


def export_status(root):
    """资产的导出状态: 没有导出为未完成; 绑定重建过或文件不见了为已过期"""
    record = asset.export_record(root)
    if record is None:
        return Status(NONE)
    rig = asset.rig_record(root)
    if rig is None or rig.get("id") != record.get("rig"):
        return Status(OUTDATED, EXPORT_RIG_REBUILT_MESSAGE)
    if any(not Path(path).is_file() for path in record.get("files", ())):
        return Status(OUTDATED, EXPORT_FILE_MISSING_MESSAGE)
    return Status(CURRENT)
