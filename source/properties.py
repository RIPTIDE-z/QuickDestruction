"""场景设置聚合根 - 保留持久化数据路径"""

from bpy.props import PointerProperty, StringProperty
from bpy.types import PropertyGroup

from .CellFracture.constants import CONTEXT
from .CellFracture.properties import CellFractureSettings


class QDSettings(PropertyGroup):
    """挂在场景上的总设置, 目前只包含参数组和最近使用的预设名"""

    fracture: PointerProperty(type=CellFractureSettings)
    preset_name: StringProperty(name="Preset", translation_context=CONTEXT)
