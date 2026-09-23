"""工具声明 - 场景属性组, 交互类清单, 菜单入口, 会话清理与文件加载回调"""

import bpy
from bpy.app.handlers import persistent
from bpy.types import PropertyGroup

from ..preset_ui import SESSIONS, preset_properties
from ..tool_contract import ToolSpec
from .constants import CONTEXT, SETTINGS_ATTR, TOOL_ID
from .interaction import panel
from .interaction.operators import MESH_FRACTURE_OT_generate_fragments
from .settings import PRESET, PROPERTY_SPECS, property_definitions


class MeshFractureSettings(PropertyGroup):
    """场景中的生成参数与本工具当前预设名"""

    # 27 项生成参数与生成算子共用同一份定义, 避免两处接口分叉;
    # preset_name 由通用预设层提供, 只记录界面显示的预设名, 不属于预设文件
    __annotations__ = {
        **property_definitions(),
        **preset_properties(),
    }


def classes():
    """返回本工具要注册的算子与面板类, 元组顺序就是注册顺序"""
    # 生成算子在前, 侧栏面板在后: 面板是顶层的, 与算子的注册顺序无关
    return (MESH_FRACTURE_OT_generate_fragments, panel.MESH_FRACTURE_PT_settings)


def menu_func(self, _context):
    """在物体菜单的快速效果组添加 MeshFracture 入口"""
    layout = self.layout
    # 分隔线把本工具与菜单中的其它条目分开
    layout.separator()
    layout.operator("mesh_fracture.generate_fragments", text="MeshFracture", text_ctxt=CONTEXT)


def clear_sessions():
    """释放对话框持有的旧场景和参数对象"""
    # 会话表由通用预设层统一持有, 因此这里清掉的是全部工具的对话框会话;
    # 换文件后所有旧场景都已失效, 一并作废正是需要的效果
    SESSIONS.clear()


@persistent
def load_pre(_unused):
    """加载新文件前移除与旧文件关联的会话"""
    # persistent 让 handler 在加载文件后仍保持注册
    clear_sessions()


TOOL = ToolSpec(
    tool_id=TOOL_ID,
    translation_module="MeshFracture",
    translation_context=CONTEXT,
    property_specs=PROPERTY_SPECS,
    # QDSettings 上的字段名, 与 settings.scene_settings() 读取的路径同源
    settings_attr=SETTINGS_ATTR,
    settings_class=MeshFractureSettings,
    classes=classes,
    # 预设归属: 目录, 参数规格与界面都由通用预设层按它提供
    preset=PRESET,
    # 物体菜单中的入口
    menu_hooks=((bpy.types.VIEW3D_MT_object_quick_effects, menu_func),),
    load_pre=load_pre,
    clear_sessions=clear_sessions,
)
