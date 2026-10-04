"""模拟会话 - 一次模拟以场景为单位: 固定参与资产, 时间设置, 驱动快照与环境签名, 编译每块碎块的计划

这里只做校验与计算, 不改动场景; 写入, 推进与提交由 bake 模块完成
"""

from dataclasses import dataclass, field

from ....common import asset, drivers, impulse, planning, rigidbody, shards
from ....common.status import (
    CURRENT,
    drivers_signature,
    environment_signature,
    physics_status,
    scene_signature,
)
from ..constants import STAGE, SUBSTEP_LIMIT
from ..errors import SimulationError
from ..settings import FINAL, frame_range
from .blocking import Walls


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

    # 场景
    scene: object
    # 刚体世界
    world: object
    # 参与的资产根集合
    roots: list
    # 参与的碎块, 按资产名与路径排序, 也是加入刚体世界的顺序
    participants: list
    # 起始帧
    frame_start: int
    # 结束帧
    frame_end: int
    # 每推进一帧经过的模拟秒
    dt: float
    # 实际使用的求解设置, 写进会话记录
    settings: dict
    # 驱动, 环境刚体与场景设置的签名, 写进会话记录供过期判定
    signatures: dict
    # 起动帧被推迟的事件数
    clamped: int
    # 不让路的物体, 起动时据此判定受阻
    walls: Walls
    # 驱动快照, 弹道的飞行动画按驱动名找回对应的物体
    snapshots: list = field(default_factory=list)
    # 弹道驱动名 -> 对各个资产的飞行计划
    flights: dict = field(default_factory=dict)
    # 终稿质量下的子步建议值, 0 表示没有建议
    suggested: int = 0
    # 建议值是否被子步上限截断
    capped: bool = False


def _check_assets(roots):
    """每个资产的物理都必须处于输入未变的状态

    roots 资产根集合序列
    返回 [(根集合, 碎块列表), ...]; 物理没有应用或已过期时抛 SimulationError
    """
    asset.validate(roots)
    result = []
    for root in roots:
        # 物理过期说明碎块数或刚体数据变了, 烘焙出的质量与质心不可信
        if physics_status(root).state != CURRENT:
            raise SimulationError('Apply physics to "{name}" first', name=root.name)
        result.append((root, asset.shards_of(root)))
    return result


def _check_parents(pairs):
    """碎块不能有父级: 关键帧写在碎块自己的变换通道里, 刚体也按世界姿态推进, 有父级时两者对不上

    pairs [(根集合, 碎块列表), ...]
    """
    for _root, items in pairs:
        for shard in items:
            if shard.parent is not None:
                raise SimulationError(
                    'Shard "{name}" has a parent; unparent it before simulating', name=shard.name
                )


def _check_animation(pairs):
    """碎块的动作只能是本插件为同一资产建立的模拟动作, 交接需要独占碎块的动画

    pairs [(根集合, 碎块列表), ...]
    """
    for root, items in pairs:
        asset_id = shards.asset_of(root)
        for shard in items:
            data = shard.animation_data
            action = data.action if data is not None else None
            # 别人的动画会与交接关键帧冲突, 必须先清掉
            if action is not None and not asset.owned(action, asset_id, STAGE):
                raise SimulationError(
                    'Shard "{name}" has its own animation; clear it before simulating',
                    name=shard.name,
                )


def _substeps(values, compiled, timing):
    """实际使用的子步数: 终稿质量取设定值与建议值中较大的, 不超过上限

    values 参数值
    compiled AssetPlan 列表
    timing 时间换算
    返回 (子步数, 建议值, 建议值是否被上限截断)
    """
    # 预览质量按设定值, 不算建议值
    if values.quality != FINAL:
        return values.substeps, 0, False
    suggested, capped = planning.suggested_substeps(compiled, timing, SUBSTEP_LIMIT)
    return max(values.substeps, suggested), suggested, capped


