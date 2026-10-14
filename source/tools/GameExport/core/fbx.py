"""FBX 导出 - 每个骨架连同它的蒙皮网格写成 FBX 文件, 轴向与缩放按引擎预设

骨架带待机动作 (原物体切换) 时写两个文件: 待机与破碎, 否则写一个
导出期间临时把场景帧范围设为这个文件的动画范围, 待机文件还临时换上待机动作, 并改变选择与活动物体;
无论成败结束时都还原
"""

import re
from pathlib import Path
from typing import NamedTuple

import bpy
from mathutils import Matrix

from ....common import asset
from ....common.created_data import WriteLog
from ....common.names import material_names
from ..constants import (
    ANIM_RANGE_BREAK,
    ANIM_RANGE_FULL,
    ARMATURE_HOLD_NAME,
    ARMATURE_NODE_NAME,
    ASSET_TOKEN,
    DESTRUCTION_SUFFIX,
    FBX_EXTENSION,
    FBX_FIXED,
    FBX_STATIC,
    FINAL_POSE_SUFFIX,
    IDLE_SUFFIX,
    INTACT_BONE,
)
from ..errors import ExportError

# 资产还没有构建绑定时的错误
NOT_BUILT_MESSAGE = "Build the rig first in the Rig panel"
# 没有导出插件时的错误
NO_ADDON_MESSAGE = "The FBX exporter is disabled; enable the add-on Import-Export: FBX format"
# 轴向冲突时的错误
AXIS_CONFLICT_MESSAGE = "The forward and up axes must lie along different axes"
# 目录为空时的错误
NO_DIRECTORY_MESSAGE = "Set an export directory first"
# 目录是相对路径但文件还没保存时的错误
UNSAVED_MESSAGE = "Save the blend file first, or use an absolute export directory"
# 几个资产会写到同一个文件时的错误
SAME_FILE_MESSAGE = "Several assets would write the same file {path}; put {asset} in the file name"
# 目标文件已存在且不允许覆盖时的错误
EXISTS_MESSAGE = "File already exists: {path}"
# 选了"从起动帧开始"而绑定里没有起动帧记录 (旧版本构建) 时的错误
NO_BREAK_MESSAGE = "The rig has no break start frame; build it again in the Rig panel"
# 自定义动画范围落在模拟范围之外时的错误
RANGE_MESSAGE = (
    "The custom animation range ends before it starts: {first} to {last} "
    "inside the simulation range {start} to {end}"
)
# 文件名里不能出现的字符
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def export_objects(root):
    """资产导出集合里的骨架与蒙皮网格, 由绑定构建工具建立

    root 资产根集合
    返回 (骨架列表, 网格列表), 各自按名字排序; 没有导出集合时抛 ExportError
    """
    found = asset.rig_objects(root)
    if found is None:
        raise ExportError(NOT_BUILT_MESSAGE)
    return found


def _stem(root, values):
    """导出文件名主干: 文件名参数里的占位符换成资产名, 去掉用户多写的扩展名, 非法字符换成下划线

    文件名为空时按默认的占位符处理; 处理后为空时取 export
    root 资产根集合
    values 参数值, 提供文件名
    返回 不含扩展名的文件名
    """
    pattern = values.file_name.strip() or ASSET_TOKEN
    # 与导出物体同一个名字来源; 不从导出集合名反推, 集合重名时它会带上 .001 后缀
    name = pattern.replace(ASSET_TOKEN, asset.source_label(root))
    # 用户常把扩展名一并写进去, 去掉避免出现 a.fbx.fbx
    if name.lower().endswith(FBX_EXTENSION):
        name = name[: -len(FBX_EXTENSION)]
    return INVALID_NAME.sub("_", name).strip() or "export"


def directory_missing(values):
    """导出目录是否还定不下来: 为空, 或是 // 开头的相对路径而 blend 文件还没保存

    values 参数值, 提供目录
    返回 定不下来为真, 此时界面应当让用户选择一个绝对目录
    """
    raw = values.directory.strip()
    return not raw or (raw.startswith("//") and not bpy.data.filepath)


