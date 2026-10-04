"""刚体世界 - 确保场景有刚体世界与成员集合, 判断点缓存是否已烘焙, 批量加入与移出刚体世界, 换算模拟步长

Blender 只能通过把物体链接进刚体世界集合来建立刚体数据; 移出集合后刚体数据仍然保留
物理设置用它建立刚体数据, 模拟烘焙用它管理会话期间的世界成员
"""

import bpy

from .errors import DomainError

# 点缓存已烘焙时的消息: 已烘焙的缓存会忽略之后对刚体的修改
BAKED_CACHE_MESSAGE = "Free the rigid body cache bake first"
# 预设地面物体上的标记: 一个场景一块, 再次添加时重新对齐而不是再建一块
GROUND_MARK = "_qd_ground"
# 新建的刚体世界成员集合名, 与 Blender 自己新建时的名字一致
WORLD_COLLECTION_NAME = "RigidBodyWorld"


class RigidBodyError(DomainError):
    """刚体世界状态不允许继续 - 消息按框架上下文翻译"""


def check_cache(scene):
    """场景的刚体点缓存已经烘焙时报错"""
    world = scene.rigidbody_world
    if world is not None and world.point_cache.is_baked:
        raise RigidBodyError(BAKED_CACHE_MESSAGE)


def ensure_world(context, tracker):
    """确保场景有刚体世界与成员集合, 返回 (刚体世界, 是否本次新建了世界); 新建的集合登记到 tracker"""
    scene = context.scene
    created = scene.rigidbody_world is None
    if created:
        with context.temp_override(scene=scene):
            bpy.ops.rigidbody.world_add()
    world = scene.rigidbody_world
    if world.collection is None:
        world.collection = tracker.track(
            "collections", bpy.data.collections.new(WORLD_COLLECTION_NAME)
        )
    return world, created


def remove_world(context):
    """删除场景的刚体世界, 用于撤回本次新建的世界"""
    with context.temp_override(scene=context.scene):
        bpy.ops.rigidbody.world_remove()


def in_world(world, obj):
    """物体当前是否是刚体世界的成员"""
    return (
        world is not None and world.collection is not None and obj.name in world.collection.objects
    )


def link(world, objects):
    """把物体加入刚体世界, 已在其中的跳过; 没有刚体数据的网格物体由 Blender 自动建立主动刚体"""
    members = world.collection.objects
    for obj in objects:
        if obj.name not in members:
            members.link(obj)


def unlink(world, objects):
    """把物体移出刚体世界, 不在其中的跳过; 刚体数据保留, 之后再加入时沿用原设置"""
    members = world.collection.objects
    for obj in objects:
        if obj.name in members:
            members.unlink(obj)


def remove_bodies(context, objects):
    """删除物体的刚体数据, 同时移出刚体世界; 一次算子调用处理全部物体"""
    bodies = [obj for obj in objects if obj.rigid_body is not None]
    if not bodies:
        return
    with context.temp_override(
        selected_objects=bodies, selected_editable_objects=bodies, object=bodies[0]
    ):
        bpy.ops.rigidbody.objects_remove()


def mark_ground(obj):
    """把物体标记为预设地面"""
    obj[GROUND_MARK] = True


def ground_of(scene):
    """场景里的预设地面, 没有时为 None"""
    return next((obj for obj in scene.objects if GROUND_MARK in obj), None)


def unit_scale(scene):
    """每个 Blender 单位对应的米数"""
    return scene.unit_settings.scale_length or 1.0


def frame_seconds(scene, time_scale):
    """模拟推进一帧经过的模拟秒: 时间缩放乘以帧率基数再除以帧率"""
    return time_scale * scene.render.fps_base / scene.render.fps
