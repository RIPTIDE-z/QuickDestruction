"""预切割持久化参数组"""
from bpy.types import PropertyGroup
from .settings import property_definitions

class CellFractureSettings(PropertyGroup):
    """与算子字段逐一对应的参数组, 用于侧栏常驻保存"""

    __annotations__ = property_definitions()
