"""工具声明 - 场景属性组, 交互类清单, 菜单入口, 会话清理与文件加载回调"""

import bpy
from bpy.app.handlers import persistent
from bpy.props import StringProperty
from bpy.types import PropertyGroup

from ..tool_contract import ToolSpec
from .constants import CONTEXT, SETTINGS_ATTR, TOOL_ID
from .settings import PROPERTY_SPECS, property_definitions


class MeshFractureSettings(PropertyGroup):
    """场景中的生成参数与本工具当前预设名"""

    # 27 项生成参数与生成算子共用同一份定义, 避免两处接口分叉;
    # preset_name 只记录界面显示的名称, 不属于预设文件的参数
    __annotations__ = {
        **property_definitions(),
        "preset_name": StringProperty(name="Preset", translation_context=CONTEXT),
    }


def classes():
    """在根翻译目录建立后加载交互类, 避免导入环"""
    # 函数体内导入是必需的: i18n 建立目录时会导入 tool_registry 与本模块,
    # 若在顶层导入交互类就会形成导入环
    from .interaction import panel, presets
    from .interaction.operators import MESH_FRACTURE_OT_generate_fragments

    # 顺序即注册顺序: 生成算子, 预设算子与菜单, 最后是子面板
    return (MESH_FRACTURE_OT_generate_fragments, *presets.CLASSES, panel.MESH_FRACTURE_PT_settings)


def menu_func(self, _context):
    """在物体菜单的快速效果组添加 MeshFracture 入口"""
    layout = self.layout
    # 分隔线把本工具与菜单中的其它条目分开
    layout.separator()
    layout.operator("mesh_fracture.generate_fragments", text="MeshFracture", text_ctxt=CONTEXT)


def clear_sessions():
    """释放对话框持有的旧场景和参数对象"""
    # 延迟导入: 预设模块属于交互层, 只在真正需要清理时才加载
    from .interaction.presets import SESSIONS

    SESSIONS.clear()


@persistent
def load_pre(_unused):
    """加载新文件前移除与旧文件关联的会话"""
    # persistent 让 handler 在加载文件后仍保持注册
    clear_sessions()


def scene_settings(context):
    """取本工具在当前场景的参数组

    字段名来自 SETTINGS_ATTR, 是交互层访问场景参数的唯一入口:
    工具改名时只需改常量, 各处调用不必跟着改
    """
    return getattr(context.scene.qd_settings, SETTINGS_ATTR)


TOOL = ToolSpec(
    tool_id=TOOL_ID,
    translation_module="MeshFracture",
    translation_context=CONTEXT,
    property_specs=PROPERTY_SPECS,
    # QDSettings 上的字段名, 与 scene_settings() 读取的路径同源
    settings_attr=SETTINGS_ATTR,
    settings_class=MeshFractureSettings,
    classes=classes,
    # 物体菜单中的入口
    menu_hooks=((bpy.types.VIEW3D_MT_object_quick_effects, menu_func),),
    load_pre=load_pre,
    clear_sessions=clear_sessions,
)
