"""烘焙流程 - 写入会话设置与交接关键帧, 逐帧推进并采样, 写成烘焙动作, 校验后提交

两阶段提交: 推进期间碎块使用临时动作, 旧成果原样保留
全部成功后才换上新动作并删除旧成果与基于它构建的绑定
两本写前快照: temp 记录会话期间的临时改动 (世界设置, 世界成员, 刚体开关), 无论成败结束时都撤回,
撤回时参与的碎块也随之移出刚体世界; result 记录对碎块动画与位姿的改动, 只在失败或取消时撤回
"""

import uuid
from dataclasses import dataclass, replace
from time import perf_counter
from typing import NamedTuple

import bpy
from mathutils import Vector

from ....common import asset, impulse, rigidbody, shards
from ....common.created_data import CreatedData, WriteLog, run_all
from ..constants import PENDING_PREFIX, PENDING_STAGE, RESULT_PREFIX, STAGE, STATIC_EPSILON
from . import flight, keys
from .session import build


class Progress(NamedTuple):
    """推进进度: 已推进的帧数与总帧数"""

    # 已推进的帧数
    done: int
    # 总帧数
    total: int


@dataclass(frozen=True)
class SimulationResult:
    """一次烘焙的结果"""

    # 本次会话的标识
    run_id: str
    # 烘焙的帧数
    frames: int
    # 参与模拟的碎块数
    shards: int
    # 动过而写了关键帧的碎块数
    moved: int
    # 推进途中再交接的次数
    rehandoffs: int
    # 起动帧被推迟到起始帧之后第一帧的事件数
    clamped: int
    # 起动速度或自转因被冻结, 锚定或环境刚体挡住而被改写的次数
    blocked: int
    # 整段冻结不动的碎块数: 没有被任何驱动波及的冻结碎块
    frozen: int
    # 被区域排除在模拟之外的碎块数
    excluded: int
    # 实际使用的每帧子步数
    substeps: int
    # 终稿质量把子步提高到了建议值
    raised: bool
    # 终稿质量下的子步建议值, 0 表示没有建议
    suggested: int
    # 建议值是否被子步上限截断
    capped: bool
    # 耗时, 秒
    seconds: float


def _set_world(session, temp):
    """会话期间的世界设置: 帧范围与求解设置; 点缓存的起止帧按不会被互相钳制的顺序写入

    session 模拟会话
    temp 会话期间的临时改动快照, 结束时撤回
    """
    world, settings = session.world, session.settings
    temp.set(world, "enabled", True)
    temp.set(world, "substeps_per_frame", settings["substeps"])
    temp.set(world, "solver_iterations", settings["solver_iterations"])
    temp.set(world, "time_scale", settings["time_scale"])
    cache = world.point_cache
    # 新起始帧不早于旧结束帧时先写结束帧, 否则先写起始帧, 两者都不会被对方钳制
    order = ("frame_start", "frame_end")
    if session.frame_start >= cache.frame_end:
        order = ("frame_end", "frame_start")
    for name in order:
        temp.set(cache, name, getattr(session, name))


def _exclude_others(session, temp):
    """刚体世界里不参与本次会话的资产碎块临时移出, 结束时再放回

    session 模拟会话
    temp 会话期间的临时改动快照
    """
    taking = {
        participant.shard.name for participant in session.participants if not _excluded(participant)
    }
    members = session.world.collection.objects
    # 复制成列表再遍历: 循环里会改动集合成员
    for obj in list(members):
        # 带资产标识但不参与本次会话的碎块先移出, 登记放回的撤回动作
        if shards.asset_of(obj) is not None and obj.name not in taking:
            members.unlink(obj)
            temp.add(lambda obj=obj: members.link(obj))


def _excluded(participant):
    """被区域排除的碎块: 不进刚体世界, 不带动画, 停在静止姿态

    participant 参与者
    返回 被排除为真
    """
    return participant.plan.mode == impulse.EXCLUDED


def _static(participant):
    """整段都不会动的碎块: 被排除的, 锚定的, 以及没有任何起动事件的冻结碎块

    除了被排除的, 其余按被动刚体参与: 与动画控制的刚体一样不可撞动, 但不必每个子步插值与激活,
    也不需要动画曲线, 碎块多而被波及的少时能省下大部分推进开销
    participant 参与者
    返回 整段不动为真
    """
    mode = participant.plan.mode
    return mode in (impulse.ANCHORED, impulse.EXCLUDED) or (
        mode == impulse.FROZEN and not participant.plan.events
    )


