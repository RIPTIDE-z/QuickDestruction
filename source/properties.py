"""场景参数聚合根 - QDSettings 按工具声明为每个工具挂载一个参数组"""

from bpy.props import PointerProperty
from bpy.types import PropertyGroup

from .tool_registry import TOOLS


class QDSettings(PropertyGroup):
    """每个工具在场景中拥有独立参数组"""

    # 字段由工具声明推导: 键是 ToolSpec.settings_attr, 类型是 ToolSpec.settings_class;
    # 未声明参数组的工具不会出现在这里, 因此场景上也不会有它的持久化路径
    __annotations__ = {
        tool.settings_attr: PointerProperty(type=tool.settings_class)
        for tool in TOOLS
        if tool.settings_class is not None
    }
