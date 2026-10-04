"""刚体世界 - 确保场景有刚体世界与成员集合, 判断点缓存是否已烘焙, 批量加入与移出刚体世界

还提供模拟步长的换算; Blender 只能通过把物体链接进刚体世界集合来建立刚体数据,
移出集合后刚体数据仍然保留
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
    """要求场景的刚体点缓存没有被烘焙, 否则抛出 RigidBodyError

    scene 要检查的场景
    """
    world = scene.rigidbody_world
    # 没有刚体世界时没有缓存可言, 直接放行
    if world is not None and world.point_cache.is_baked:
        raise RigidBodyError(BAKED_CACHE_MESSAGE)


def ensure_world(context, tracker):
    """确保场景有刚体世界与成员集合; 新建的集合登记到 tracker

    context Blender 上下文
    tracker 创建账本, 新建的成员集合登记在里面以便失败时回收
    返回 (刚体世界, 是否本次新建了世界)
    """
    scene = context.scene
    created = scene.rigidbody_world is None
    # 世界只能通过算子新建, 用 temp_override 指定场景, 不依赖当前窗口
    if created:
        with context.temp_override(scene=scene):
            bpy.ops.rigidbody.world_add()
    world = scene.rigidbody_world
    # 世界存在但没有成员集合 (例如被手动清空) 时补建
    if world.collection is None:
        world.collection = tracker.track(
            "collections", bpy.data.collections.new(WORLD_COLLECTION_NAME)
        )
    return world, created


def remove_world(context):
    """删除场景的刚体世界, 用于撤回本次新建的世界

    context Blender 上下文
    """
    with context.temp_override(scene=context.scene):
        bpy.ops.rigidbody.world_remove()


def in_world(world, obj):
    """物体当前是否是刚体世界的成员

    world 刚体世界, 可以为 None
    obj 要判断的物体
    返回 是成员为真
    """
    return (
        world is not None and world.collection is not None and obj.name in world.collection.objects
    )


def link(world, objects):
    """把物体加入刚体世界, 已在其中的跳过; 没有刚体数据的网格物体由 Blender 自动建立主动刚体

    world 刚体世界
    objects 要加入的物体序列
    """
    members = world.collection.objects
    for obj in objects:
        # 重复链接会抛错, 先判断成员关系
        if obj.name not in members:
            members.link(obj)


def unlink(world, objects):
    """把物体移出刚体世界, 不在其中的跳过; 刚体数据保留, 之后再加入时沿用原设置

    world 刚体世界
    objects 要移出的物体序列
    """
    members = world.collection.objects
    for obj in objects:
        # 重复取消链接会抛错, 先判断成员关系
        if obj.name in members:
            members.unlink(obj)


def remove_bodies(context, objects):
    """删除物体的刚体数据, 同时移出刚体世界; 一次算子调用处理全部物体

    context Blender 上下文
    objects 要删除刚体数据的物体序列, 没有刚体的物体被忽略
    """
    bodies = [obj for obj in objects if obj.rigid_body is not None]
    # 算子在没有目标时会报错, 空列表直接返回
    if not bodies:
        return
    # 算子读取选中物体与活动物体, 用 temp_override 提供, 不改动用户的选择
    with context.temp_override(
        selected_objects=bodies, selected_editable_objects=bodies, object=bodies[0]
    ):
        bpy.ops.rigidbody.objects_remove()


def mark_ground(obj):
    """把物体标记为预设地面

    obj 地面物体
    """
    obj[GROUND_MARK] = True


def ground_of(scene):
    """场景里的预设地面

    scene 要查找的场景
    返回 地面物体, 没有时为 None
    """
    return next((obj for obj in scene.objects if GROUND_MARK in obj), None)


def unit_scale(scene):
    """每个 Blender 单位对应的米数

    scene 场景
    返回 单位缩放, 场景里写成 0 时按 1 处理
    """
    return scene.unit_settings.scale_length or 1.0


def frame_seconds(scene, time_scale):
    """模拟推进一帧经过的模拟秒: 时间缩放乘以帧率基数再除以帧率

    scene 场景, 提供帧率与帧率基数
    time_scale 时间缩放, 小于 1 为慢动作
    返回 每帧经过的模拟秒
    """
    return time_scale * scene.render.fps_base / scene.render.fps