def _set_body(participant, temp):
    """按激活方式设置刚体: 不动的为被动; 冻结先受动画控制; 休眠从休眠开始; 自由直接参与

    participant 参与者
    temp 会话期间的临时改动快照
    """
    # 被排除的碎块不进刚体世界, 不设置刚体
    if _excluded(participant):
        return
    body = participant.shard.rigid_body
    mode = participant.plan.mode
    temp.set(body, "enabled", True)
    temp.set(body, "type", "PASSIVE" if _static(participant) else "ACTIVE")
    # 冻结的碎块起动前受动画控制, 起动帧交接后才交还模拟
    temp.set(body, "kinematic", mode == impulse.FROZEN)
    sleeping = mode == impulse.SLEEPING
    temp.set(body, "use_deactivation", sleeping)
    temp.set(body, "use_start_deactivated", sleeping)


def _prepare_shard(participant, pending, session, result):
    """碎块回到静止姿态, 改用四元数旋转, 绑定临时动作并写下起始帧与预先确定的首次交接

    participant 参与者
    pending 本资产的临时动作
    session 模拟会话
    result 对碎块动画与位姿的改动快照, 失败或取消时撤回
    """
    shard = participant.shard
    keys.remember_animation(shard, result)
    result.set(shard, "rotation_mode", "QUATERNION")
    result.set(shard, "matrix_world", participant.rest.copy())
    # 整段不动的碎块不需要动画曲线
    if _static(participant):
        shard.animation_data_create().action = None
        return
    location, rotation, _scale = participant.rest.decompose()
    frozen = participant.plan.mode == impulse.FROZEN
    curves = keys.bind(shard, pending, kinematic=True)
    keys.key_kinematic(curves, session.frame_start, frozen)
    keys.key_pose(curves, session.frame_start, location, rotation)
    events = participant.plan.events
    # 冻结碎块的第一次交接在起动帧之前就能确定, 预先写好, 之后的事件在推进途中再写
    if frozen and events:
        event = session.walls.first_event(participant)
        keys.key_handoff(curves, event.frame, location, rotation, event, session.dt)
    participant.curves = curves


def _prepare(session, temp, result, tracker):
    """写入会话期间的全部设置

    session 模拟会话
    temp 会话期间的临时改动快照
    result 对碎块动画与位姿的改动快照
    tracker 创建账本
    返回 {资产标识: 临时动作}
    """
    _set_world(session, temp)
    _exclude_others(session, temp)
    pending = {}
    # 每个资产一个临时动作, 推进期间碎块用它; 旧成果动作在提交前原样保留
    for root in session.roots:
        action = tracker.track("actions", bpy.data.actions.new(PENDING_PREFIX + root.name))
        asset.claim(action, shards.asset_of(root), PENDING_STAGE)
        pending[shards.asset_of(root)] = action
    for participant in session.participants:
        _set_body(participant, temp)
        _prepare_shard(participant, pending[participant.asset_id], session, result)
    # 被排除的碎块不加入刚体世界
    ordered = [
        participant.shard for participant in session.participants if not _excluded(participant)
    ]
    rigidbody.link(session.world, ordered)
    temp.add(lambda: rigidbody.unlink(session.world, ordered))
    return pending


def _online_events(session):
    """推进途中才写的交接: 冻结碎块的第二次及以后的事件, 其他激活方式的全部事件

    session 模拟会话
    返回 {帧: [(参与者, 事件)]}
    """
    schedule = {}
    for participant in session.participants:
        events = participant.plan.events
        if _static(participant):
            continue
        # 冻结碎块的第一次事件已在准备阶段写好
        if participant.plan.mode == impulse.FROZEN:
            events = events[1:]
        for event in events:
            schedule.setdefault(event.frame, []).append((participant, event))
    return schedule


def _sample(samples, participant):
    """记下碎块当前的位置与朝向, 四元数与上一帧保持同一符号

    samples {碎块名: ([位置], [四元数])}
    participant 参与者
    """
    location, rotation, _scale = participant.shard.matrix_world.decompose()
    track = samples[participant.shard.name]
    # q 与 -q 同一朝向; 与上一帧取同一符号, 写成关键帧后插值才不会绕远路
    if track[1] and rotation.dot(track[1][-1]) < 0.0:
        rotation = -rotation
    track[0].append(location)
    track[1].append(rotation)


