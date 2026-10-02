"""阶段入口 - 新建驱动物体, 或把已有的空物体设为驱动; 参数经驱动规格校验后写入"""

import bpy

from ...common import drivers
from .constants import DISPLAY_TYPES, DRIVER_COLLECTION, OBJECT_NAMES
from .errors import DriverError


def driver_collection(scene):
    """场景里放驱动物体的集合, 没有时新建并链接到场景根集合"""
    collection = bpy.data.collections.get(DRIVER_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(DRIVER_COLLECTION)
    if collection.name not in scene.collection.children:
        scene.collection.children.link(collection)
    return collection


def default_frame(scene):
    """新驱动的起动帧: 当前帧, 但不早于场景起始帧之后的第一帧"""
    return max(scene.frame_start + 1, scene.frame_current)


def add_driver(context, role, *, location=None, **params):
    """在 location 处新建一个驱动空物体, 默认放在 3D 游标处; params 是驱动参数, 经规格校验后写入"""
    if role not in DISPLAY_TYPES:
        raise DriverError("Unknown driver type")
    scene = context.scene
    obj = bpy.data.objects.new(OBJECT_NAMES[role], None)
    obj.empty_display_type = DISPLAY_TYPES[role]
    obj.empty_display_size = 1.0
    obj.location = scene.cursor.location if location is None else location
    driver_collection(scene).objects.link(obj)
    values = {"role": role, "frame": default_frame(scene), **params}
    drivers.DRIVER_SCHEMA.write(getattr(obj, drivers.ATTRIBUTE), values)
    return obj


def make_driver(obj, role):
    """把已有的空物体设为驱动, 显示类型改成与角色一致, 其余参数保持不变"""
    if obj is None or obj.type != "EMPTY":
        raise DriverError("Select an empty to use as a driver")
    if role not in DISPLAY_TYPES:
        raise DriverError("Unknown driver type")
    obj.empty_display_type = DISPLAY_TYPES[role]
    drivers.DRIVER_SCHEMA.write(getattr(obj, drivers.ATTRIBUTE), {"role": role})
    return obj
