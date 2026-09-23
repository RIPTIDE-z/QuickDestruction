"""场景参数聚合根 - QDSettings 按工具声明为每个工具挂载一个参数组"""

from bpy.props import PointerProperty
from bpy.types import PropertyGroup

from .tool_registry import TOOLS


class QDSettings(PropertyGroup):
    """每个工具在场景中拥有独立参数组"""

    __annotations__ = {
        tool.settings_attr: PointerProperty(type=tool.settings_class)
        for tool in TOOLS
        if tool.settings_class is not None
    }
