"""网格整理 - 求交结果的焊接, 清理与压平, 以及最终碎块的共面面合并

面合并只在全部切割结束后对最终碎块做一次, 原因见 doc/开发文档/Architecture.md 的 MeshFracture 一节
"""

import bmesh

from ....common.blender_context import mesh_edit


def dissolve_coplanar(bm, *, with_edges=False):
    """合并近似共面的小面; 胞体阶段只按顶点判断, 最终碎块还要按边判断"""
    bm.normal_update()
    options = {"verts": list(bm.verts), "angle_limit": 0.001}
    if with_edges:
        options["edges"] = list(bm.edges)
    bmesh.ops.dissolve_limit(bm, **options)


def _flatten_faces(bm, tolerance):
    """把平面偏离超过容差的面三角化; 三角形必然共面, 只看四边形及以上的面"""
    targets = []
    for face in bm.faces:
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
    """按共享边把面分成连通块; 只在顶点处相接的部分分属不同的块"""
    seen = set()
    parts = []
    for face in bm.faces:
        if face in seen:
            continue
        seen.add(face)
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


def _drop_loose(bm):
    """删除没有面的散边与散点, 否则拆分孤岛会把它们拆成没有面的空碎块"""
    loose = [edge for edge in bm.edges if not edge.link_faces]
    if loose:
        bmesh.ops.delete(bm, geom=loose, context="EDGES")
    lonely = [vert for vert in bm.verts if not vert.link_edges]
    if lonely:
        bmesh.ops.delete(bm, geom=lonely, context="VERTS")


def _disconnect_components(bm):
    """把只在顶点处相接的几部分断开, 让拆分孤岛按共享边判断连通, 返回连通块数

    这样的几部分留在同一物体里时, 下一轮以它为源的 Exact 求交会判错内外, 丢掉大片几何
    """
    parts = face_components(bm)
    if len(parts) < 2:
        return len(parts)
    # 最大的一块保留原顶点, 其余各块复制共享顶点后断开
    parts.sort(key=len)
    for part in parts[:-1]:
        bmesh.ops.split(bm, geom=part, use_only_faces=True)
    return len(parts)


def weld_intersection(mesh, weld_dist):
    """整理求交结果供递归使用: 焊接重合顶点, 清掉散边散点, 断开点接触的部分, 再三角化非平面面

    返回互不连通的部分数, 拆分孤岛据此只处理真正含多个孤岛的碎块
    """
    with mesh_edit(mesh) as bm:
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_dist)
        _drop_loose(bm)
        parts = _disconnect_components(bm)
        # 焊接与清理会改动拓扑, 重新算法线后才能判断面是否共面
        bm.normal_update()
        _flatten_faces(bm, weld_dist)
        bm.to_mesh(mesh)
    return parts


def dissolve_final_shards(cells):
    """对最终碎块合并近共面面, 只在全部切割结束后调用一次"""
    for cell in cells:
        with mesh_edit(cell.data) as bm:
            dissolve_coplanar(bm, with_edges=True)
            bm.to_mesh(cell.data)
        cell.data.update()
