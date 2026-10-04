"""参数规格 - 场景参数只有计划预览的显示设置; 驱动参数是挂在物体上的规格, 写在 common 的驱动契约里

OBJECT_SCHEMAS 列出本工具挂到其他数据块上的规格, 语言文件检查据此核对译文
"""

from ...common.drivers import DRIVER_SCHEMA
from ...common.schema import Schema
from .constants import CONTEXT


def _redraw(_owner, _context):
    """预览设置改变后重绘三维视图: 绘制回调只在重绘时执行

    _owner 参数组, 不使用
    _context Blender 传入的上下文, 不使用
    """
    # 延迟导入: 本模块在 Blender 之外也要能被单元测试导入
    import bpy

    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


# 参数规格: 字段名 -> (bpy.props 的类型名, 该类型的关键字参数)
PROPERTY_SPECS = {
    # 是否在视口里显示计划预览
    "show_preview": (
        "BoolProperty",
        {
            "name": "Show Plan Preview",
            "description": "Draw an arrow on every shard the drivers start: its direction and "
            "length show the starting velocity, not the final path.",
            "default": False,
            "update": _redraw,
        },
    ),
    # 预览箭头相对速度的长度比例
    "preview_scale": (
        "FloatProperty",
        {
            "name": "Arrow Scale",
            "description": "Length of the preview arrows per unit of speed.",
            "min": 0.001,
            "max": 10.0,
            "soft_max": 1.0,
            "default": 0.1,
            "update": _redraw,
        },
    ),
    # 是否按起动时刻给箭头着色
    "color_by_time": (
        "BoolProperty",
        {
            "name": "Color by Start Time",
            "description": "Color the arrows from blue for the first shards to start to red "
            "for the last.",
            "default": True,
            "update": _redraw,
        },
    ),
}

# 绑定翻译上下文的场景参数规格
SCHEMA = Schema(PROPERTY_SPECS, CONTEXT)

# 本工具挂到物体上的参数规格
OBJECT_SCHEMAS = (DRIVER_SCHEMA,)