def export_names(root, values):
    """导出会写进 FBX 的名字: 文件名主干, 导出物体名与材质名

    root 资产根集合, 必须已经构建
    values 参数值, 提供文件名
    返回 名字列表
    """
    armatures, meshes = export_objects(root)
    objects = [obj.name for obj in (*armatures, *meshes)]
    return [_stem(root, values), *objects, *material_names(meshes)]


def resolve_directory(values):
    """求导出目录的绝对路径

    values 参数值, 提供目录
    返回 目录路径; 为空, 或相对路径而文件未保存时抛 ExportError
    """
    raw = values.directory.strip()
    if not raw:
        raise ExportError(NO_DIRECTORY_MESSAGE)
    # 未保存的文件没有位置可供 // 相对, abspath 会原样返回 // 开头的路径
    if raw.startswith("//") and not bpy.data.filepath:
        raise ExportError(UNSAVED_MESSAGE)
    return Path(bpy.path.abspath(raw))


class Take(NamedTuple):
    """要写出的一个 FBX 文件"""

    # 骨架物体, 连同以它为父级的蒙皮网格一起写出
    armature: object
    # 文件路径
    path: Path
    # 导出期间临时换上的动作; None 表示用骨架的当前动作 (破碎动作)
    action: object
    # 动画的起止帧, 场景里的帧号
    frames: tuple
    # 导出时把动作整体向前平移的帧数, 让文件里的第一帧是第 0 帧; 0 表示不平移
    shift: int = 0


def animation_range(record, values):
    """破碎动画的导出范围

    record 绑定记录, 提供模拟范围与第一块碎块的起动帧
    values 参数值, 提供范围模式
    返回 (起始帧, 结束帧), 场景里的帧号, 都在模拟范围里; 范围不合法时抛 ExportError
    """
    start, end = record["frames"]
    mode = values.anim_range
    if mode == ANIM_RANGE_FULL:
        return start, end
    if mode == ANIM_RANGE_BREAK:
        first = record.get("first_start")
        # 旧版本构建的绑定没有起动帧记录
        if first is None:
            raise ExportError(NO_BREAK_MESSAGE)
        # 起动帧之前留 break_lead 帧静止姿态, 不早于模拟起始帧
        return min(max(start, first - values.break_lead), end), end
    # 自定义范围: 结束帧为 0 表示到模拟结束, 超出模拟范围的部分去掉
    first = max(start, values.range_start)
    last = end if values.range_end == 0 else min(end, values.range_end)
    if first > last:
        raise ExportError(RANGE_MESSAGE, first=first, last=last, start=start, end=end)
    return first, last


def takes(root, values):
    """一个资产要写出的全部文件

    root 资产根集合, 必须已经构建
    values 参数值, 提供目录, 文件名与动画范围
    返回 Take 列表; 骨架不止一个时文件名加两位序号, 带待机动作的骨架再分待机与破碎两个文件
    """
    armatures, _meshes = export_objects(root)
    directory = resolve_directory(values)
    stem = _stem(root, values)
    frames = animation_range(asset.rig_record(root), values)
    # 裁剪过的范围平移到第 0 帧开始, 完整的模拟范围保持原来的帧号
    shift = 0 if values.anim_range == ANIM_RANGE_FULL else frames[0]
    found = []
    for index, armature in enumerate(armatures):
        base = stem if len(armatures) == 1 else f"{stem}_{index + 1:02d}"
        idle = asset.idle_action(armature)
        # 仅碎块: 一个文件, 用骨架当前的动作
        if idle is None:
            found.append(Take(armature, directory / f"{base}{FBX_EXTENSION}", None, frames, shift))
            continue
        # 原物体切换: 待机文件用待机动作与它自己的帧范围, 破碎文件用当前动作与模拟范围
        idle_frames = tuple(round(value) for value in idle.frame_range)
        found.append(
            Take(armature, directory / f"{base}{IDLE_SUFFIX}{FBX_EXTENSION}", idle, idle_frames)
        )
        found.append(
            Take(
                armature,
                directory / f"{base}{DESTRUCTION_SUFFIX}{FBX_EXTENSION}",
                None,
                frames,
                shift,
            )
        )
    return found


