"""孤岛拆分 - 把含多个互不连通部分的碎块拆成独立物体, 并按几何排序保证可复现

只用数据 API: 每个多出来的孤岛是原碎块的一份副本, 只保留属于它的面
不用 bpy.ops.mesh.separate: 算子每次调用都有固定开销并会触发依赖图更新, 碎块多时是最大的剩余耗时
"""

from itertools import compress

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


def face_labels(mesh):
    """按共享边把面分成连通块, 只在顶点处相接的部分分属不同的块

    不建 BMesh: 用并查集直接扫描面角的边下标, 总代价与面角数成正比
    mesh 网格数据, 不改动
    返回 (每个面所属块序号的列表, 块数); 块按最小面下标从小到大编号, 顺序可复现
    """
    count = len(mesh.polygons)
    totals = [0] * count
    mesh.polygons.foreach_get("loop_total", totals)
    edges = [0] * len(mesh.loops)
    mesh.loops.foreach_get("edge_index", edges)
    # 并查集: 根总是块里最小的面下标
    parent = list(range(count))
    # 每条边第一次出现时所在的面
    first = [-1] * len(mesh.edges)
    cursor = 0
    for face, total in enumerate(totals):
        for edge in edges[cursor : cursor + total]:
            other = first[edge]
            if other < 0:
                first[edge] = face
                continue
            # 两个面共用这条边: 找到各自的根并合并, 路径减半让树保持扁平
            low, high = face, other
            while parent[low] != low:
                parent[low] = parent[parent[low]]
                low = parent[low]
            while parent[high] != high:
                parent[high] = parent[parent[high]]
                high = parent[high]
            if low < high:
                parent[high] = low
            elif high < low:
                parent[low] = high
        cursor += total
    # 面按下标顺序访问, 根第一次出现的次序就是最小面下标的次序
    numbers = {}
    labels = [0] * count
    for face in range(count):
        root = face
        while parent[root] != root:
            parent[root] = parent[parent[root]]
            root = parent[root]
        labels[face] = numbers.setdefault(root, len(numbers))
    return labels, len(numbers)


def _has_loose(mesh):
    """网格是否有不属于任何面的边或点

    比较被面用到的边与点的个数和网格里的总数, 不建 BMesh
    mesh 网格数据
    返回 有游离几何为真
    """
    edges = [0] * len(mesh.loops)
    mesh.loops.foreach_get("edge_index", edges)
    verts = [0] * len(mesh.loops)
    mesh.loops.foreach_get("vertex_index", verts)
    return len(set(edges)) < len(mesh.edges) or len(set(verts)) < len(mesh.vertices)


def _delete_faces(bm, mask):
    """删除掩码为真的面, 只被它们使用的边与顶点一起删除

    bm 要修改的 BMesh
    mask 与 bm.faces 同序的布尔列表
    """
    doomed = list(compress(bm.faces, mask))
    if doomed:
        bmesh.ops.delete(bm, geom=doomed, context="FACES")


def _halve(bm, labels, low, high, emit):
    """把含有块 [low, high) 全部面的 BMesh 递归对半拆开, 每个叶子只含一个块的面

    每层只复制一次当前的 BMesh, 另一半直接在原 BMesh 里删面, 总代价约为面数乘以块数的对数,
    而不是每个块复制一遍整个网格
    bm 面的次序与 labels 一一对应的 BMesh; 交给本函数后由它释放, 中途出错也会释放
    labels 与 bm.faces 同序的块序号列表
    low 最小的块序号
    high 最大的块序号加一
    emit 回调 emit(块序号, 只含该块面的 BMesh), 回调返回后 BMesh 即被释放, 不能保留它
    """
    try:
        while high - low > 1:
            middle = (low + high) // 2
            in_left = [label < middle for label in labels]
            in_right = [not flag for flag in in_left]
            # 左半从副本里删掉右半的面, 副本交给递归释放
            left = bm.copy()
            try:
                _delete_faces(left, in_right)
            except Exception:
                left.free()
                raise
            _halve(left, list(compress(labels, in_left)), low, middle, emit)
            # 右半直接在原 BMesh 里删掉左半的面, 继续循环
            _delete_faces(bm, in_left)
            labels = list(compress(labels, in_right))
            low = middle
        emit(low, bm)
    finally:
        bm.free()


def split_by_labels(mesh, labels, count, emit):
    """按块序号把网格拆开, 每块只含自己的面

    mesh 网格数据, 不改动
    labels 每个面所属块序号的列表
    count 块数
    emit 回调 emit(块序号, 只含该块面的 BMesh), 回调返回后 BMesh 即被释放
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        # 游离的边与点不属于任何面, 不清掉的话会留在每一块里
        if _has_loose(mesh):
            cleanup.drop_loose(bm)
    except Exception:
        # 读入失败时还没有交给 _halve, 自己释放
        bm.free()
        raise
    _halve(bm, labels, 0, count, emit)


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
