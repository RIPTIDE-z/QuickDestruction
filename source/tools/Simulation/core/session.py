"""模拟会话 - 一次模拟以场景为单位: 固定参与资产, 时间设置, 驱动快照与环境签名, 编译每块碎块的计划

这里只做校验与计算, 不改动场景; 写入, 推进与提交由 bake 模块完成
"""

from dataclasses import dataclass

from ....common import asset, drivers, impulse, rigidbody, shards
from ....common.blender_context import vertex_coordinates
from ....common.status import (
    CURRENT,
    drivers_signature,
    environment_signature,
    physics_status,
    scene_signature,
)
from ..constants import STAGE
from ..errors import SimulationError
from ..settings import frame_range


@dataclass
class Participant:
    """参与模拟的一块碎块"""

    # 碎块物体
    shard: object
    # 所属资产的标识
    asset_id: str
    # 静止姿态
    rest: object
    # 本块的计划
    plan: impulse.ShardPlan
    # 临时动作里的曲线, 推进途中插入交接关键帧用; 锚定的碎块没有
    curves: dict | None = None


@dataclass
class Session:
    """一次模拟会话的全部输入"""

    # 场景与刚体世界
    scene: object
    world: object
    # 参与的资产根集合
    roots: list
    # 参与的碎块, 按资产名与路径排序, 也是加入刚体世界的顺序
    participants: list
    # 起止帧
    frame_start: int
    frame_end: int
    # 每推进一帧经过的模拟秒
    dt: float
    # 实际使用的求解设置, 写进会话记录
    settings: dict
    # 驱动, 环境刚体与场景设置的签名, 写进会话记录供过期判定
    signatures: dict
    # 起动帧被推迟的事件数
    clamped: int


def _check_assets(roots):
    """每个资产的物理都必须处于输入未变的状态, 返回 [(根集合, 碎块列表), ...]"""
    result = []
    for root in roots:
        if physics_status(root).state != CURRENT:
            raise SimulationError('Apply physics to "{name}" first', name=root.name)
        result.append((root, asset.shards_of(root)))
    return result


def _check_animation(pairs):
    """碎块的动作只能是本插件为同一资产建立的模拟动作, 交接需要独占碎块的动画"""
    for root, items in pairs:
        asset_id = shards.asset_of(root)
        for shard in items:
            data = shard.animation_data
            action = data.action if data is not None else None
            if action is not None and not asset.owned(action, asset_id, STAGE):
                raise SimulationError(
                    'Shard "{name}" has its own animation; clear it before simulating',
                    name=shard.name,
                )


def _rules(root):
    """资产物理生效记录里的激活规则"""
    record = asset.physics_record(root)
    return impulse.Rules(
        activation=record["activation"],
        anchor_bottom=bool(record["use_anchor_bottom"]),
        anchor_height=float(record["anchor_height"]),
    )


def _info(shard, rest, asset_id):
    """碎块在静止姿态下的编译信息: 质心是原点的位置, 最低点取静止姿态下的世界坐标"""
    flat = vertex_coordinates(shard.data)
    rows = rest.row
    lowest = min(
        rows[2][0] * flat[index] + rows[2][1] * flat[index + 1] + rows[2][2] * flat[index + 2]
        for index in range(0, len(flat), 3)
    )
    key = asset_id + "/" + "/".join(str(index) for index in shards.path_of(shard))
    return impulse.ShardInfo(key=key, center=tuple(rest.translation), lowest=lowest + rest[2][3])


def _timing(scene, values):
    """起止帧与每帧的模拟秒; 结束帧必须晚于起始帧"""
    start, end = frame_range(values, scene)
    if end <= start:
        raise SimulationError("End Frame must be after Start Frame")
    return start, end, rigidbody.frame_seconds(scene, values.time_scale)


def build(context, roots, values):
    """校验输入并编译计划, 返回 Session; 任何不满足的条件都在改动场景之前报错"""
    scene = context.scene
    rigidbody.check_cache(scene)
    # 脚本改过的驱动变换要先求值, 否则计划用的是旧位置, 烘焙后的驱动签名也会与面板对不上
    context.view_layer.update()
    world = scene.rigidbody_world
    if world is None or world.collection is None:
        raise SimulationError("Apply physics before simulating")
    pairs = _check_assets(roots)
    _check_animation(pairs)
    start, end, dt = _timing(scene, values)
    snapshots = [drivers.snapshot(obj) for obj in drivers.driver_objects(scene)]
    timing = impulse.Timing(frame_start=start, dt=dt)
    participants = []
    clamped = 0
    for root, items in pairs:
        asset_id = shards.asset_of(root)
        rests = [asset.rest_matrix(shard) for shard in items]
        infos = [_info(shard, rest, asset_id) for shard, rest in zip(items, rests, strict=True)]
        plan = impulse.compile_plan(infos, snapshots, _rules(root), timing)
        clamped += plan.clamped
        for shard, rest, info in zip(items, rests, infos, strict=True):
            participants.append(Participant(shard, asset_id, rest, plan.shards[info.key]))
    settings = {
        "frame_start": start,
        "frame_end": end,
        "substeps": values.substeps,
        "solver_iterations": values.solver_iterations,
        "time_scale": values.time_scale,
    }
    signatures = {
        "drivers": drivers_signature(scene),
        "environment": environment_signature(scene),
        "scene": scene_signature(scene),
    }
    return Session(
        scene, world, list(roots), participants, start, end, dt, settings, signatures, clamped
    )
