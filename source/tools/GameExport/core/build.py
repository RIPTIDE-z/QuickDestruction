"""构建流程 - 读轨迹, 分类, 划分, 建骨架与蒙皮网格, 写动画, 校验后提交

两阶段提交: 新绑定先建在 QD_Building_ 前缀的临时集合里, 校验全部通过才删除旧导出集合并改成最终名字
任何一步失败都回收本次新建的数据与对场景的改动, 旧导出原样保留
每个阶段产出一次进度, 供可取消的界面任务使用
"""

import traceback
import uuid
from dataclasses import dataclass
from time import perf_counter
from typing import NamedTuple

import bpy

from ....common import asset, shards
from ....common.created_data import CreatedData, WriteLog
from ....common.status import CURRENT, simulation_status
from ....common.translate import tr_report
from ..constants import (
    ACTION_PREFIX,
    BUILDING_PREFIX,
    CONTEXT,
    EXPORT_SUFFIX,
    MESH_SUFFIX,
    RIG_SUFFIX,
    VERIFY_FRAMES,
)
from ..errors import ExportError
from ..settings import BUILD_FIELDS
from . import bones, check, interior, merge, tracks, transfer
from .merge import Entry
from .partition import bone_name, fallback_name, partition

# 没有模拟成果时的错误
NO_SIMULATION_MESSAGE = 'Asset "{name}" has no current simulation; bake it first'
# 碎块有父物体时的错误: 导出把碎块的世界姿态烘进骨架, 父级会让世界姿态与动画不一致
PARENTED_MESSAGE = 'Shard "{name}" has a parent; unparent it first'
# 缺少静止姿态时的错误
NO_REST_MESSAGE = 'Shard "{name}" has no rest pose; apply physics again'
# 校验失败时的错误
VERIFY_FAILED_MESSAGE = "The rig failed its checks: {detail}"

# 一个资产构建的总阶段数: 读取分类, 骨架, 网格, 动画, 校验, 提交
# 骨架, 网格与动画在同一步里建成, 所以进度依次是 1, 4, 5, 6
STAGES = 6


class Progress(NamedTuple):
    """构建进度: 已完成的阶段数与总阶段数"""

    # 已完成的阶段数
    done: int
    # 总阶段数
    total: int


@dataclass(frozen=True)
class RigResult:
    """一个资产的构建结果"""

    # 资产根集合名
    asset: str
    # 绑定标识
    rig_id: str
    # 会动的碎块数
    moving: int
    # 不动的碎块数
    static: int
    # 骨架数
    armatures: int
    # 蒙皮网格数
    meshes: int
    # 骨骼总数, 含每个骨架的 root
    bones: int
    # 检查发现的问题
    issues: tuple
    # 耗时, 秒
    seconds: float


@dataclass(frozen=True)
class Pending:
    """已构建并校验通过, 等待提交的绑定"""

    # 资产根集合
    root: object
    # 本次新建的临时集合
    collection: object
    # 旧导出集合, 没有时为 None
    old: object
    # 构建结果
    result: RigResult


def build_digest(values):
    """构建参数的摘要: 记在绑定记录里, 与面板比较得出"设置已改动"; 只含改变产物的字段

    values 参数值或参数组, 按字段名读取
    返回 [[字段名, 值的字符串], ...]
    """
    return [[key, repr(getattr(values, key))] for key in BUILD_FIELDS]


def _label(root):
    """导出物体与集合用的名字: 源物体名, 源已删除时用根集合名

    root 资产根集合
    返回 名字
    """
    return root.get(shards.SOURCE_MARK) or root.name


def _numbered(base, index, count):
    """只有一个时用原名, 多个时加两位序号

    base 基础名
    index 从 0 开始的序号
    count 总个数
    返回 名字
    """
    return base if count == 1 else f"{base}_{index + 1:02d}"


