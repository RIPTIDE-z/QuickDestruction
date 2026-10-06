"""FBX 导出 - 每个骨架连同它的蒙皮网格写成一个 FBX 文件, 轴向与缩放按引擎预设

导出期间临时把场景帧范围设为模拟范围, 并改变选择与活动物体; 无论成败结束时都还原
"""

import re
from pathlib import Path

import bpy

from ....common import asset
from ....common.created_data import WriteLog
from ..constants import (
    ARMATURE_HOLD_NAME,
    ARMATURE_NODE_NAME,
    ASSET_TOKEN,
    FBX_EXTENSION,
    FBX_FIXED,
)
from ..errors import ExportError
from .build import asset_label
from .check import export_objects, material_names

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
# 文件名里不能出现的字符
INVALID_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def _stem(root, values):
    """导出文件名主干: 文件名参数里的占位符换成资产名, 去掉用户多写的扩展名, 非法字符换成下划线

    文件名为空时按默认的占位符处理; 处理后为空时取 export
    root 资产根集合
    values 参数值, 提供文件名
    返回 不含扩展名的文件名
    """
    pattern = values.file_name.strip() or ASSET_TOKEN
    # 与导出物体同一个名字来源; 不从导出集合名反推, 集合重名时它会带上 .001 后缀
    name = pattern.replace(ASSET_TOKEN, asset_label(root))
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


def target_files(root, values):
    """求一个资产导出的文件路径, 一个骨架一个

    root 资产根集合, 必须已经构建
    values 参数值, 提供目录与文件名
    返回 路径列表; 骨架不止一个时文件名加两位序号
    """
    armatures, _meshes = export_objects(root)
    directory = resolve_directory(values)
    stem = _stem(root, values)
    count = len(armatures)
    return [
        directory
        / (f"{stem}{FBX_EXTENSION}" if count == 1 else f"{stem}_{index + 1:02d}{FBX_EXTENSION}")
        for index in range(count)
    ]


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
    """导出期间把帧范围设为模拟范围; 按不会被互相钳制的顺序写入

    scene 场景
    log 写前快照, 结束时由调用方撤回
    first 模拟起始帧
    last 模拟结束帧
    """
    order = ("frame_start", "frame_end")
    # 新起始帧不早于旧结束帧时先写结束帧, 否则先写起始帧, 两者都不会被对方钳制
    if first >= scene.frame_end:
        order = ("frame_end", "frame_start")
    for name in order:
        log.set(scene, name, first if name == "frame_start" else last)


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
    paths = target_files(root, values)
    # 先全部核对再动手: 多个文件时不能写一半才发现后面的已存在
    if not overwrite:
        for path in paths:
            if path.exists():
                raise ExportError(EXISTS_MESSAGE, path=str(path))
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    armatures, meshes = export_objects(root)
    record = asset.rig_record(root)
    scene, view = context.scene, context.view_layer
    # 记下现场, 结束时还原: 导出需要改选择, 活动物体与帧范围
    selection = [obj for obj in view.objects if obj.select_get()]
    active, frame = view.objects.active, scene.frame_current
    log = WriteLog()
    try:
        _set_frames(scene, log, *record["frames"])
        # 一个骨架连同它的蒙皮网格写一个文件
        for armature, path in zip(armatures, paths, strict=True):
            members = [obj for obj in meshes if obj.parent == armature]
            _select(context, [armature, *members], armature)
            # 每个骨架单独改名并在写完后立刻改回: 同一时刻只能有一个物体叫 Armature
            names = WriteLog()
            try:
                _take_node_name(armature, names)
                bpy.ops.export_scene.fbx(
                    filepath=str(path),
                    use_selection=True,
                    axis_forward=values.axis_forward,
                    axis_up=values.axis_up,
                    apply_scale_options=values.apply_scale,
                    global_scale=values.global_scale,
                    **FBX_FIXED,
                )
            finally:
                names.undo()
    finally:
        # 无论成败都还原帧范围, 选择, 活动物体与当前帧
        log.undo()
        _select(context, selection, active)
        scene.frame_set(frame)
    return paths


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
