"""参数规格 - 模拟烘焙的场景参数: 帧范围与刚体世界的求解设置

这些都是编辑输入; 烘焙时实际使用的值记进会话记录, 状态判定只看记录, 不看面板
"""

from ...common.schema import Schema
from .constants import CONTEXT

# 质量档: 预览按设定的子步烘焙, 终稿把子步提高到建议值
PREVIEW = "PREVIEW"
FINAL = "FINAL"

# 参数规格: 字段名 -> (bpy.props 的类型名, 该类型的关键字参数)
PROPERTY_SPECS = {
    "use_scene_range": (
        "BoolProperty",
        {
            "name": "Use Scene Range",
            "description": "Bake from the start to the end frame of the scene; turn off to set "
            "the range here.",
            "default": True,
        },
    ),
    "frame_start": (
        "IntProperty",
        {
            "name": "Start Frame",
            "description": "First frame of the simulation; shards are at rest on this frame.",
            "min": 0,
            "default": 1,
        },
    ),
    "frame_end": (
        "IntProperty",
        {
            "name": "End Frame",
            "description": "Last frame of the simulation.",
            "min": 1,
            "default": 120,
        },
    ),
    "quality": (
        "EnumProperty",
        {
            "name": "Quality",
            "items": (
                (PREVIEW, "Preview", "Use the substeps below as they are; quick to bake."),
                (
                    FINAL,
                    "Final",
                    "Raise the substeps to the suggested value when it is higher, so fast "
                    "shards are less likely to pass through thin ones.",
                ),
            ),
            "default": PREVIEW,
            "description": "Preview is for trying things out; Final bakes with enough substeps "
            "for the fastest shards.",
        },
    ),
    "substeps": (
        "IntProperty",
        {
            "name": "Substeps",
            "description": "Simulation steps per frame; more steps keep fast or thin shards "
            "from passing through each other, but take longer.",
            "min": 1,
            "max": 200,
            "soft_max": 50,
            "default": 10,
        },
    ),
    "solver_iterations": (
        "IntProperty",
        {
            "name": "Solver Iterations",
            "description": "Iterations per step for resolving contacts; more is steadier for "
            "stacked shards.",
            "min": 1,
            "max": 100,
            "default": 10,
        },
    ),
    "time_scale": (
        "FloatProperty",
        {
            "name": "Time Scale",
            "description": "Speed of the simulation; below 1 gives slow motion. Driver speeds "
            "keep their meaning in simulated seconds.",
            "min": 0.01,
            "max": 10.0,
            "soft_max": 2.0,
            "default": 1.0,
        },
    ),
}


def _custom_range(values):
    """帧范围字段只在不跟随场景范围时生效"""
    return not values.use_scene_range


# 参数的生效条件: 字段名 -> 条件函数; 条件不成立时界面置灰但仍可编辑
ACTIVE_WHEN = {
    "frame_start": _custom_range,
    "frame_end": _custom_range,
}

# 绑定翻译上下文的参数规格
SCHEMA = Schema(PROPERTY_SPECS, CONTEXT)


def frame_range(values, scene):
    """实际使用的帧范围: 跟随场景时取场景的起止帧, 否则取面板里的值"""
    if values.use_scene_range:
        return scene.frame_start, scene.frame_end
    return values.frame_start, values.frame_end