def _read(root):
    """读出构建输入, 并逐块校验

    root 资产根集合
    返回 (模拟记录, 碎块列表, 静止矩阵列表, {碎块名: 轨迹}); 不满足时抛 ExportError
    """
    result = asset.sim_record(root)
    if result is None or asset.sim_action(root) is None:
        raise ExportError(NO_SIMULATION_MESSAGE, name=root.name)
    items = asset.shards_of(root)
    rests = []
    for shard in items:
        # 带父物体的碎块世界姿态含父级变换, 与动画里的世界轨迹不一致
        if shard.parent is not None:
            raise ExportError(PARENTED_MESSAGE, name=shard.name)
        rest = asset.rest_matrix(shard)
        if rest is None:
            raise ExportError(NO_REST_MESSAGE, name=shard.name)
        rests.append(rest)
    return result, items, rests, tracks.read_tracks(items)


def _bone_names(items, moving):
    """给会动的碎块起骨骼名: 路径名, 路径过长或重名时退回顺序号

    items 碎块列表
    moving 会动的碎块在 items 里的序号
    返回 与 moving 同序的骨骼名列表
    """
    names, used = [], set()
    for number, index in enumerate(moving):
        name = bone_name(shards.path_of(items[index]))
        # 路径名超过字节上限, 或与已用的重名时, 用顺序号保证唯一
        if name is None or name in used:
            name = fallback_name(number)
        used.add(name)
        names.append(name)
    return names


def _make_collection(tracker, name):
    """建临时集合并链接进场景

    tracker 创建账本
    name 集合名
    返回 集合
    """
    collection = tracker.track("collections", bpy.data.collections.new(name))
    bpy.context.scene.collection.children.link(collection)
    return collection


def _segment_entries(items, rests, names, moving, segment, statics):
    """一段网格要合并的碎块: 本段会动的碎块各归自己的骨骼, statics 是要并入这一段的不动碎块

    items 碎块列表
    rests 静止矩阵列表, 与 items 同序
    names 会动的碎块的骨骼名, 与 moving 同序
    moving 会动的碎块在 items 里的序号
    segment 本段包含的会动碎块在 moving 里的位置
    statics 并入本段的不动碎块在 items 里的序号
    返回 Entry 列表
    """
    entries = [
        Entry(items[moving[position]], rests[moving[position]], names[position], moving[position])
        for position in segment
    ]
    # 不动的碎块全部归 root 顶点组
    entries += [Entry(items[index], rests[index], "root", index) for index in statics]
    return entries


def _build_geometry(context, tracker, collection, label, plan, inputs, values, asset_id):
    """按划分建骨架, 蒙皮网格与动画

    context Blender 上下文
    tracker 创建账本
    collection 临时集合, 新物体都链接进它
    label 物体名用的主干, 含临时前缀
    plan 划分结果, Armature 列表
    inputs (碎块, 静止矩阵, 骨骼名, 会动序号, 不动序号, 轨迹) 的元组
    values 参数值
    asset_id 资产标识, 写到动作上
    返回 (骨架列表, 网格列表, {骨骼名: 碎块名}, 每个骨架包含的会动碎块位置列表)
    """
    items, rests, names, moving, still, tracked = inputs
    armature_objects, mesh_objects, mapping, per_armature = [], [], {}, []
    for armature_index, armature in enumerate(plan):
        members = [position for segment in armature.segments for position in segment]
        armature_name = _numbered(label + RIG_SUFFIX, armature_index, len(plan))
        # 骨骼头部放在碎块的静止质心
        specs = [
            (
                names[position],
                rests[moving[position]].translation,
                bones.bone_length(items[moving[position]]),
            )
            for position in members
        ]
        lengths = [spec[2] for spec in specs]
        obj = bones.make_armature(
            context, tracker, armature_name, collection, specs, max(lengths, default=1.0)
        )
        armature_objects.append(obj)
        per_armature.append(members)
        for position in members:
            mapping[names[position]] = items[moving[position]].name
        for segment_index, segment in enumerate(armature.segments):
            # 不动的碎块并入第一个骨架的第一段
            statics = still if armature_index == 0 and segment_index == 0 else ()
            entries = _segment_entries(items, rests, names, moving, segment, statics)
            mesh_name = _numbered(label + MESH_SUFFIX, segment_index, len(armature.segments))
            # 多个骨架时网格名带骨架序号与段序号, 避免不同骨架的网格重名
            if len(plan) > 1:
                mesh_name = f"{label}{MESH_SUFFIX}_{armature_index + 1:02d}_{segment_index + 1:02d}"
            mesh = merge.build_mesh(context, tracker, mesh_name, collection, entries, obj)
            if values.interior_uv == "BOX":
                interior.box_project(mesh.data, values.interior_uv_scale)
            mesh_objects.append(mesh)
    # 动画在全部骨架建好之后写: 写动画不再需要进入编辑模式
    for obj, members in zip(armature_objects, per_armature, strict=True):
        animated = [
            (names[position], tracked[items[moving[position]].name], rests[moving[position]])
            for position in members
        ]
        action = transfer.write_action(tracker, obj, animated)
        asset.claim(action, asset_id, asset.RIG_STAGE)
    return armature_objects, mesh_objects, mapping, per_armature


