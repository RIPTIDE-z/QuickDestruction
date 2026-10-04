"""FBX 导出 - 每个骨架连同它的蒙皮网格写成一个 FBX 文件, 轴向与缩放按引擎预设

导出期间临时把场景帧范围设为模拟范围, 并改变选择与活动物体; 无论成败结束时都还原
"""

import re
from pathlib import Path

import bpy

from ....common import asset
from ....common.created_data import WriteLog
from ..constants import ASSET_TOKEN, EXPORT_SUFFIX, FBX_EXTENSION, FBX_FIXED
from ..errors import ExportError
from .check import export_objects

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


def _label(root):
    """资产的显示名: 导出集合名去掉后缀, 没有导出集合时取根集合名"""
    collection = asset.rig_collection(root)
    return collection.name.removesuffix(EXPORT_SUFFIX) if collection is not None else root.name


def _stem(root, values):
    """导出文件名主干: 文件名参数里的占位符换成资产名, 去掉用户多写的扩展名, 非法字符换成下划线

    文件名为空时按默认的占位符处理; 处理后为空时取 export
    """
    pattern = values.file_name.strip() or ASSET_TOKEN
    name = pattern.replace(ASSET_TOKEN, _label(root))
    if name.lower().endswith(FBX_EXTENSION):
        name = name[: -len(FBX_EXTENSION)]
    return INVALID_NAME.sub("_", name).strip() or "export"


def resolve_directory(values):
    """导出目录的绝对路径; 为空或相对路径而文件未保存时报错"""
    raw = values.directory.strip()
    if not raw:
        raise ExportError(NO_DIRECTORY_MESSAGE)
    if raw.startswith("//") and not bpy.data.filepath:
        raise ExportError(UNSAVED_MESSAGE)
    return Path(bpy.path.abspath(raw))


def target_files(root, values):
    """导出的文件路径列表, 一个骨架一个; 骨架不止一个时文件名加两位序号"""
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
    """目标文件里已经存在的"""
    return [path for path in target_files(root, values) if path.exists()]


def _check_axes(values):
    """前向轴与上方向轴不能在同一条轴上"""
    if values.axis_forward.lstrip("-") == values.axis_up.lstrip("-"):
        raise ExportError(AXIS_CONFLICT_MESSAGE)


def _set_frames(scene, log, first, last):
    """导出期间把帧范围设为模拟范围; 按不会被互相钳制的顺序写入"""
    order = ("frame_start", "frame_end")
    if first >= scene.frame_end:
        order = ("frame_end", "frame_start")
    for name in order:
        log.set(scene, name, first if name == "frame_start" else last)


def _select(context, objects, active):
    """只选中给定物体并设为活动"""
    for obj in context.view_layer.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    context.view_layer.objects.active = active


def export_files(context, root, values, overwrite):
    """导出资产的绑定, 返回写出的文件路径列表; 目标已存在且不允许覆盖时报错, 什么都不写"""
    if not hasattr(bpy.ops.export_scene, "fbx"):
        raise ExportError(NO_ADDON_MESSAGE)
    _check_axes(values)
    paths = target_files(root, values)
    if not overwrite:
        for path in paths:
            if path.exists():
                raise ExportError(EXISTS_MESSAGE, path=str(path))
    paths[0].parent.mkdir(parents=True, exist_ok=True)
    armatures, meshes = export_objects(root)
    record = asset.rig_record(root)
    scene, view = context.scene, context.view_layer
    selection = [obj for obj in view.objects if obj.select_get()]
    active, frame = view.objects.active, scene.frame_current
    log = WriteLog()
    try:
        _set_frames(scene, log, *record["frames"])
        for armature, path in zip(armatures, paths, strict=True):
            members = [obj for obj in meshes if obj.parent == armature]
            _select(context, [armature, *members], armature)
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
        log.undo()
        _select(context, selection, active)
        scene.frame_set(frame)
    return paths


def all_targets(roots, values):
    """全部资产的目标文件路径列表; 不同资产写到同一个文件时报错, 避免后写的悄悄覆盖先写的"""
    seen = {}
    for root in roots:
        for path in target_files(root, values):
            key = str(path).lower()
            if key in seen and seen[key] is not root:
                raise ExportError(SAME_FILE_MESSAGE, path=str(path), asset=ASSET_TOKEN)
            seen[key] = root
    return [path for root in roots for path in target_files(root, values)]
