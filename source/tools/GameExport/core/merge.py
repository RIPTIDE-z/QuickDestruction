"""蒙皮网格 - 把一组碎块合并成一个蒙皮网格

复制碎块网格并变换到静止世界姿态, 加顶点组与来源属性, 再 join

顶点组与骨骼同名, 权重 1; 不动的碎块归 root 组, 原物体切换时归 still 组
来源属性记录每个顶点属于哪块碎块, 检查与测试靠它把蒙皮网格逐块对回碎块
合并用受控的 object.join: 只在构建集合里的临时复制品上执行, 保留全部属性, 材质与 UV
"""

from dataclasses import dataclass

import bpy

from ..constants import FACE_SHARD_ATTRIBUTE, SHARD_ATTRIBUTE, VERTEX_ATTRIBUTE


@dataclass(frozen=True)
class Entry:
    """合并进网格的一块碎块"""

    # 碎块物体
    shard: object
    # 碎块的静止世界矩阵
    rest: object
    # 顶点组名: 骨骼名或 root
    group: str
    # 碎块在资产里的序号, 写进来源属性
    ordinal: int


def _copy_shard(tracker, collection, entry):
    """复制碎块网格并变换到静止世界姿态; 顶点组与来源属性在这里写好

    tracker 创建账本
    collection 临时物体链接进的集合
    entry 要复制的碎块
    返回 临时物体
    """
    # 复制网格数据而不是引用: 变换只作用在副本上, 原碎块不受影响
    mesh = tracker.track("meshes", entry.shard.data.copy())
    mesh.transform(entry.rest)
    obj = tracker.track("objects", bpy.data.objects.new(f"copy_{entry.ordinal}", mesh))
    collection.objects.link(obj)
    count = len(mesh.vertices)
    # 整块碎块的全部顶点权重 1 归同一个顶点组 (骨骼名)
    group = obj.vertex_groups.new(name=entry.group)
    group.add(list(range(count)), 1.0, "REPLACE")
    # 来源属性: 点域记碎块序号与原顶点序号, 面域记碎块序号
    for name, domain, values in (
        (SHARD_ATTRIBUTE, "POINT", [entry.ordinal] * count),
        (VERTEX_ATTRIBUTE, "POINT", list(range(count))),
        (FACE_SHARD_ATTRIBUTE, "FACE", [entry.ordinal] * len(mesh.polygons)),
    ):
        attribute = mesh.attributes.new(name, "INT", domain)
        attribute.data.foreach_set("value", values)
    return obj


def _join(context, objects):
    """把临时物体合并进第一个, 其余的随 join 被删除

    context Blender 上下文
    objects 临时物体列表, 至少一个
    返回 合并后的物体, 即第一个
    """
    first = objects[0]
    # 只有一个物体时无需合并
    if len(objects) > 1:
        # 算子读取选中物体与活动物体, 用 temp_override 提供, 不改动用户的选择
        with context.temp_override(
            active_object=first,
            object=first,
            selected_objects=objects,
            selected_editable_objects=objects,
        ):
            bpy.ops.object.join()
    return first


def build_mesh(context, tracker, name, collection, entries, armature):
    """把 entries 合并成一个蒙皮网格物体并绑到骨架上

    entries 为空时返回只有一个 root 顶点组的空网格, 导出仍然是合法的骨架加网格
    context Blender 上下文
    tracker 创建账本
    name 网格物体与数据的名字
    collection 物体链接进的集合
    entries 要合并的碎块 Entry 序列
    armature 骨架物体, 网格以它为父级并加骨架修改器
    返回 蒙皮网格物体
    """
    # 没有碎块时给一个空网格, 保证每个骨架至少有一段网格
    if not entries:
        mesh = tracker.track("meshes", bpy.data.meshes.new(name))
        obj = tracker.track("objects", bpy.data.objects.new(name, mesh))
        collection.objects.link(obj)
        obj.vertex_groups.new(name="root")
    else:
        copies = [_copy_shard(tracker, collection, entry) for entry in entries]
        # join 之后只有第一个物体与它的网格还在; 其余网格成了孤儿, 攒着一起删
        orphans = [copy.data for copy in copies[1:]]
        obj = _join(context, copies)
        tracker.bury(*orphans)
        tracker.flush()
        obj.name = name
        obj.data.name = name
    bind(obj, armature)
    return obj


def bind(obj, armature):
    """把网格物体绑到骨架上

    obj 网格物体, 顶点组与骨骼同名
    armature 骨架物体
    """
    # 骨架修改器让顶点跟着骨骼动, 父级关系让导出时网格归属于这个骨架
    modifier = obj.modifiers.new("Armature", "ARMATURE")
    modifier.object = armature
    obj.parent = armature
