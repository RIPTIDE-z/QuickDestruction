"""网格整理 - 求交结果的焊接, 清理与压平, 以及最终碎块的共面面合并

面合并只在全部切割结束后对最终碎块做一次, 原因见 doc/开发文档/Architecture.md 的 MeshFracture 一节
"""

import bmesh

from ....common.blender_context import mesh_edit


def dissolve_coplanar(bm, *, with_edges=False):
    """合并近似共面的小面; 胞体阶段只按顶点判断, 最终碎块还要按边判断

    bm 要整理的 BMesh
    with_edges 为真时同时按边判断
    """
    bm.normal_update()
    options = {"verts": list(bm.verts), "angle_limit": 0.001}
    if with_edges:
        options["edges"] = list(bm.edges)
    bmesh.ops.dissolve_limit(bm, **options)


def _flatten_faces(bm, tolerance):
    """把平面偏离超过容差的面三角化; 三角形必然共面, 只看四边形及以上的面

    bm 要整理的 BMesh
    tolerance 平面偏离容差, 世界单位
    """
    targets = []
    for face in bm.faces:
        # 三角形必然共面, 不必检查
        if len(face.verts) <= 3:
            continue
        normal = face.normal
        origin = face.verts[0].co
        # 退化面没有法线, 同样需要三角化
        if (
            normal.length == 0
            or max(abs((vert.co - origin).dot(normal)) for vert in face.verts) > tolerance
        ):
            targets.append(face)
    if targets:
        bmesh.ops.triangulate(bm, faces=targets)


def face_components(bm):
    """按共享边把面分成连通块; 只在顶点处相接的部分分属不同的块

    bm 要分块的 BMesh
    返回 面列表的列表, 每个列表是一个连通块
    """
    seen = set()
    parts = []
    for face in bm.faces:
        if face in seen:
            continue
        seen.add(face)
        # 深度优先: 沿共享边扩散, 访问过的面不再入栈
        stack, part = [face], []
        while stack:
            current = stack.pop()
            part.append(current)
            for edge in current.edges:
                others = [other for other in edge.link_faces if other not in seen]
                seen.update(others)
                stack.extend(others)
        parts.append(part)
    return parts


def drop_loose(bm):
    """删除没有面的散边与散点, 否则拆分孤岛会把它们拆成没有面的空碎块

    bm 要整理的 BMesh
    """
    loose = [edge for edge in bm.edges if not edge.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="EDGES")
    lonely = [vert for vert in bm.verts if not vert.link_edges]
    if lonely:
        bmesh.ops.delete(bm, geom=lonely, context="VERTS")


def _disconnect_components(bm):
    """把只在顶点处相接的几部分断开, 让拆分孤岛按共享边判断连通

    这样的几部分留在同一物体里时, 下一轮以它为源的 Exact 求交会判错内外, 丢掉大片几何
    bm 要整理的 BMesh
    返回 连通块数
    """
    parts = face_components(bm)
    # 只有一块: 没有需要断开的部分
    if len(parts) < 2:
        return len(parts)
    # 最大的一块保留原顶点, 其余各块复制共享顶点后断开
    parts.sort(key=len)
    for part in parts[:-1]:
        bmesh.ops.split(bm, geom=part, use_only_faces=True)
    return len(parts)


def weld_intersection(mesh, weld_dist):
    """整理求交结果供递归使用: 焊接重合顶点, 清掉散边散点, 断开点接触的部分, 再三角化非平面面

    mesh 求交结果的网格数据, 原地修改
    weld_dist 焊接距离
    返回 互不连通的部分数, 拆分孤岛据此只处理真正含多个孤岛的碎块
    """
    with mesh_edit(mesh) as bm:
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_dist)
        drop_loose(bm)
        parts = _disconnect_components(bm)
        # 焊接与清理会改动拓扑, 重新算法线后才能判断面是否共面
        bm.normal_update()
        _flatten_faces(bm, weld_dist)
        bm.to_mesh(mesh)
    return parts


def dissolve_final_shards(cells):
    """对最终碎块合并近共面面, 只在全部切割结束后调用一次

    cells 最终碎块物体
    """
    for cell in cells:
        with mesh_edit(cell.data) as bm:
            dissolve_coplanar(bm, with_edges=True)
            bm.to_mesh(cell.data)
        cell.data.update()
