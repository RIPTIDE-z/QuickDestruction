"""孤岛拆分 - 把每个碎块里互不连通的几何拆成独立物体, 并按几何排序保证可复现"""

import uuid

import bpy

from ....common.blender_context import select_only, vertex_coordinates

# 孤岛拆分时认领结果用的自定义属性名, 拆分结束即删除
_SPLIT_TOKEN = "_qd_split_owner"


def _island_sort_key(obj):
    """孤岛结果的稳定排序键: 只用几何本身, 位置优先, 再比包围盒与顶点数"""
    flat = vertex_coordinates(obj.data)
    if not flat:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0)
    axes = (flat[0::3], flat[1::3], flat[2::3])
    return (
        tuple(round(min(axis), 6) for axis in axes),
        tuple(round(max(axis), 6) for axis in axes),
        len(flat) // 3,
    )


def split_islands(context, cells, batch):
    """按不连通块拆分并认领结果, 按几何排序后返回; separate 的产出顺序不定, 排序保证可复现"""
    # separate 在物体模式下作用于选中的物体, 因此只选中本批碎块
    select_only(context, cells, cells[0])
    # 物体副本会连自定义属性一起复制, 用一次性令牌认领本次拆分的结果
    token = uuid.uuid4().hex
    for cell in cells:
        cell[_SPLIT_TOKEN] = token
    try:
        with context.temp_override(
            object=cells[0],
            active_object=cells[0],
            selected_objects=cells,
            selected_editable_objects=cells,
        ):
            bpy.ops.mesh.separate(type="LOOSE")
    finally:
        # separate 会选中拆出的物体, 只扫描选中物体即可, 不随碎块总数变慢; 抛错时也要完成认领与清理
        selected = context.view_layer.objects.selected
        claimed = [item for item in selected if item.get(_SPLIT_TOKEN) == token]
        for item in claimed:
            batch.naming.tracker.track("objects", item)
            batch.naming.tracker.track("meshes", item.data)
            del item[_SPLIT_TOKEN]
    return sorted(claimed, key=_island_sort_key)
