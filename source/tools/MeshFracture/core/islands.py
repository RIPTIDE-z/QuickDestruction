"""孤岛拆分 - 把含多个互不连通部分的碎块拆成独立物体, 并按几何排序保证可复现

只用数据 API: 每个多出来的孤岛是原碎块的一份副本, 只保留属于它的面
不用 bpy.ops.mesh.separate: 算子每次调用都有固定开销并会触发依赖图更新, 碎块多时是最大的剩余耗时
"""

import bmesh

from ....common.blender_context import mesh_edit, vertex_coordinates
from . import cleanup


def _island_sort_key(obj):
    """孤岛结果的稳定排序键: 只用几何本身, 位置优先, 再比包围盒与顶点数

    obj 碎块物体
    返回 可比较的元组 (最小角, 最大角, 顶点数), 坐标取 6 位小数
    """
    flat = vertex_coordinates(obj.data)
    # 没有顶点的碎块没有几何可比, 排在最前
    if not flat:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0)
    axes = (flat[0::3], flat[1::3], flat[2::3])
    return (
        tuple(round(min(axis), 6) for axis in axes),
        tuple(round(max(axis), 6) for axis in axes),
        len(flat) // 3,
    )


def keep_faces(mesh, keep):
    """只保留下标在 keep 里的面, 其余的面连同只被它们使用的边与顶点一起删除

    mesh 网格数据, 原地修改
    keep 要保留的面下标集合
    """
    with mesh_edit(mesh) as bm:
        bm.faces.ensure_lookup_table()
        bmesh.ops.delete(
            bm, geom=[face for face in bm.faces if face.index not in keep], context="FACES"
        )
        bm.to_mesh(mesh)


def _split_cell(cell, tracker):
    """把一个碎块的每个孤岛拆成独立物体

    cell 含多个孤岛的碎块
    tracker 回收账本
    返回 全部孤岛物体, 第一个是原物体
    """
    with mesh_edit(cell.data) as bm:
        groups = [{face.index for face in part} for part in cleanup.face_components(bm)]
    pieces = [cell]
    # 先从未改动的原网格复制出其余孤岛, 最后才削减原物体自己的网格
    for keep in groups[1:]:
        mesh = tracker.track("meshes", cell.data.copy())
        keep_faces(mesh, keep)
        # 物体副本带走变换, 材质槽链接方式与所在集合, 只换网格
        piece = tracker.track("objects", cell.copy())
        piece.data = mesh
        for collection in cell.users_collection:
            collection.objects.link(piece)
        pieces.append(piece)
    keep_faces(cell.data, groups[0])
    return pieces


def split_islands(cells, multi, tracker):
    """把 multi 里的碎块按孤岛拆开

    cells 全部碎块
    multi 已知含多个孤岛的碎块集合, 其余碎块不检查
    tracker 回收账本
    返回 全部碎块, 按几何排序保证可复现
    """
    pieces = []
    for cell in cells:
        pieces.extend(_split_cell(cell, tracker) if cell in multi else [cell])
    return sorted(pieces, key=_island_sort_key)