def _last_start(items, rests, moving, tracked):
    """全部会动的碎块里最晚的起动帧

    items 碎块列表
    rests 静止矩阵列表
    moving 会动的碎块序号
    tracked {碎块名: 轨迹}
    返回 帧号; 都没有时为 None
    """
    starts = [tracks.motion_start(tracked[items[i].name], rests[i]) for i in moving]
    starts = [start for start in starts if start is not None]
    return max(starts) if starts else None


def _claim_all(collection, asset_id):
    """把导出集合里的物体标记为本资产的绑定阶段; 集合不能标记, 带资产标识的集合会被当成资产根

    collection 导出集合
    asset_id 资产标识
    """
    for obj in collection.objects:
        asset.claim(obj, asset_id, asset.RIG_STAGE)


def _restore_view(context, selection, active, frame):
    """还原选择, 活动物体与当前帧

    context Blender 上下文
    selection 要恢复选中的物体
    active 要恢复的活动物体
    frame 要恢复的帧
    """
    for obj in context.view_layer.objects:
        obj.select_set(obj in selection)
    context.view_layer.objects.active = active
    context.scene.frame_set(frame)


def _build_one(context, root, values, tracker, log):
    """一个资产的完整构建, 生成器: 每个阶段产出一次 Progress

    context Blender 上下文
    root 资产根集合
    values 参数值
    tracker 创建账本
    log 写前快照
    返回 Pending, 校验通过但还没有提交
    """
    started = perf_counter()
    scene = context.scene
    # 模拟成果必须是当前的: 过期的成果动画与碎块现状对不上
    if simulation_status(root, scene).state != CURRENT:
        raise ExportError(NO_SIMULATION_MESSAGE, name=root.name)
    result, items, rests, tracked = _read(root)
    moving, still = tracks.classify(
        items, rests, tracked, values.static_distance, values.static_angle
    )
    names = _bone_names(items, moving)
    points = [tuple(rests[index].translation) for index in moving]
    plan = partition(points, values.max_bones_total, values.max_bones_per_mesh)
    yield Progress(1, STAGES)
    label = _label(root)
    asset_id = shards.asset_of(root)
    building = _make_collection(tracker, BUILDING_PREFIX + label)
    inputs = (items, rests, names, moving, still, tracked)
    # 物体先用带前缀的临时名, 提交时旧导出已删, 再改回最终名字, 避免与旧导出重名得到 .001 后缀
    armatures, meshes, mapping, _members = _build_geometry(
        context, tracker, building, BUILDING_PREFIX + label, plan, inputs, values, asset_id
    )
    yield Progress(4, STAGES)
    rig_id = uuid.uuid4().hex
    record = {
        "id": rig_id,
        "run": result["run"],
        "physics_rev": result["physics_rev"],
        "digest": build_digest(values),
        "frames": [result["frame_start"], result["frame_end"]],
        "moving": len(moving),
        "static": len(still),
        "armatures": len(armatures),
        "meshes": len(meshes),
        "bones": sum(len(obj.data.bones) for obj in armatures),
        "last_start": _last_start(items, rests, moving, tracked),
        "map": mapping,
        # 蒙皮误差的容差: 不动阈值允许的位移加转角在碎块尺度上的位移
        "tolerance": values.static_distance
        + values.static_angle * max((max(shard.dimensions) for shard in items), default=0.0),
    }
    # 校验函数经资产记录找到导出集合, 所以先让记录暂时指向临时集合, 失败时由写前快照恢复
    previous = (asset.rig_record(root), asset.rig_collection(root))
    asset.set_rig_result(root, record, building)
    log.add(lambda: _restore_rig(root, previous))
    frames = check.frame_samples(result["frame_start"], result["frame_end"], VERIFY_FRAMES)
    error, frame = check.verify_skin(context, root, frames)
    if error > check.skin_tolerance(record):
        detail = tr_report(CONTEXT, check.SKIN_MISMATCH_MESSAGE, error=error, frame=frame)
        raise ExportError(VERIFY_FAILED_MESSAGE, detail=detail)
    yield Progress(5, STAGES)
    # 逐帧核对刚做过, 这里只做其余的检查
    issues = check.check_rig(context, root, values, sample_frames=0)
    errors = [issue for issue in issues if issue.level == check.ERROR]
    if errors:
        detail = check.issue_text(errors[0])
        raise ExportError(VERIFY_FAILED_MESSAGE, detail=detail)
    _claim_all(building, asset_id)
    yield Progress(6, STAGES)
    rig = RigResult(
        asset=root.name,
        rig_id=rig_id,
        moving=len(moving),
        static=len(still),
        armatures=len(armatures),
        meshes=len(meshes),
        bones=record["bones"],
        issues=issues,
        seconds=perf_counter() - started,
    )
    return Pending(root, building, previous[1], rig)


