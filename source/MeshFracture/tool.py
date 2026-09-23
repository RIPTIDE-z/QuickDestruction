"""MeshFracture 的注册声明与菜单入口。"""

import bpy
from bpy.app.handlers import persistent
from bpy.types import PropertyGroup

from ..tool_contract import ToolSpec
from .constants import CONTEXT, TOOL_ID
from .settings import PROPERTY_SPECS, property_definitions


class MeshFractureSettings(PropertyGroup):
    """场景中的网格破碎参数组, 与生成算子共用同一份规格。"""

    __annotations__ = property_definitions()


def classes():
    """在根翻译目录建立后加载交互类, 避免导入环。"""
    from .interaction import panel, presets
    from .interaction.operators import OBJECT_OT_add_quick_destruction_objects

    return (OBJECT_OT_add_quick_destruction_objects, *presets.CLASSES, panel.QD_PT_settings)


def menu_func(self, _context):
    """在物体菜单的快速效果组添加 MeshFracture 入口。"""
    layout = self.layout
    layout.separator()
    layout.operator("object.add_quick_destruction_objects", text="MeshFracture", text_ctxt=CONTEXT)


def clear_sessions():
    """释放对话框持有的旧场景和参数对象。"""
    from .interaction.presets import SESSIONS

    SESSIONS.clear()


@persistent
def load_pre(_unused):
    """加载新文件前移除与旧文件关联的会话。"""
    clear_sessions()


TOOL = ToolSpec(
    tool_id=TOOL_ID,
    translation_module="MeshFracture",
    translation_context=CONTEXT,
    property_specs=PROPERTY_SPECS,
    settings_attr="fracture",
    settings_class=MeshFractureSettings,
    classes=classes,
    menu_hooks=((bpy.types.VIEW3D_MT_object_quick_effects, menu_func),),
    load_pre=load_pre,
    clear_sessions=clear_sessions,
)