def _carry_motion(samples, participant, matrix, event, dt):
    """途中起动的事件叠加在碎块当时的速度上: 下落, 滑动与自转都保留, 事件给的是增量而不是替换

    当前速度取上一帧采样到这一帧的平均值; 还没有采样时事件原样返回
    samples {碎块名: ([位置], [四元数])}
    participant 参与者
    matrix 碎块当前的世界矩阵
    event 计划里的事件, 速度是增量
    dt 一帧经过的模拟秒
    返回 叠加了当前速度的事件
    """
    track = samples.get(participant.shard.name)
    # 还没有采样: 没有"上一帧"可比, 事件原样使用
    if not track or not track[0]:
        return event
    velocity, spin = keys.motion(track[0][-1], track[1][-1], matrix, dt)
    return replace(
        event,
        velocity=tuple(Vector(event.velocity) + velocity),
        spin=tuple(Vector(event.spin) + spin),
    )


def _run(session):
    """逐帧推进: 先求值这一帧, 再插入到期的再交接, 最后采样; 每帧产出一次进度

    session 模拟会话
    返回 (采样, 再交接次数); 采样是 {碎块名: ([位置], [四元数])}
    """
    scene = session.scene
    schedule = _online_events(session)
    sampled = [participant for participant in session.participants if not _static(participant)]
    samples = {participant.shard.name: ([], []) for participant in sampled}
    total = session.frame_end - session.frame_start + 1
    rehandoffs = 0
    for index, frame in enumerate(range(session.frame_start, session.frame_end + 1)):
        # 求值这一帧: 刚体世界推进一帧, 碎块得到这一帧的位姿
        scene.frame_set(frame)
        # 到期的再交接: 叠加当前速度, 经受阻判定后写成交接关键帧
        for participant, event in schedule.get(frame, ()):
            matrix = participant.shard.matrix_world
            event = _carry_motion(samples, participant, matrix, event, session.dt)
            event = session.walls.limit(participant, matrix, event)
            location, rotation, _scale = matrix.decompose()
            keys.key_handoff(participant.curves, frame, location, rotation, event, session.dt)
            rehandoffs += 1
        for participant in sampled:
            _sample(samples, participant)
        yield Progress(index + 1, total)
    return samples, rehandoffs


def _moved(track):
    """整段采样里是否离开过第一帧的位姿

    track ([位置], [四元数])
    返回 动过为真
    """
    locations, rotations = track
    first_location, first_rotation = locations[0], rotations[0]
    return any(
        (location - first_location).length > STATIC_EPSILON
        or first_rotation.rotation_difference(rotation).angle > STATIC_EPSILON
        for location, rotation in zip(locations, rotations, strict=True)
    )


def _write_results(session, samples, tracker):
    """为每个资产建烘焙动作, 动过的碎块写逐帧关键帧, 没动的碎块不带动画停在静止姿态

    session 模拟会话
    samples 逐帧采样
    tracker 创建账本
    返回 ({资产标识: 动作}, {资产标识: 动过的块数})
    """
    finals = {}
    for root in session.roots:
        asset_id = shards.asset_of(root)
        action = tracker.track("actions", bpy.data.actions.new(RESULT_PREFIX + root.name))
        asset.claim(action, asset_id, STAGE)
        finals[asset_id] = action
    frames = list(range(session.frame_start, session.frame_end + 1))
    moved = dict.fromkeys(finals, 0)
    for participant in session.participants:
        shard = participant.shard
        track = samples.get(shard.name)
        # 动过的碎块写逐帧关键帧
        if track is not None and _moved(track):
            keys.write_track(shard, finals[participant.asset_id], frames, *track)
            moved[participant.asset_id] += 1
            continue
        # 没动过的碎块: 不带动画, 回到静止姿态
        shard.animation_data_create().action = None
        shard.matrix_world = participant.rest
    return finals, moved


def _remember_records(session, result):
    """提交前记下各资产的模拟记录与场景的会话记录, 提交中途失败时恢复

    session 模拟会话
    result 对碎块动画与位姿的改动快照
    """
    runs = asset.runs(session.scene)
    result.add(lambda: asset.set_runs(session.scene, runs))
    for root in session.roots:
        record, action = asset.sim_record(root), asset.sim_action(root)
        # 原来没有成果: 撤回就是清除; 原来有: 撤回时写回旧记录与旧动作
        if record is None:
            result.add(lambda root=root: asset.clear_sim_result(root))
        else:
            result.add(lambda r=root, rec=record, act=action: asset.set_sim_result(r, rec, act))


def _anchored_ordinals(session):
    """每个资产里设计上静止的碎块序号: 被区域或底部锚定的, 以及被区域排除的

    它们由物理设置与驱动区域决定, 与这次模拟里驱动怎样撞击无关, 同一套设置下的不同模拟结果一致;
    序号是碎块在资产里按路径排序后的位置, 与参与者列表里同一资产的顺序一致, 也是绑定构建使用的碎块序号
    session 模拟会话
    返回 {资产标识: [序号, ...]}
    """
    ordinals = {}
    counts = {}
    for item in session.participants:
        found = ordinals.setdefault(item.asset_id, [])
        position = counts.get(item.asset_id, 0)
        counts[item.asset_id] = position + 1
        if item.plan.mode in (impulse.ANCHORED, impulse.EXCLUDED):
            found.append(position)
    return ordinals


