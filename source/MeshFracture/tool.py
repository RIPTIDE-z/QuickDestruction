"""工具声明 - 场景属性组, 交互类清单, 菜单入口, 会话清理与文件加载回调"""

import bpy
from bpy.app.handlers import persistent
from bpy.props import StringProperty
from bpy.types import PropertyGroup

from ..tool_contract import ToolSpec
from .constants import CONTEXT, TOOL_ID
from .settings import PROPERTY_SPECS, property_definitions


class MeshFractureSettings(PropertyGroup):
    """场景中的生成参数与本工具当前预设名"""

    __annotations__ = {
        **property_definitions(),
        "preset_name": StringProperty(name="Preset", translation_context=CONTEXT),
    }


def classes():
    """在根翻译目录建立后加载交互类, 避免导入环"""
    from .interaction import panel, presets
    from .interaction.operators import MESH_FRACTURE_OT_generate_fragments

    return (MESH_FRACTURE_OT_generate_fragments, *presets.CLASSES, panel.MESH_FRACTURE_PT_settings)


def menu_func(self, _context):
    """在物体菜单的快速效果组添加 MeshFracture 入口"""
    layout = self.layout
    layout.separator()
    layout.operator("mesh_fracture.generate_fragments", text="MeshFracture", text_ctxt=CONTEXT)


def clear_sessions():
    """释放对话框持有的旧场景和参数对象"""
    from .interaction.presets import SESSIONS

    SESSIONS.clear()


@persistent
def load_pre(_unused):
    """加载新文件前移除与旧文件关联的会话"""
    clear_sessions()


TOOL = ToolSpec(
    tool_id=TOOL_ID,
    translation_module="MeshFracture",
    translation_context=CONTEXT,
    property_specs=PROPERTY_SPECS,
    settings_attr="mesh_fracture",
    settings_class=MeshFractureSettings,
    classes=classes,
    menu_hooks=((bpy.types.VIEW3D_MT_object_quick_effects, menu_func),),
    load_pre=load_pre,
    clear_sessions=clear_sessions,
)
