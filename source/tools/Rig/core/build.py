"""构建流程 - 读轨迹, 分类, 划分, 建骨架与蒙皮网格, 写动画, 校验后提交

两阶段提交: 新绑定先建在 QD_Building_ 前缀的临时集合里, 校验全部通过才删除旧导出集合并改成最终名字
绑定方式为原物体切换时, 第一个骨架另带原物体骨骼与原物体网格, 全部碎块骨骼带缩放开关, 见 swap 模块
导出集合与绑定记录只由本模块提交; 游戏导出只读它们
任何一步失败都回收本次新建的数据与对场景的改动, 旧导出原样保留
每个阶段产出一次进度, 供可取消的界面任务使用
"""

import uuid
from dataclasses import dataclass
from time import perf_counter
from typing import NamedTuple

import bpy

from ....common import asset, shards
from ....common.created_data import CreatedData, WriteLog, run_all
from ....common.names import material_names
from ....common.status import CURRENT, simulation_status
from ....common.translate import tr_report
from ..constants import (
    ACTION_PREFIX,
    BUILDING_PREFIX,
    CONTEXT,
    EXPORT_SUFFIX,
    IDLE_SUFFIX,
    INTACT_SUFFIX,
    MESH_SUFFIX,
    RIG_SUFFIX,
    VERIFY_FRAMES,
)
from ..errors import RigError
from ..settings import BUILD_FIELDS, SWAP
from . import bones, check, interior, merge, seamless, swap, tracks, transfer
from .merge import Entry
from .partition import ROOT_BONE, bone_name, partition

# 没有模拟成果时的错误
NO_SIMULATION_MESSAGE = 'Asset "{name}" has no current simulation; bake it first'
# 碎块有父物体时的错误: 导出把碎块的世界姿态烘进骨架, 父级会让世界姿态与动画不一致
PARENTED_MESSAGE = 'Shard "{name}" has a parent; unparent it first'
# 模拟成果里没有锚定记录时的错误: 旧版本烘焙的成果
NO_ANCHOR_MESSAGE = "The simulation has no anchor record; bake it again to merge anchored shards"
# 缺少静止姿态时的错误
NO_REST_MESSAGE = 'Shard "{name}" has no rest pose; apply physics again'
# 校验失败时的错误
VERIFY_FAILED_MESSAGE = "The rig failed its checks: {detail}"
# 骨架骨骼上限放不下固定骨骼加一块碎块时的错误
BONE_ROOM_MESSAGE = "Bones per Skeleton must be above {count} for this rig type"

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
    # 合并成静止整体的碎块数
    merged: int
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


class SwapPlan(NamedTuple):
    """原物体切换的构建输入"""

    # 源物体, 做成原物体网格
    source: object
    # 模拟起始帧, 缩放开关的第一个关键帧
    start: int
    # 原物体的隐藏帧
    hide: int
    # 碎块的出现帧
    show: int
    # 待机动作的长度, 帧
    length: int
    # 原物体与不动碎块骨骼的头部位置, 世界坐标
    pivot: object
    # 隐藏时的缩放
    hidden: float