def final_pose_path(root, values):
    """终局静态姿态文件的路径

    root 资产根集合
    values 参数值, 提供目录与文件名
    返回 路径
    """
    return resolve_directory(values) / f"{_stem(root, values)}{FINAL_POSE_SUFFIX}{FBX_EXTENSION}"


def target_files(root, values):
    """求一个资产导出的文件路径

    root 资产根集合, 必须已经构建
    values 参数值, 提供目录与文件名
    返回 路径列表, 与 takes 同序; 开启终局姿态文件时最后多一个
    """
    paths = [take.path for take in takes(root, values)]
    if values.export_final_pose:
        paths.append(final_pose_path(root, values))
    return paths


def existing_files(root, values):
    """目标文件里已经存在的

    root 资产根集合
    values 参数值
    返回 已存在的路径列表
    """
    return [path for path in target_files(root, values) if path.exists()]


def _check_axes(values):
    """前向轴与上方向轴不能在同一条轴上, 否则 FBX 导出插件会报错

    values 参数值, 提供两个轴向
    """
    if values.axis_forward.lstrip("-") == values.axis_up.lstrip("-"):
        raise ExportError(AXIS_CONFLICT_MESSAGE)


def _set_frames(scene, log, first, last):
    """导出期间把帧范围设为这个文件的动画范围; 按不会被互相钳制的顺序写入

    scene 场景
    log 写前快照, 结束时由调用方撤回
    first 起始帧
    last 结束帧
    """
    order = ("frame_start", "frame_end")
    # 新起始帧不早于旧结束帧时先写结束帧, 否则先写起始帧, 两者都不会被对方钳制
    if first >= scene.frame_end:
        order = ("frame_end", "frame_start")
    for name in order:
        log.set(scene, name, first if name == "frame_start" else last)


def _action_curves(action):
    """动作里全部变换曲线

    action 动作
    返回 曲线列表
    """
    return [
        curve
        for layer in action.layers
        for strip in layer.strips
        for bag in strip.channelbags
        for curve in bag.fcurves
    ]


def _move_keys(curve, delta):
    """把一条曲线的全部关键帧与手柄在时间轴上平移 delta 帧

    curve 动画曲线
    delta 平移的帧数, 可以为负
    """
    points = curve.keyframe_points
    for attribute in ("co", "handle_left", "handle_right"):
        flat = [0.0] * (2 * len(points))
        points.foreach_get(attribute, flat)
        flat[0::2] = [value + delta for value in flat[0::2]]
        points.foreach_set(attribute, flat)
    curve.update()


def _shift_action(action, shift, log):
    """导出期间把动作的全部关键帧整体向前平移 shift 帧, 写完后由 log 撤回

    shift 为 0 时什么也不做; 平移后文件里的第一帧是第 0 帧
    action 要平移的动作, 通常是骨架当前的破碎动作
    shift 向前平移的帧数
    log 写前快照, 导出结束后由调用方撤回
    """
    if not shift:
        return
    curves = _action_curves(action)
    for curve in curves:
        _move_keys(curve, -shift)

    def restore():
        """把关键帧平移回原来的帧号"""
        for curve in curves:
            _move_keys(curve, shift)

    log.add(restore)


def _take_node_name(armature, log):
    """导出期间把骨架物体改名为 Armature, 虚幻引擎据此跳过骨骼之上的顶层节点

    场景里别的物体已经叫 Armature 时先把它临时改名, 撤回时按相反顺序恢复两者的名字
    armature 要导出的骨架物体
    log 写前快照, 导出结束后由调用方撤回
    """
    holder = bpy.data.objects.get(ARMATURE_NODE_NAME)
    if holder is not None and holder != armature:
        log.set(holder, "name", ARMATURE_HOLD_NAME)
    log.set(armature, "name", ARMATURE_NODE_NAME)