def _restore_rig(root, previous):
    """把资产的绑定记录与导出集合引用恢复成构建前的样子

    root 资产根集合
    previous (旧记录, 旧导出集合), 旧记录为 None 表示原来没有绑定
    """
    record, collection = previous
    if record is None:
        asset.clear_rig_result(root)
    else:
        asset.set_rig_result(root, record, collection)


def _stale_items(collection):
    """导出集合连同其中物体, 物体数据与动作, 供一次批量删除

    collection 要删除的导出集合
    返回 数据块列表
    """
    stale = []
    for obj in collection.objects:
        animation = obj.animation_data.action if obj.animation_data else None
        stale.extend(item for item in (obj, obj.data, animation) if item is not None)
    stale.append(collection)
    return stale


def _commit(pending):
    """全部资产都校验通过后提交: 删除旧导出, 把临时名改成最终名字

    pending Pending 列表
    """
    stale = []
    for item in pending:
        # 旧导出与新建的临时集合不是同一个才需要删除
        if item.old is not None and item.old != item.collection:
            stale.extend(_stale_items(item.old))
    # 先删旧的再改名: 否则新旧同名, 改名会得到 .001 后缀
    if stale:
        bpy.data.batch_remove(stale)
    for item in pending:
        collection = item.collection
        for obj in collection.objects:
            final = obj.name.removeprefix(BUILDING_PREFIX)
            obj.name = final
            obj.data.name = final
            if obj.animation_data and obj.animation_data.action:
                obj.animation_data.action.name = ACTION_PREFIX + final
        collection.name = _label(item.root) + EXPORT_SUFFIX


def build_steps(context, roots, values):
    """构建的生成器; 中途失败或关闭时撤回本次全部改动, 旧导出保留

    context Blender 上下文
    roots 资产根集合列表
    values 参数值
    返回 生成器, 每个阶段产出 Progress, 返回值是 [RigResult, ...]
    """
    asset.validate(roots)
    view = context.view_layer
    # 记下现场: 构建要改选择, 活动物体与当前帧, 结束时无论成败都还原
    selection = [obj for obj in view.objects if obj.select_get()]
    active, frame = view.objects.active, context.scene.frame_current
    tracker, log = CreatedData(), WriteLog()
    committed = False
    try:
        pending = []
        # 先逐个构建并校验, 全部通过后才统一提交: 多资产时一个失败不能让另一个丢掉旧导出
        for root in roots:
            pending.append((yield from _build_one(context, root, values, tracker, log)))
        _commit(pending)
        committed = True
    finally:
        # 失败或被取消时撤回对记录的改动并回收新建的数据; 成功时只还原现场
        steps = [] if committed else [log.undo, tracker.rollback]
        steps.append(lambda: _restore_view(context, selection, active, frame))
        for step in steps:
            try:
                step()
            except Exception:
                # 清理失败不能覆盖原异常, 也不能阻止其余收尾
                traceback.print_exc()
    return [item.result for item in pending]