class Inputs(NamedTuple):
    """一个资产的构建输入, 读取与分类之后不再改变"""

    # 碎块列表, 按路径排序, 序号即来源属性里的碎块序号
    items: list
    # 静止世界矩阵, 与 items 同序
    rests: list
    # 有骨骼的碎块的骨骼名, 与 bound 同序
    names: list
    # 有骨骼的碎块在 items 里的序号, 按路径顺序; 绑定静止碎块时是全部碎块, 否则只有会动的
    bound: list
    # 没有骨骼的碎块在 items 里的序号: 不绑定静止碎块时, 不动的碎块并入 root 或 still
    loose: list
    # 带动画关键帧的碎块在 items 里的序号: 只有会动的碎块, 绑了骨骼的不动碎块停在静止姿态
    animated: frozenset
    # 合并成静止整体的碎块在 items 里的序号: 设计上静止的碎块, 没有骨骼也不逐块进蒙皮网格
    base: list
    # 静止整体的网格, 世界坐标, 与未破碎的源物体一致; 没有合并时为 None
    base_mesh: object
    # {碎块名: 轨迹}
    tracked: dict
    # 原物体切换的输入; 仅碎块时为 None
    swap: SwapPlan | None


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
    返回 (模拟记录, 碎块列表, 静止矩阵列表, {碎块名: 轨迹}); 不满足时抛 RigError
    """
    result = asset.sim_record(root)
    if result is None or asset.sim_action(root) is None:
        raise RigError(NO_SIMULATION_MESSAGE, name=root.name)
    items = asset.shards_of(root)
    rests = []
    for shard in items:
        # 带父物体的碎块世界姿态含父级变换, 与动画里的世界轨迹不一致
        if shard.parent is not None:
            raise RigError(PARENTED_MESSAGE, name=shard.name)
        rest = asset.rest_matrix(shard)
        if rest is None:
            raise RigError(NO_REST_MESSAGE, name=shard.name)
        rests.append(rest)
    return result, items, rests, tracks.read_tracks(items)


def _bone_names(bound):
    """给有骨骼的碎块起骨骼名: 按碎块路径的顺序平铺编号, b_001, b_002, ...

    碎块列表已按路径排序, 所以编号确定; 绑定静止碎块时每块碎块都有骨骼, 编号与碎块一一对应,
    不随模拟里哪些碎块会动而变化; 骨骼到碎块的映射另记在绑定记录里
    bound 有骨骼的碎块在碎块列表里的序号
    返回 与 bound 同序的骨骼名列表
    """
    return [bone_name(number) for number in range(len(bound))]


def _make_collection(context, tracker, name):
    """建临时集合并链接进场景

    context Blender 上下文, 提供场景
    tracker 创建账本
    name 集合名
    返回 集合
    """
    collection = tracker.track("collections", bpy.data.collections.new(name))
    context.scene.collection.children.link(collection)
    return collection


def _segment_entries(inputs, segment, statics, still_group, with_base):
    """一段网格要合并的碎块: 本段有骨骼的碎块各归自己的骨骼, statics 是要并入这一段的无骨骼碎块

    inputs 构建输入
    segment 本段包含的碎块在 bound 里的位置
    statics 并入本段的无骨骼碎块在 items 里的序号
    still_group 无骨骼碎块与静止整体归属的顶点组名
    with_base 为真时本段还带上静止整体
    返回 Entry 列表
    """
    items, rests, names, bound = inputs.items, inputs.rests, inputs.names, inputs.bound
    entries = [
        Entry(items[bound[position]], rests[bound[position]], names[position], bound[position])
        for position in segment
    ]
    # 无骨骼的碎块全部归同一个顶点组: 仅碎块时是 root, 原物体切换时是能被隐藏的 still
    entries += [Entry(items[index], rests[index], still_group, index) for index in statics]
    # 静止整体已经在世界坐标, 不对应某一块碎块, 序号记 -1
    if with_base and inputs.base_mesh is not None:
        entries.append(Entry(None, None, still_group, -1, inputs.base_mesh))
    return entries


def _build_geometry(context, tracker, collection, label, plan, inputs, values, asset_id):
    """按划分建骨架, 蒙皮网格与动画

    context Blender 上下文
    tracker 创建账本
    collection 临时集合, 新物体都链接进它
    label 物体名用的主干, 含临时前缀
    plan 划分结果, Armature 列表
    inputs 构建输入
    values 参数值
    asset_id 资产标识, 写到动作上
    返回 (骨架列表, 网格列表, {骨骼名: 碎块名}, 每个骨架包含的碎块在 bound 里的位置列表)
    """
    items, rests, names, bound, loose, _animated, base, _base_mesh, _tracked, plan_swap = inputs
    still_group = ROOT_BONE if plan_swap is None else swap.STILL_BONE
    armature_objects, mesh_objects, mapping, per_armature = [], [], {}, []
    for armature_index, armature in enumerate(plan):
        members = [position for segment in armature.segments for position in segment]
        armature_name = _numbered(label + RIG_SUFFIX, armature_index, len(plan))
        # 骨骼头部放在碎块的静止质心
        specs = [
            (
                names[position],
                rests[bound[position]].translation,
                bones.bone_length(items[bound[position]]),
            )
            for position in members
        ]
        lengths = [spec[2] for spec in specs]
        # 原物体切换: 第一个骨架另带原物体骨骼, 有无骨骼的不动碎块或静止整体时再带 still 骨骼, 头部都在资产中心
        if plan_swap is not None and armature_index == 0:
            length = max(lengths, default=1.0)
            specs.append((swap.INTACT_BONE, plan_swap.pivot, length))
            if loose or base:
                specs.append((swap.STILL_BONE, plan_swap.pivot, length))
        obj = bones.make_armature(
            context, tracker, armature_name, collection, specs, max(lengths, default=1.0)
        )
        armature_objects.append(obj)
        per_armature.append(members)
        for position in members:
            mapping[names[position]] = items[bound[position]].name
        for segment_index, segment in enumerate(armature.segments):
            # 无骨骼的碎块与静止整体并入第一个骨架的第一段
            first = armature_index == 0 and segment_index == 0
            statics = loose if first else ()
            entries = _segment_entries(inputs, segment, statics, still_group, first)
            mesh_name = _numbered(label + MESH_SUFFIX, segment_index, len(armature.segments))
            # 多个骨架时网格名带骨架序号与段序号, 避免不同骨架的网格重名
            if len(plan) > 1:
                mesh_name = f"{label}{MESH_SUFFIX}_{armature_index + 1:02d}_{segment_index + 1:02d}"
            mesh = merge.build_mesh(context, tracker, mesh_name, collection, entries, obj)
            if values.interior_uv == "BOX":
                interior.box_project(mesh.data, values.interior_uv_scale)
            mesh_objects.append(mesh)
        # 原物体网格单独一段, 归第一个骨架, 只引用原物体骨骼
        if plan_swap is not None and armature_index == 0:
            intact = swap.build_intact(
                context, tracker, label + INTACT_SUFFIX, collection, plan_swap.source
            )
            merge.bind(intact, obj)
            mesh_objects.append(intact)
    # 动画在全部骨架建好之后写: 写动画不再需要进入编辑模式
    for index, (obj, members) in enumerate(zip(armature_objects, per_armature, strict=True)):
        _write_animations(tracker, obj, index, members, inputs, asset_id)
    return armature_objects, mesh_objects, mapping, per_armature


def _write_animations(tracker, obj, index, members, inputs, asset_id):
    """给一个骨架写动作: 仅碎块时只有破碎动作; 原物体切换时先写待机动作

    写动作会把它设为骨架的当前动作, 所以待机先写, 最后写的破碎动作才是当前动作
    tracker 创建账本
    obj 骨架物体
    index 骨架序号
    members 这个骨架里的碎块在 bound 里的位置
    inputs 构建输入
    asset_id 资产标识, 写到动作上
    """
    plan_swap = inputs.swap
    # 这个骨架里每块有骨骼的碎块: (骨骼名, 碎块序号)
    bones_here = [(inputs.names[position], inputs.bound[position]) for position in members]
    bone_names = [name for name, _index in bones_here]
    # 只有会动的碎块写位置与旋转关键帧; 绑了骨骼的不动碎块没有关键帧, 一直停在静止姿态, 只参与缩放开关
    moving_here = [(name, index) for name, index in bones_here if index in inputs.animated]
    if plan_swap is not None:
        still_poses = [
            (
                name,
                swap.idle_track(inputs.rests[i], plan_swap.start, plan_swap.length),
                inputs.rests[i],
            )
            for name, i in moving_here
        ]
        toggles = _toggles(plan_swap, bone_names, index, [*inputs.loose, *inputs.base], idle=True)
        idle = transfer.write_action(tracker, obj, still_poses, toggles, IDLE_SUFFIX)
        asset.claim(idle, asset_id, asset.RIG_STAGE)
        asset.set_idle_action(obj, idle)
    animated = [
        (name, inputs.tracked[inputs.items[i].name], inputs.rests[i]) for name, i in moving_here
    ]
    toggles = _toggles(plan_swap, bone_names, index, [*inputs.loose, *inputs.base])
    action = transfer.write_action(tracker, obj, animated, toggles)
    asset.claim(action, asset_id, asset.RIG_STAGE)


def _toggles(plan_swap, bone_names, armature_index, still, idle=False):
    """一个骨架上的缩放开关

    破碎动作: 碎块骨骼在出现帧之前是隐藏缩放, 原物体骨骼从隐藏帧起缩到隐藏缩放
    待机动作: 原物体骨骼一直是 1, 碎块骨骼一直是隐藏缩放
    plan_swap SwapPlan; 仅碎块时为 None, 此时没有开关
    bone_names 这个骨架里全部碎块的骨骼名, 含绑了骨骼的不动碎块
    armature_index 骨架序号, 原物体与 still 骨骼只在第一个骨架里
    still 没有骨骼的不动碎块的序号列表, 为空时没有 still 骨骼
    idle 为真时给出待机动作的开关
    返回 [(骨骼名, [(帧, 缩放), ...]), ...]
    """
    if plan_swap is None:
        return []
    start, scale = plan_swap.start, plan_swap.hidden

    def keys(frame, visible):
        """一根骨骼的缩放关键帧; visible 为真表示它在开关帧之前显示 (原物体)"""
        if idle:
            return swap.idle_keys(start, plan_swap.length, visible, scale)
        return swap.toggle_keys(start, frame, visible, scale)

    # 出现帧之前要隐藏的骨骼: 本骨架全部有骨骼的碎块, 第一个骨架再加上无骨骼不动碎块的 still
    hidden = list(bone_names)
    toggles = []
    # 原物体与 still 骨骼只建在第一个骨架里
    if armature_index == 0:
        toggles.append((swap.INTACT_BONE, keys(plan_swap.hide, True)))
        if still:
            hidden.append(swap.STILL_BONE)
    toggles += [(name, keys(plan_swap.show, False)) for name in hidden]
    return toggles


def _starts(items, rests, moving, tracked):
    """全部会动的碎块的起动帧

    items 碎块列表
    rests 静止矩阵列表
    moving 会动的碎块序号
    tracked {碎块名: 轨迹}
    返回 帧号列表, 只旋转不平移的碎块没有起动帧, 不在其中
    """
    starts = [tracks.motion_start(tracked[items[i].name], rests[i]) for i in moving]
    return [start for start in starts if start is not None]


def _anchored(result, still, values):
    """要合并成静止整体的碎块序号: 模拟记录里设计上静止的碎块, 且整段动画里确实没有动

    result 模拟记录
    still 不动的碎块序号
    values 参数值, 提供是否合并
    返回 排序后的序号列表; 不合并或没有锚定的碎块时为空; 记录里没有锚定信息时抛 RigError
    """
    if not values.merge_anchored:
        return []
    anchored = result.get("anchored")
    # 旧版本烘焙的成果没有这个字段
    if anchored is None:
        raise RigError(NO_ANCHOR_MESSAGE)
    return sorted(set(anchored) & set(still))


def _swap_plan(root, values, result, rests):
    """原物体切换的构建输入; 仅碎块时为 None

    root 资产根集合
    values 参数值, 提供绑定方式, 两个开关帧与隐藏缩放
    result 模拟记录, 提供起始帧
    rests 全部碎块的静止世界矩阵
    返回 SwapPlan 或 None; 源物体已被删除时抛 RigError
    """
    if values.rig_mode != SWAP:
        return None
    return SwapPlan(
        source=swap.source_object(root),
        start=result["frame_start"],
        hide=values.intact_hide_frame,
        show=values.shards_show_frame,
        length=values.idle_length,
        pivot=swap.pivot(rests),
        hidden=values.hidden_scale,
    )


def _partition(points, values):
    """按骨骼上限划分有骨骼的碎块; 原物体切换时每个骨架多留原物体与 still 两根骨骼

    points 有骨骼碎块的静止质心
    values 参数值
    返回 划分结果; 上限放不下固定骨骼时抛 RigError
    """
    fixed = 1 + (swap.SWAP_BONES if values.rig_mode == SWAP else 0)
    try:
        return partition(points, values.max_bones_total, values.max_bones_per_mesh, fixed)
    except ValueError as error:
        raise RigError(BONE_ROOM_MESSAGE, count=fixed) from error


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
        raise RigError(NO_SIMULATION_MESSAGE, name=root.name)
    result, items, rests, tracked = _read(root)
    moving, still = tracks.classify(
        items, rests, tracked, values.static_distance, values.static_angle
    )
    starts = _starts(items, rests, moving, tracked)
    plan_swap = _swap_plan(root, values, result, rests)
    # 导出物体与集合的名字由资产名派生, 材质名原样进入 FBX: 在建任何东西之前先检查
    if values.ascii_names:
        sources = items if plan_swap is None else [*items, plan_swap.source]
        check.require_ascii([asset.source_label(root), *material_names(sources)])
    base = _anchored(result, still, values)
    # 设计上静止的碎块合成静止整体, 没有骨骼; 其余不动的碎块: 绑定静止碎块时每块都有骨骼, 骨架只由碎块本身
    # 与锚定设置决定, 与模拟里哪些碎块会动无关, 不同模拟可以共用; 否则它们没有骨骼, 并入 root 或 still
    if values.bind_still:
        bound, loose = [index for index in range(len(items)) if index not in base], []
    else:
        bound, loose = moving, [index for index in still if index not in base]
    names = _bone_names(bound)
    points = [tuple(rests[index].translation) for index in bound]
    plan = _partition(points, values)
    yield Progress(1, STAGES)
    label = asset.source_label(root)
    asset_id = shards.asset_of(root)
    # 静止整体从未破碎的源物体出发求得, 外表面就是源物体自己的几何
    base_mesh = seamless.build_base(context, root, items, base, tracker) if base else None
    building = _make_collection(context, tracker, BUILDING_PREFIX + label)
    inputs = Inputs(
        items, rests, names, bound, loose, frozenset(moving), base, base_mesh, tracked, plan_swap
    )
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
        # 合并成静止整体的碎块序号与整体归属的骨骼 (顶点组); 没有合并时为 None; 检查不再逐块核对这些碎块
        "merged": {
            "shards": list(base),
            "bone": ROOT_BONE if plan_swap is None else swap.STILL_BONE,
        }
        if base
        else None,
        "armatures": len(armatures),
        "meshes": len(meshes),
        "bones": sum(len(obj.data.bones) for obj in armatures),
        "last_start": max(starts, default=None),
        # 第一块碎块离开静止姿态的帧: 游戏导出的"从起动帧开始"据此裁剪动画范围; 没有碎块起动时取起始帧
        "first_start": min(starts, default=result["frame_start"]),
        # 绑定方式与两个开关帧: 仅碎块时为 None; 校验只核对碎块出现帧及之后
        "mode": values.rig_mode,
        "hide_frame": None if plan_swap is None else plan_swap.hide,
        "show_frame": None if plan_swap is None else plan_swap.show,
        "idle_length": None if plan_swap is None else plan_swap.length,
        # 构建时是否要求 ASCII 名称: 游戏导出据此在写文件前再查一次文件名与物体名
        "ascii": values.ascii_names,
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
        raise RigError(VERIFY_FAILED_MESSAGE, detail=detail)
    yield Progress(5, STAGES)
    # 逐帧核对刚做过, 这里只做其余的检查
    issues = check.check_rig(context, root, values, sample_frames=0)
    errors = [issue for issue in issues if issue.level == check.ERROR]
    if errors:
        detail = check.issue_text(errors[0])
        raise RigError(VERIFY_FAILED_MESSAGE, detail=detail)
    _claim_all(building, asset_id)
    yield Progress(6, STAGES)
    rig = RigResult(
        asset=root.name,
        rig_id=rig_id,
        moving=len(moving),
        static=len(still),
        merged=len(base),
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


def _commit(pending):
    """全部资产都校验通过后提交: 删除旧导出, 把临时名改成最终名字

    pending Pending 列表
    """
    for item in pending:
        # 旧导出与新建的临时集合不是同一个才需要删除; 用户放进旧集合的物体保留
        if item.old is not None and item.old != item.collection:
            asset.remove_rig_collection(item.old, shards.asset_of(item.root))
    # 先删旧的再改名: 否则新旧同名, 改名会得到 .001 后缀
    for item in pending:
        collection = item.collection
        for obj in collection.objects:
            final = obj.name.removeprefix(BUILDING_PREFIX)
            obj.name = final
            obj.data.name = final
            if obj.animation_data and obj.animation_data.action:
                obj.animation_data.action.name = ACTION_PREFIX + final
            idle = asset.idle_action(obj)
            if idle is not None:
                idle.name = ACTION_PREFIX + final + IDLE_SUFFIX
        collection.name = asset.source_label(item.root) + EXPORT_SUFFIX


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
        run_all(steps)
    return [item.result for item in pending]