def _use_action(armature, action, log):
    """导出期间给骨架换上另一个动作, 撤回时连同槽位一起恢复

    armature 骨架物体
    action 要换上的动作
    log 写前快照, 导出结束后由调用方撤回
    """
    data = armature.animation_data
    old_action, old_slot = data.action, data.action_slot

    def restore():
        """先恢复动作再恢复槽位: 槽位必须属于当前动作"""
        data.action = old_action
        data.action_slot = old_slot

    log.add(restore)
    data.action = action
    # 换上动作时 Blender 按上次的槽位名找槽位, 找不到时取它唯一的槽位
    if data.action_slot is None and action.slots:
        data.action_slot = action.slots[0]


def _select(context, objects, active):
    """只选中给定物体并设为活动

    context Blender 上下文
    objects 要选中的物体序列
    active 要设为活动的物体, 可以为 None
    """
    for obj in context.view_layer.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    context.view_layer.objects.active = active


def export_files(context, root, values, overwrite):
    """导出资产的绑定, 目标已存在且不允许覆盖时报错, 什么都不写

    context Blender 上下文
    root 资产根集合, 必须已经构建
    values 参数值
    overwrite 为真时允许覆盖已存在的文件
    返回 写出的文件路径列表
    """
    # FBX 导出是内置插件, 用户可能停用了
    if not hasattr(bpy.ops.export_scene, "fbx"):
        raise ExportError(NO_ADDON_MESSAGE)
    _check_axes(values)
    planned = takes(root, values)
    paths = target_files(root, values)
    # 先全部核对再动手: 多个文件时不能写一半才发现后面的已存在
    if not overwrite:
        for path in paths:
            if path.exists():
                raise ExportError(EXISTS_MESSAGE, path=str(path))
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    _armatures, meshes = export_objects(root)
    scene, view = context.scene, context.view_layer
    # 记下现场, 结束时还原: 导出需要改选择, 活动物体与当前帧
    selection = [obj for obj in view.objects if obj.select_get()]
    active, frame = view.objects.active, scene.frame_current
    try:
        for take in planned:
            _write_take(context, take, meshes, values)
        if values.export_final_pose:
            _write_final_pose(context, root, values, paths[-1])
    finally:
        # 无论成败都还原选择, 活动物体与当前帧
        _select(context, selection, active)
        scene.frame_set(frame)
    return paths


def _write_take(context, take, meshes, values):
    """写出一个文件: 骨架连同它的蒙皮网格; 期间的改名, 换动作与帧范围在写完后立刻撤回

    同一时刻只能有一个物体叫 Armature, 所以每个文件单独改名
    context Blender 上下文
    take 要写出的文件
    meshes 资产的全部蒙皮网格
    values 参数值, 提供轴向与缩放
    """
    armature = take.armature
    members = [obj for obj in meshes if obj.parent == armature]
    _select(context, [armature, *members], armature)
    log = WriteLog()
    try:
        _take_node_name(armature, log)
        first, last = take.frames
        if take.action is not None:
            _use_action(armature, take.action, log)
        # 破碎动作的范围可能被裁剪: 关键帧平移 shift 帧, 场景范围随之平移, 文件里的第一帧是第 0 帧
        _shift_action(armature.animation_data.action, take.shift, log)
        _set_frames(context.scene, log, first - take.shift, last - take.shift)
        bpy.ops.export_scene.fbx(
            filepath=str(take.path),
            use_selection=True,
            axis_forward=values.axis_forward,
            axis_up=values.axis_up,
            apply_scale_options=values.apply_scale,
            global_scale=values.global_scale,
            **FBX_FIXED,
        )
    finally:
        log.undo()


def _skin_matrices(armature, depsgraph):
    """一个骨架在当前帧的蒙皮矩阵: 骨骼姿态去掉缩放后乘静止姿态的逆, 已含骨架的世界矩阵

    不带缩放: 原物体切换时碎块骨骼在出现帧之前缩在隐藏缩放, 终局姿态要的是碎块原尺寸
    armature 骨架物体
    depsgraph 当前帧的依赖图
    返回 {骨骼名: 矩阵}
    """
    posed = armature.evaluated_get(depsgraph)
    matrices = {}
    for bone in armature.data.bones:
        location, rotation, _scale = posed.pose.bones[bone.name].matrix.decompose()
        pose = Matrix.LocRotScale(location, rotation, None)
        matrices[bone.name] = armature.matrix_world @ pose @ bone.matrix_local.inverted()
    return matrices


