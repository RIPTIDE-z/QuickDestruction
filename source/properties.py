"""场景设置聚合根, 保留已有 .blend 的属性路径。"""

from bpy.props import PointerProperty, StringProperty
from bpy.types import PropertyGroup

from .constants import UI_CONTEXT
from .tool_registry import TOOLS


class QDSettings(PropertyGroup):
    """每个工具拥有独立参数组; 旧预设名字段为 CellFracture 保留。"""

    __annotations__ = {
        tool.settings_attr: PointerProperty(type=tool.settings_class)
        for tool in TOOLS
        if tool.settings_class is not None
    }
    preset_name: StringProperty(name="Preset", translation_context=UI_CONTEXT)
