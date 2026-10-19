"""断面材质 - 断面材质索引指向源物体最后一个槽之后的那个槽时, 碎块自动多一个断面材质槽

新槽里放 <源物体第一个材质名>_Interior; 同名材质已经存在时直接复用, 重复切割不会生成 .001
只改碎块的网格, 不改源物体: 源物体的材质槽保持原样
"""

import bpy

from ..errors import MeshFractureError

# 自动建立的断面材质名后缀
INTERIOR_SUFFIX = "_Interior"
# 新建断面材质的占位颜色: 偏暖的浅灰, 一眼能和外表面区分, 引擎里换成真正的断面材质
PLACEHOLDER_COLOR = (0.8, 0.75, 0.65, 1.0)
# 新建断面材质的粗糙度
PLACEHOLDER_ROUGHNESS = 0.8
# 断面材质槽不存在时的消息
MISSING_SLOT_MESSAGE = (
    'Material slot {index} does not exist on "{name}"; use one of its slots, '
    "or slot {count} to create an interior material automatically"
)


def check_slot(obj, index):
    """断面材质索引是否可用: 0 总是可用, 其余要么是已有的槽, 要么正好是最后一个槽之后的那个

    obj 源物体
    index 断面材质索引
    不可用时抛 MeshFractureError
    """
    count = len(obj.material_slots)
    # 没有任何槽时 0 号之后的槽也不自动建: 外表面没有材质, 断面单独有材质没有意义
    if index > 0 and (index > count or count == 0):
        raise MeshFractureError(MISSING_SLOT_MESSAGE, index=index, name=obj.name, count=count)


def needs_interior_slot(slot_count, index):
    """断面材质索引是否指向要自动新建的那个槽

    slot_count 已有的材质槽数
    index 断面材质索引
    返回 需要新建为真
    """
    return index >= 1 and index == slot_count


def interior_material(obj, tracker):
    """取得或新建 obj 的断面材质: 名字是第一个有材质的槽的材质名加后缀, 没有材质时用物体名

    obj 源物体
    tracker 回收账本, 新建的材质登记在里面, 失败时没有使用者就删除
    返回 材质
    """
    base = next((slot.material.name for slot in obj.material_slots if slot.material), obj.name)
    name = f"{base}{INTERIOR_SUFFIX}"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing
    material = tracker.track("materials", bpy.data.materials.new(name))
    material.diffuse_color = PLACEHOLDER_COLOR
    material.roughness = PLACEHOLDER_ROUGHNESS
    # 渲染用的节点也设成同样的颜色; 没有节点树的版本只用视口颜色
    if material.node_tree is None and hasattr(material, "use_nodes"):
        material.use_nodes = True
    if material.node_tree is not None:
        for node in material.node_tree.nodes:
            if node.type == "BSDF_PRINCIPLED":
                node.inputs["Base Color"].default_value = PLACEHOLDER_COLOR
                node.inputs["Roughness"].default_value = PLACEHOLDER_ROUGHNESS
    return material


def append_interior_slot(mesh, obj, index, tracker):
    """碎块网格的材质槽刚从源物体复制过来时, 按需在末尾加上断面材质

    mesh 碎块或胞体的网格, 材质槽已与源物体一致
    obj 源物体
    index 断面材质索引
    tracker 回收账本
    """
    if needs_interior_slot(len(mesh.materials), index):
        mesh.materials.append(interior_material(obj, tracker))
