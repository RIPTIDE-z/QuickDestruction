"""壳体操作数 - 源网格含嵌套的封闭曲面时, 为布尔准备一个把内腔曲面翻向朝内的临时副本

空心壳常由外层与内层两张封闭曲面组成, 且两张面的法线都朝外
Exact 布尔按绕数判断内外, 两层都朝外时内腔的绕数为 2, 会被当成实体填满
只有内层朝内, 内腔的绕数才回到 0, 布尔得到的才是外层与内层之间的壳层
所以这里按嵌套深度把深度为奇数的封闭曲面翻向朝内, 偶数的保持朝外, 源物体本身不改动
"""

import bmesh
import bpy

from ....common.hollow import reversed_faces
from .shard_objects import discard_object

# 临时操作数的物体名前缀之后跟切割期临时名, 由回收账本负责回收
OPERAND_LABEL = "operand"


def hollow_operand(context, obj, depsgraph, batch):
    """为布尔准备操作数: 源物体含嵌套曲面且朝向不符时返回修正后的临时副本, 否则就是源物体本身

    副本取自求值后的网格, 修改器栈的结果也一并带入; 副本链接在场景根集合里, 才会进入依赖图
    它与它的网格都登记在回收账本里, 失败时随回滚删除, 成功时由调用方用 release_operand 丢弃
    context Blender 上下文
    obj 被切的物体
    depsgraph 求值用的依赖图
    batch ShardBatch, 提供回收账本与临时名
    返回 操作数物体
    """
    evaluated = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        flipped = reversed_faces(bm)
        if not flipped:
            bpy.data.meshes.remove(mesh)
            return obj
        bmesh.ops.reverse_faces(bm, faces=flipped)
        # 源面的隐藏标记不能带进操作数: 布尔结果里仍然隐藏的面才被当成断面
        for face in bm.faces:
            face.hide = False
        bm.to_mesh(mesh)
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    finally:
        bm.free()
    tracker = batch.naming.tracker
    tracker.track("meshes", mesh)
    name = f"{batch.naming.temp_name()}_{OPERAND_LABEL}"
    mesh.name = name
    operand = tracker.track("objects", bpy.data.objects.new(name, mesh))
    operand.matrix_world = evaluated.matrix_world.copy()
    context.scene.collection.objects.link(operand)
    return operand


def release_operand(tracker, obj, operand):
    """布尔取回结果后丢弃临时副本; 操作数就是源物体本身时什么也不做

    tracker 回收账本
    obj 被切的物体
    operand hollow_operand 返回的操作数
    """
    if operand is not obj:
        discard_object(tracker, operand)