def _posed_copy(obj, matrices):
    """蒙皮网格的静态副本: 每个顶点按它所属骨骼的蒙皮矩阵变到当前姿态, 副本没有顶点组与修改器

    绑定里每个顶点的权重都是 1, 只属于一根骨骼; 没有顶点组的顶点留在原处
    obj 蒙皮网格物体
    matrices _skin_matrices 的结果
    返回 网格数据, 顶点在世界坐标
    """
    mesh = obj.data.copy()
    by_group = {
        group.index: matrices[group.name] for group in obj.vertex_groups if group.name in matrices
    }
    for vertex in mesh.vertices:
        if not vertex.groups:
            continue
        group = max(vertex.groups, key=lambda item: item.weight).group
        matrix = by_group.get(group)
        if matrix is not None:
            vertex.co = matrix @ (obj.matrix_world @ vertex.co)
        else:
            vertex.co = obj.matrix_world @ vertex.co
    mesh.update()
    return mesh


def _write_final_pose(context, root, values, path):
    """写终局静态姿态文件: 破碎动画最后一帧每块碎块的姿态, 只有网格, 没有骨架与动画

    取自绑定里的蒙皮网格, 所以合并后的静止整体也在里面; 原物体网格不含在内
    临时网格与物体写完立刻删除, 帧由调用方还原
    context Blender 上下文
    root 资产根集合, 必须已经构建
    values 参数值, 提供轴向与缩放
    path 文件路径
    """
    armatures, meshes = export_objects(root)
    scene = context.scene
    # 终局姿态取破碎动画导出范围的最后一帧
    scene.frame_set(animation_range(asset.rig_record(root), values)[1])
    depsgraph = context.evaluated_depsgraph_get()
    matrices = {}
    for armature in armatures:
        matrices[armature] = _skin_matrices(armature, depsgraph)
    # 原物体网格的顶点只在原物体骨骼上; 没有骨架的网格没有姿态可言, 一并跳过
    sources = [
        obj for obj in meshes if obj.parent in matrices and INTACT_BONE not in obj.vertex_groups
    ]
    label = asset.source_label(root)
    objects, datas = [], []
    try:
        for index, obj in enumerate(sources):
            name = label + FINAL_POSE_SUFFIX
            if len(sources) > 1:
                name = f"{name}_{index + 1:02d}"
            mesh = _posed_copy(obj, matrices[obj.parent])
            mesh.name = name
            datas.append(mesh)
            copy = bpy.data.objects.new(name, mesh)
            objects.append(copy)
            context.scene.collection.objects.link(copy)
        _select(context, objects, None)
        bpy.ops.export_scene.fbx(
            filepath=str(path),
            use_selection=True,
            axis_forward=values.axis_forward,
            axis_up=values.axis_up,
            apply_scale_options=values.apply_scale,
            global_scale=values.global_scale,
            **FBX_STATIC,
        )
    finally:
        # 先删物体再删它们的网格; 都是本次新建的临时数据
        bpy.data.batch_remove([*objects, *datas])


def all_targets(roots, values):
    """求全部资产的目标文件路径; 不同资产写到同一个文件时报错, 避免后写的悄悄覆盖先写的

    roots 资产根集合序列
    values 参数值
    返回 路径列表, 按资产顺序排列
    """
    seen = {}
    for root in roots:
        for path in target_files(root, values):
            # Windows 的文件名不区分大小写, 比较时统一小写
            key = str(path).lower()
            if key in seen and seen[key] is not root:
                raise ExportError(SAME_FILE_MESSAGE, path=str(path), asset=ASSET_TOKEN)
            seen[key] = root
    return [path for root in roots for path in target_files(root, values)]