def _commit(session, finals, pending, moved, flights, result):
    """写入会话与资产的模拟记录, 删除临时动作, 最后删除各资产的旧成果动作

    删除动作放在最后: 之前任一步失败都能按写前快照恢复, 旧成果还在
    session 模拟会话
    finals {资产标识: 新成果动作}
    pending {资产标识: 临时动作}
    moved {资产标识: 动过的碎块数}
    flights 本次新建的飞行动作列表
    result 对碎块动画与位姿的改动快照
    返回 会话标识
    """
    _remember_records(session, result)
    run_id = uuid.uuid4().hex
    anchored = _anchored_ordinals(session)
    revisions = {}
    for root in session.roots:
        revisions[shards.asset_of(root)] = asset.physics_record(root)["rev"]
    old_actions = []
    # 每个资产写入新的模拟记录并指向新成果动作, 旧动作先记下稍后删除
    for root in session.roots:
        asset_id = shards.asset_of(root)
        old_actions.append((asset.sim_action(root), asset_id))
        record = {
            "run": run_id,
            "physics_rev": revisions[asset_id],
            "frame_start": session.frame_start,
            "frame_end": session.frame_end,
            "moved": moved[asset_id],
            # 设计上静止的碎块序号: 绑定构建据此把它们并成一个整体
            "anchored": anchored[asset_id],
        }
        asset.set_sim_result(root, record, finals[asset_id])
    run = {"assets": revisions, **session.signatures, "settings": session.settings}
    asset.add_run(session.scene, run_id, run)
    for action in pending.values():
        bpy.data.actions.remove(action)
    for action in flight.stale(flights):
        bpy.data.actions.remove(action)
    for action, asset_id in old_actions:
        # 旧成果动作不是新动作, 且确实属于本插件与这个资产, 才能删除
        stale = action is not None and action not in finals.values()
        if stale and asset.owned(action, asset_id, STAGE):
            bpy.data.actions.remove(action)
    # 新成果取代旧成果后, 基于旧成果构建的绑定与导出集合不再对应碎块动画, 同样放在最后删除
    for root in session.roots:
        asset.discard_rig(root)
    return run_id


def _finish(session, temp, result, tracker, committed, original_frame):
    """收尾: 失败或取消时撤回对碎块的改动并回收新建数据; 无论成败都撤回会话期间的临时改动并回到原来的帧

    session 模拟会话
    temp 会话期间的临时改动快照
    result 对碎块动画与位姿的改动快照
    tracker 创建账本
    committed 是否已经提交
    original_frame 烘焙之前的当前帧
    """
    # 已提交时保留对碎块的改动; 未提交 (失败或取消) 时全部撤回并回收新建的数据
    steps = [] if committed else [result.undo, tracker.rollback]
    steps += [temp.undo, lambda: session.scene.frame_set(original_frame)]
    run_all(steps)


def bake_steps(context, roots, values):
    """烘焙的生成器: 每推进一帧产出一次 Progress; 中途关闭时撤回本次全部改动

    context Blender 上下文
    roots 资产根集合列表
    values 参数值
    返回 生成器, 返回值是 SimulationResult
    """
    started = perf_counter()
    session = build(context, roots, values)
    original_frame = session.scene.frame_current
    temp, result, tracker = WriteLog(), WriteLog(), CreatedData()
    committed = False
    try:
        pending = _prepare(session, temp, result, tracker)
        samples, rehandoffs = yield from _run(session)
        finals, moved = _write_results(session, samples, tracker)
        flights = flight.write(session, tracker, result)
        run_id = _commit(session, finals, pending, moved, flights, result)
        committed = True
    finally:
        _finish(session, temp, result, tracker, committed, original_frame)
    frames = session.frame_end - session.frame_start + 1
    return SimulationResult(
        run_id=run_id,
        frames=frames,
        shards=len(session.participants),
        moved=sum(moved.values()),
        rehandoffs=rehandoffs,
        clamped=session.clamped,
        blocked=session.walls.blocked,
        frozen=sum(
            1
            for item in session.participants
            if item.plan.mode == impulse.FROZEN and not item.plan.events
        ),
        excluded=sum(1 for item in session.participants if _excluded(item)),
        substeps=session.settings["substeps"],
        raised=session.settings["substeps"] > session.settings["requested"],
        suggested=session.suggested,
        capped=session.capped,
        seconds=perf_counter() - started,
    )
