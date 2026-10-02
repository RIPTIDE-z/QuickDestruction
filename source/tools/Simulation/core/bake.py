"""烘焙流程 - 写入会话设置与交接关键帧, 逐帧推进并采样, 写成烘焙动作, 校验后提交

两阶段提交: 推进期间碎块使用临时动作, 旧成果原样保留; 全部成功后才换上新动作并删除旧成果
两本写前快照: temp 记录会话期间的临时改动 (世界设置, 世界成员, 刚体开关), 无论成败结束时都撤回,
撤回时参与的碎块也随之移出刚体世界; result 记录对碎块动画与位姿的改动, 只在失败或取消时撤回
"""

import traceback
import uuid
from dataclasses import dataclass
from time import perf_counter
from typing import NamedTuple

import bpy

from ....common import asset, impulse, rigidbody, shards
from ....common.created_data import CreatedData, WriteLog
from ..constants import PENDING_PREFIX, PENDING_STAGE, RESULT_PREFIX, STAGE, STATIC_EPSILON
from . import keys
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
    # 耗时, 秒
    seconds: float


def _set_world(session, temp):
    """会话期间的世界设置: 帧范围与求解设置; 点缓存的起止帧按不会被互相钳制的顺序写入"""
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
    """刚体世界里不参与本次会话的资产碎块临时移出, 结束时再放回"""
    taking = {participant.shard.name for participant in session.participants}
    members = session.world.collection.objects
    for obj in list(members):
        if shards.asset_of(obj) is not None and obj.name not in taking:
            members.unlink(obj)
            temp.add(lambda obj=obj: members.link(obj))


def _static(participant):
    """整段都不会动的碎块: 锚定的, 以及没有任何起动事件的冻结碎块

    它们按被动刚体参与: 与动画控制的刚体一样不可撞动, 但不必每个子步插值与激活, 也不需要动画曲线,
    碎块多而被波及的少时能省下大部分推进开销
    """
    mode = participant.plan.mode
    return mode == impulse.ANCHORED or (mode == impulse.FROZEN and not participant.plan.events)


def _set_body(participant, temp):
    """按激活方式设置刚体: 不动的为被动; 冻结先受动画控制; 休眠从休眠开始; 自由直接参与"""
    body = participant.shard.rigid_body
    mode = participant.plan.mode
    temp.set(body, "enabled", True)
    temp.set(body, "type", "PASSIVE" if _static(participant) else "ACTIVE")
    temp.set(body, "kinematic", mode == impulse.FROZEN)
    sleeping = mode == impulse.SLEEPING
    temp.set(body, "use_deactivation", sleeping)
    temp.set(body, "use_start_deactivated", sleeping)


def _remember_animation(shard, result):
    """记下碎块原来的动画引用, 失败时恢复; 原来没有动画数据时撤回就是清掉它"""
    data = shard.animation_data
    if data is None:
        result.add(shard.animation_data_clear)
        return
    action, slot = data.action, data.action_slot
    # 先登记槽位再登记动作: 撤回逆序执行, 动作先恢复, 槽位才能属于它
    result.add(lambda: setattr(data, "action_slot", slot) if slot is not None else None)
    result.add(lambda: setattr(data, "action", action))


def _prepare_shard(participant, pending, session, result):
    """碎块回到静止姿态, 改用四元数旋转, 绑定临时动作并写下起始帧与预先确定的首次交接"""
    shard = participant.shard
    _remember_animation(shard, result)
    result.set(shard, "rotation_mode", "QUATERNION")
    result.set(shard, "matrix_world", participant.rest.copy())
    if _static(participant):
        shard.animation_data_create().action = None
        return
    location, rotation, _scale = participant.rest.decompose()
    frozen = participant.plan.mode == impulse.FROZEN
    curves = keys.bind(shard, pending, kinematic=True)
    keys.key_kinematic(curves, session.frame_start, frozen)
    keys.key_pose(curves, session.frame_start, location, rotation)
    events = participant.plan.events
    if frozen and events:
        keys.key_handoff(curves, events[0].frame, location, rotation, events[0], session.dt)
    participant.curves = curves


def _prepare(session, temp, result, tracker):
    """写入会话期间的全部设置, 返回 {资产标识: 临时动作}"""
    _set_world(session, temp)
    _exclude_others(session, temp)
    pending = {}
    for root in session.roots:
        action = tracker.track("actions", bpy.data.actions.new(PENDING_PREFIX + root.name))
        asset.claim(action, shards.asset_of(root), PENDING_STAGE)
        pending[shards.asset_of(root)] = action
    for participant in session.participants:
        _set_body(participant, temp)
        _prepare_shard(participant, pending[participant.asset_id], session, result)
    ordered = [participant.shard for participant in session.participants]
    rigidbody.link(session.world, ordered)
    temp.add(lambda: rigidbody.unlink(session.world, ordered))
    return pending


def _online_events(session):
    """推进途中才写的交接: 冻结碎块的第二次及以后的事件, 其他激活方式的全部事件; {帧: [(碎块, 事件)]}"""
    schedule = {}
    for participant in session.participants:
        events = participant.plan.events
        if _static(participant):
            continue
        if participant.plan.mode == impulse.FROZEN:
            events = events[1:]
        for event in events:
            schedule.setdefault(event.frame, []).append((participant, event))
    return schedule


