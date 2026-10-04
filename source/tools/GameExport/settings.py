"""参数规格 - 游戏导出的场景参数: 引擎预设与轴向缩放, 绑定构建的阈值与骨骼上限, 导出目录

engine 必须排在轴向与缩放之前: 预设载入与回滚都按规格顺序写入, 后写的字段会覆盖引擎联动写入的值
"""

from ...common.schema import Schema
from .constants import CONTEXT, ENGINE_FIELDS, ENGINE_PRESETS

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
            "default": "GENERIC",
            "update": _load_engine,
            "description": "Choosing an engine fills in the axes and scale below; they can "
            "still be edited afterwards.",
        },
    ),
    # 导出文件的前向轴
    "axis_forward": (
        "EnumProperty",
        {
            "name": "Forward Axis",
            "items": tuple(_axis_item(axis) for axis in AXES),
            "default": "-Z",
            "description": "Axis that points forward in the exported file.",
        },
    ),
    # 导出文件的上方向轴, 必须与前向轴不在同一条轴上
    "axis_up": (
        "EnumProperty",
        {
            "name": "Up Axis",
            "items": tuple(_axis_item(axis) for axis in AXES),
            "default": "Y",
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
            "default": "FBX_SCALE_NONE",
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
    # 不动判定的位移阈值, Blender 单位
    "static_distance": (
        "FloatProperty",
        {
            "name": "Still Distance",
            "description": "Shards that never move farther than this and never turn more than "
            "Still Angle are merged into the root and get no bone.",
            "min": 0.0,
            "soft_max": 0.1,
            "default": 0.001,
            "precision": 4,
            "subtype": "DISTANCE",
            "unit": "LENGTH",
        },
    ),
    # 不动判定的转角阈值, 弧度, 默认约 0.5 度
    "static_angle": (
        "FloatProperty",
        {
            "name": "Still Angle",
            "description": "Largest turn of a shard that still counts as standing still.",
            "min": 0.0,
            "soft_max": 0.0872665,
            "default": 0.0087266,
            "subtype": "ANGLE",
        },
    ),
    # 一段蒙皮网格最多使用的骨骼数, 0 表示不限
    "max_bones_per_mesh": (
        "IntProperty",
        {
            "name": "Bones per Mesh",
            "description": "Most bones, including the root, one skinned mesh may use; the "
            "mesh is split into several when needed. 0 is no limit.",
            "min": 0,
            "default": 0,
        },
    ),
    # 一个骨架最多的骨骼数, 0 表示不限
    "max_bones_total": (
        "IntProperty",
        {
            "name": "Bones per Skeleton",
            "description": "Most bones, including the root, one skeleton may have; shards "
            "beyond it go to extra skeletons exported to separate files. 0 is no limit.",
            "min": 0,
            "default": 0,
        },
    ),
    # 断面 UV 的处理方式
    "interior_uv": (
        "EnumProperty",
        {
            "name": "Interior UV",
            "items": (
                ("KEEP", "Keep", "Leave the UVs of the interior faces as they are."),
                (
                    "BOX",
                    "Box Projection",
                    "Project the interior faces onto the axis their normal points along.",
                ),
            ),
            "default": "BOX",
            "description": "UVs for the faces created by cutting, so a material can be put on "
            "them in the engine.",
        },
    ),
    # 盒投影时每单位距离对应的 UV 长度
    "interior_uv_scale": (
        "FloatProperty",
        {
            "name": "UV Scale",
            "description": "UV length per unit of distance for the box projection.",
            "min": 0.001,
            "soft_max": 10.0,
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


def _box_projection(values):
    """UV 缩放只在盒投影时生效

    values 参数组
    返回 生效为真
    """
    return values.interior_uv == "BOX"


# 参数的生效条件: 字段名 -> 条件函数; 条件不成立时界面置灰但仍可编辑
ACTIVE_WHEN = {"interior_uv_scale": _box_projection}

# 绑定构建改变产物的字段: 它们变了绑定就该重建, 记在绑定记录里与面板比较
BUILD_FIELDS = (
    "static_distance",
    "static_angle",
    "max_bones_per_mesh",
    "max_bones_total",
    "interior_uv",
    "interior_uv_scale",
)

# 绑定翻译上下文的参数规格
SCHEMA = Schema(PROPERTY_SPECS, CONTEXT)
