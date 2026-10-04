"""骨架 - 在世界原点建骨架, 一根 root 加每块会动的碎块一根骨骼

骨骼头部放在碎块的静止质心, 沿 +Y 向的尾部只是为了让骨骼有长度, 滚转为 0
所以每根骨骼的 matrix_local 是纯平移: 骨骼空间的轴与世界轴平行, 姿态换算只需要平移部分
"""

from contextlib import contextmanager

import bpy
from mathutils import Vector

from .partition import ROOT_BONE

# 骨骼的最小长度, Blender 单位; 零长度的骨骼会在退出编辑模式时被删掉
MIN_BONE_LENGTH = 1e-4
# 骨骼长度占碎块最小尺寸的比例
BONE_LENGTH_RATIO = 0.25


@contextmanager
def edit_mode(context, obj):
    """让物体成为活动物体并进入编辑模式, 无论如何退出时回到物体模式; 选择与活动物体由调用方还原"""
    context.view_layer.objects.active = obj
    bpy.ops.object.mode_set(mode="EDIT")
    try:
        yield
    finally:
        if obj.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")


def bone_length(shard):
    """碎块对应的骨骼长度: 碎块最小尺寸的四分之一, 不小于下限"""
    return max(MIN_BONE_LENGTH, BONE_LENGTH_RATIO * min(shard.dimensions))


def make_armature(context, tracker, name, collection, bones, root_length):
    """建一个骨架物体并链接进 collection; bones 是 [(骨骼名, 头部位置, 长度), ...]

    返回骨架物体; 数据与物体都登记在创建账本里, 失败时由账本回收
    """
    data = tracker.track("armatures", bpy.data.armatures.new(name))
    obj = tracker.track("objects", bpy.data.objects.new(name, data))
    collection.objects.link(obj)
    with edit_mode(context, obj):
        root = data.edit_bones.new(ROOT_BONE)
        root.head = Vector((0.0, 0.0, 0.0))
        root.tail = Vector((0.0, root_length, 0.0))
        for bone_name, head, length in bones:
            bone = data.edit_bones.new(bone_name)
            bone.head = Vector(head)
            bone.tail = Vector(head) + Vector((0.0, length, 0.0))
            bone.parent = root
    return obj