def _timing(scene, values):
    """求起止帧与每帧的模拟秒; 结束帧必须晚于起始帧

    scene 场景
    values 参数值
    返回 (起始帧, 结束帧, 每帧的模拟秒)
    """
    start, end = frame_range(values, scene)
    if end <= start:
        raise SimulationError("End Frame must be after Start Frame")
    return start, end, rigidbody.frame_seconds(scene, values.time_scale)


def suggestion(context, roots, values):
    """终稿质量的子步建议值 (启发式)

    不改动场景, 不做起动受阻判定
    资产的物理不是输入未变的状态, 或计划输入不满足时返回 (0, False)
    context Blender 上下文
    roots 资产根集合序列
    values 参数组或参数值
    返回 (建议值, 是否被上限截断)
    """
    scene = context.scene
    # 物理过期时不能编译计划
    if any(physics_status(root).state != CURRENT for root in roots):
        return 0, False
    # 面板每次绘制都会调用本函数, 输入不满足时静默返回而不是报错
    try:
        start, _end, dt = _timing(scene, values)
        snapshots = [drivers.snapshot(obj) for obj in drivers.driver_objects(scene)]
        depsgraph = context.evaluated_depsgraph_get()
        timing = impulse.Timing(frame_start=start, dt=dt)
        compiled = [planning.compile_asset(root, snapshots, timing, depsgraph) for root in roots]
    except (SimulationError, planning.PlanError):
        return 0, False
    return planning.suggested_substeps(compiled, timing, SUBSTEP_LIMIT)


def build(context, roots, values):
    """校验输入并编译计划; 任何不满足的条件都在改动场景之前报错

    context Blender 上下文
    roots 参与的资产根集合列表
    values 参数值
    返回 Session
    """
    scene = context.scene
    rigidbody.check_cache(scene)
    # 脚本改过的驱动变换要先求值, 否则计划用的是旧位置, 烘焙后的驱动签名也会与面板对不上
    context.view_layer.update()
    world = scene.rigidbody_world
    # 没有刚体世界说明还没有应用过物理
    if world is None or world.collection is None:
        raise SimulationError("Apply physics before simulating")
    pairs = _check_assets(roots)
    _check_parents(pairs)
    _check_animation(pairs)
    start, end, dt = _timing(scene, values)
    snapshots = [drivers.snapshot(obj) for obj in drivers.driver_objects(scene)]
    timing = impulse.Timing(frame_start=start, dt=dt)
    depsgraph = context.evaluated_depsgraph_get()
    compiled = [
        planning.compile_asset(root, snapshots, timing, depsgraph) for root, _items in pairs
    ]
    # 把各资产的碎块摊平成参与者列表; 弹道的飞行计划按驱动名归并
    participants = []
    flights = {}
    for (root, _items), item in zip(pairs, compiled, strict=True):
        asset_id = shards.asset_of(root)
        for shard, rest, info in zip(item.shards, item.rests, item.infos, strict=True):
            participants.append(Participant(shard, asset_id, rest, item.plan.shards[info.key]))
        for name, flight in item.flights.items():
            flights.setdefault(name, []).append(flight)
    clamped = sum(item.plan.clamped for item in compiled)
    substeps, suggested, capped = _substeps(values, compiled, timing)
    settings = {
        "frame_start": start,
        "frame_end": end,
        "substeps": substeps,
        "solver_iterations": values.solver_iterations,
        "time_scale": values.time_scale,
        # 面板上的请求值与质量档; 状态判定拿它们与面板比较, 而不是与提高后的实际子步比较
        "requested": values.substeps,
        "quality": values.quality,
    }
    # 会话记录里存的签名: 之后任何一项变化都会让成果过期
    signatures = {
        "drivers": drivers_signature(scene),
        "environment": environment_signature(scene),
        "scene": scene_signature(scene),
    }
    walls = Walls(participants, scene, dt)
    return Session(
        scene,
        world,
        list(roots),
        participants,
        start,
        end,
        dt,
        settings,
        signatures,
        clamped,
        walls,
        snapshots,
        flights,
        suggested,
        capped,
    )