def _sample(samples, participant):
    """记下碎块当前的位置与朝向, 四元数与上一帧保持同一符号"""
    location, rotation, _scale = participant.shard.matrix_world.decompose()
    track = samples[participant.shard.name]
    if track[1] and rotation.dot(track[1][-1]) < 0.0:
        rotation = -rotation
    track[0].append(location)
    track[1].append(rotation)


def _run(session):
    """逐帧推进: 先求值这一帧, 再插入到期的再交接, 最后采样; 每帧产出一次进度, 返回 (采样, 再交接次数)"""
    scene = session.scene
    schedule = _online_events(session)
    sampled = [participant for participant in session.participants if not _static(participant)]
    samples = {participant.shard.name: ([], []) for participant in sampled}
    total = session.frame_end - session.frame_start + 1
    rehandoffs = 0
    for index, frame in enumerate(range(session.frame_start, session.frame_end + 1)):
        scene.frame_set(frame)
        for participant, event in schedule.get(frame, ()):
            location, rotation, _scale = participant.shard.matrix_world.decompose()
            keys.key_handoff(participant.curves, frame, location, rotation, event, session.dt)
            rehandoffs += 1
        for participant in sampled:
            _sample(samples, participant)
        yield Progress(index + 1, total)
    return samples, rehandoffs


def _moved(track):
    """整段采样里是否离开过第一帧的位姿"""
    locations, rotations = track
    first_location, first_rotation = locations[0], rotations[0]
    return any(
        (location - first_location).length > STATIC_EPSILON
        or first_rotation.rotation_difference(rotation).angle > STATIC_EPSILON
        for location, rotation in zip(locations, rotations, strict=True)
    )


def _write_results(session, samples, tracker):
    """为每个资产建烘焙动作, 动过的碎块写逐帧关键帧, 没动的碎块不带动画停在静止姿态

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
        if track is not None and _moved(track):
            keys.write_track(shard, finals[participant.asset_id], frames, *track)
            moved[participant.asset_id] += 1
            continue
        shard.animation_data_create().action = None
        shard.matrix_world = participant.rest
    return finals, moved


def _remember_records(session, result):
    """提交前记下各资产的模拟记录与场景的会话记录, 提交中途失败时恢复"""
    runs = asset.runs(session.scene)
    result.add(lambda: asset.set_runs(session.scene, runs))
    for root in session.roots:
        record, action = asset.sim_record(root), asset.sim_action(root)
        if record is None:
            result.add(lambda root=root: asset.clear_sim_result(root))
        else:
            result.add(lambda r=root, rec=record, act=action: asset.set_sim_result(r, rec, act))


def _commit(session, finals, pending, moved, result):
    """写入会话与资产的模拟记录, 删除临时动作, 最后删除各资产的旧成果动作; 返回会话标识

    删除动作放在最后: 之前任一步失败都能按写前快照恢复, 旧成果还在
    """
    _remember_records(session, result)
    run_id = uuid.uuid4().hex
    revisions = {}
    for root in session.roots:
        revisions[shards.asset_of(root)] = asset.physics_record(root)["rev"]
    old_actions = []
    for root in session.roots:
        asset_id = shards.asset_of(root)
        old_actions.append((asset.sim_action(root), asset_id))
        record = {
            "run": run_id,
            "physics_rev": revisions[asset_id],
            "frame_start": session.frame_start,
            "frame_end": session.frame_end,
            "moved": moved[asset_id],
        }
        asset.set_sim_result(root, record, finals[asset_id])
    run = {"assets": revisions, **session.signatures, "settings": session.settings}
    asset.add_run(session.scene, run_id, run)
    for action in pending.values():
        bpy.data.actions.remove(action)
    for action, asset_id in old_actions:
        stale = action is not None and action not in finals.values()
        if stale and asset.owned(action, asset_id, STAGE):
            bpy.data.actions.remove(action)
    return run_id


def _finish(session, temp, result, tracker, committed, original_frame):
    """收尾: 失败或取消时撤回对碎块的改动并回收新建数据; 无论成败都撤回会话期间的临时改动并回到原来的帧"""
    steps = [] if committed else [result.undo, tracker.rollback]
    steps += [temp.undo, lambda: session.scene.frame_set(original_frame)]
    for step in steps:
        try:
            step()
        except Exception:
            # 清理失败不能覆盖原异常, 也不能阻止其余收尾
            traceback.print_exc()


def bake_steps(context, roots, values):
    """烘焙的生成器: 每推进一帧产出一次 Progress, 返回 SimulationResult; 中途关闭时撤回本次全部改动"""
    started = perf_counter()
    session = build(context, roots, values)
    original_frame = session.scene.frame_current
    temp, result, tracker = WriteLog(), WriteLog(), CreatedData()
    committed = False
    try:
        pending = _prepare(session, temp, result, tracker)
        samples, rehandoffs = yield from _run(session)
        finals, moved = _write_results(session, samples, tracker)
        run_id = _commit(session, finals, pending, moved, result)
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
        seconds=perf_counter() - started,
    )
