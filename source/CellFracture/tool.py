"""CellFracture 的注册声明与菜单入口。"""

import bpy
from bpy.app.handlers import persistent

from ..tool_contract import ToolSpec
from .constants import CONTEXT, TOOL_ID
from .properties import CellFractureSettings
from .settings import PROPERTY_SPECS


def classes():
    """在根翻译目录建立后加载交互类, 避免导入环。"""
    from . import panel, presets
    from .operators import OBJECT_OT_add_quick_destruction_objects

    return (OBJECT_OT_add_quick_destruction_objects, *presets.CLASSES, panel.QD_PT_settings)


def menu_func(self, _context):
    """在物体菜单的快速效果组添加 CellFracture 入口。"""
    layout = self.layout
    layout.separator()
    layout.operator(
        "object.add_quick_destruction_objects", text="Quick Destruction Effect", text_ctxt=CONTEXT
    )


def clear_sessions():
    """释放对话框持有的旧场景和参数对象。"""
    from .presets import SESSIONS

    SESSIONS.clear()


@persistent
def load_pre(_unused):
    """加载新文件前移除与旧文件关联的会话。"""
    clear_sessions()


TOOL = ToolSpec(
    tool_id=TOOL_ID,
    translation_module="CellFracture",
    translation_context=CONTEXT,
    property_specs=PROPERTY_SPECS,
    settings_attr="fracture",
    settings_class=CellFractureSettings,
    classes=classes,
    menu_hooks=((bpy.types.VIEW3D_MT_object_quick_effects, menu_func),),
    load_pre=load_pre,
    clear_sessions=clear_sessions,
)
