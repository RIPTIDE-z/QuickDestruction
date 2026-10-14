"""参数规格 - 游戏导出的场景参数: 引擎预设与轴向缩放, 导出目录与文件名

engine 必须排在轴向与缩放之前: 预设载入与回滚都按规格顺序写入, 后写的字段会覆盖引擎联动写入的值
轴向与缩放的默认值必须等于默认引擎的预设值: 默认值不会触发引擎的联动回调
"""

from ...common.schema import Schema
from .constants import (
    ANIM_RANGE_BREAK,
    ANIM_RANGE_CUSTOM,
    ANIM_RANGE_FULL,
    CONTEXT,
    ENGINE_FIELDS,
    ENGINE_PRESETS,
)

# FBX 轴向的取值, 与 Blender 导出插件一致
AXES = ("X", "Y", "Z", "-X", "-Y", "-Z")


def _load_engine(owner, _context):
    """选择引擎时把它的轴向与缩放写进各字段; 选择自定义时不改动

    owner 参数组
    _context Blender 传入的上下文, 不使用
    """
    values = ENGINE_PRESETS.get(owner.engine)
    # 自定义引擎不在预设表里: 保持用户已有的轴向与缩放
    if values is None:
        return
    for key, value in zip(ENGINE_FIELDS, values, strict=True):
        setattr(owner, key, value)


def engine_values(engine):
    """引擎对应的轴向与缩放字段

    engine 引擎标识
    返回 字段字典; 自定义引擎返回空字典
    """
    values = ENGINE_PRESETS.get(engine)
    return dict(zip(ENGINE_FIELDS, values, strict=True)) if values is not None else {}


def complete_config(config):
    """补全阶段 API 的配置: 先叠引擎的取值, 再叠调用方显式给出的字段, 与界面上的联动一致

    config 调用方给出的字段字典
    返回 补全后的字典, 不改动 config
    """
    engine = config.get("engine", PROPERTY_SPECS["engine"][1]["default"])
    # 引擎值不是字符串时交给后面的校验报参数错误
    if not isinstance(engine, str):
        return dict(config)
    return {**engine_values(engine), **config}


def _axis_item(axis):
    """生成轴向枚举项

    axis 轴向, 如 -Z
    返回 (标识, 名称, 说明)
    """
    sign = "negative " if axis.startswith("-") else ""
    return (axis, axis, f"The {sign}{axis.lstrip('-')} axis.")


# 参数规格: 字段名 -> (bpy.props 的类型名, 该类型的关键字参数)
PROPERTY_SPECS = {
    # 目标引擎, 选择后自动填入下面的轴向与缩放
    "engine": (
        "EnumProperty",
        {
            "name": "Engine",
            "items": (
                ("GENERIC", "Generic", "Blender's default FBX axes and scale."),
                (
                    "UNREAL",
                    "Unreal Engine",
                    "Z up, -Y forward, scale from the scene units. Check the first import "
                    "with the list in the user guide.",
                ),
                (
                    "UNITY",
                    "Unity",
                    "Y up, -Z forward, scale applied to all objects. Check the first import "
                    "with the list in the user guide.",
                ),
                ("CUSTOM", "Custom", "Keep the axes and scale below as they are."),
            ),
            "default": "UNREAL",
            "update": _load_engine,
            "description": "Choosing an engine fills in the axes and scale below; they can "
            "still be edited afterwards.",
        },
    ),
    # 破碎动画导出哪一段
    "anim_range": (
        "EnumProperty",
        {
            "name": "Animation Range",
            "items": (
                (
                    ANIM_RANGE_FULL,
                    "Full Simulation",
                    "Export the whole simulation range, with the frame numbers it has.",
                ),
                (
                    ANIM_RANGE_BREAK,
                    "From Break Start",
                    "Start at the frame where the first shard leaves its rest pose, so the "
                    "first frame of the file is the start of the breaking.",
                ),
                (
                    ANIM_RANGE_CUSTOM,
                    "Custom Range",
                    "Export only the frames between the start and end below.",
                ),
            ),
            "default": ANIM_RANGE_FULL,
            "description": "Which part of the destruction animation is exported. Trimmed "
            "ranges are shifted so the file starts at frame 0; the Idle animation and the "
            "full range are not shifted.",
        },
    ),
    # 从起动帧开始时, 起动帧之前保留的静止帧数
    "break_lead": (
        "IntProperty",
        {
            "name": "Lead Frames",
            "description": "Rest-pose frames kept before the first break frame, so the clip "
            "starts a little earlier than the first movement. 0 starts on the first moved frame.",
            "min": 0,
            "default": 0,
        },
    ),
    # 自定义范围的起始帧
    "range_start": (
        "IntProperty",
        {
            "name": "Range Start",
            "description": "First frame of the custom range; a frame before the start of the "
            "simulation is cut to it.",
            "min": 0,
            "default": 0,
        },
    ),
    # 自定义范围的结束帧, 0 表示到模拟结束
    "range_end": (
        "IntProperty",
        {
            "name": "Range End",
            "description": "Last frame of the custom range. 0 goes to the end of the simulation; "
            "a frame after it is cut to it.",
            "min": 0,
            "default": 0,
        },
    ),
    # 是否另写一个破碎动画最后一帧的静态姿态文件
    "export_final_pose": (
        "BoolProperty",
        {
            "name": "Final Pose File",
            "description": "Also write a static FBX of the shards as they lie on the last frame "
            "of the destruction animation: meshes only, with no skeleton and no animation, "
            "for example as the model of the ruins. The source object is not included.",
            "default": False,
        },
    ),
    # 导出文件的前向轴
    "axis_forward": (
        "EnumProperty",
        {
            "name": "Forward Axis",
            "items": tuple(_axis_item(axis) for axis in AXES),
            "default": "-Y",
            "description": "Axis that points forward in the exported file.",
        },
    ),
    # 导出文件的上方向轴, 必须与前向轴不在同一条轴上
    "axis_up": (
        "EnumProperty",
        {
            "name": "Up Axis",
            "items": tuple(_axis_item(axis) for axis in AXES),
            "default": "Z",
            "description": "Axis that points up in the exported file; it must differ from "
            "the forward axis.",
        },
    ),
    # 缩放写入文件的方式
    "apply_scale": (
        "EnumProperty",
        {
            "name": "Apply Scale",
            "items": (
                ("FBX_SCALE_NONE", "None", "Write sizes as they are and let the engine convert."),
                (
                    "FBX_SCALE_UNITS",
                    "Scene Units",
                    "Scale the file by the unit settings of the scene.",
                ),
                ("FBX_SCALE_ALL", "All", "Apply the scale to every object, keeping sizes."),
                ("FBX_SCALE_CUSTOM", "Custom", "Use only the global scale below."),
            ),
            "default": "FBX_SCALE_UNITS",
            "description": "How the scale is written into the file.",
        },
    ),
    # 额外施加在全部内容上的缩放
    "global_scale": (
        "FloatProperty",
        {
            "name": "Global Scale",
            "description": "Extra scale applied to everything on export.",
            "min": 0.001,
            "max": 1000.0,
            "soft_max": 100.0,
            "default": 1.0,
        },
    ),
    # 导出文件名, 不含扩展名; {asset} 换成源物体名
    "file_name": (
        "StringProperty",
        {
            "name": "File Name",
            "description": "Name of the FBX file without the extension; {asset} is replaced by "
            "the name of the source object. With several skeletons a number is added.",
            "default": "{asset}",
        },
    ),
    # 导出目录, // 表示 blend 文件所在目录
    "directory": (
        "StringProperty",
        {
            "name": "Directory",
            "description": "Folder the FBX files are written to; // is the folder of the "
            "blend file.",
            "default": "//export/",
            "subtype": "DIR_PATH",
            # 没有这个选项, 界面会把以 // 开头的路径标红并警告
            "options": {"PATH_SUPPORTS_BLEND_RELATIVE"},
        },
    ),
}


def _from_break(values):
    """起动帧之前的静止帧数只在"从起动帧开始"时生效

    values 参数组
    返回 生效为真
    """
    return values.anim_range == ANIM_RANGE_BREAK


def _custom_range(values):
    """起止帧只在"自定义范围"时生效

    values 参数组
    返回 生效为真
    """
    return values.anim_range == ANIM_RANGE_CUSTOM


# 参数的生效条件: 字段名 -> 条件函数; 条件不成立时界面把它置灰但仍可编辑
ACTIVE_WHEN = {
    "break_lead": _from_break,
    "range_start": _custom_range,
    "range_end": _custom_range,
}

# 游戏导出的参数规格
SCHEMA = Schema(PROPERTY_SPECS, CONTEXT)
