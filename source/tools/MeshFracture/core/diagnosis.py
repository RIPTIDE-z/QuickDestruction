"""源网格体检 - 切割前检查源网格是否封闭, 是否自相交; 结果只用于提示用户, 不阻止切割"""

import bmesh
from mathutils.bvhtree import BVHTree

# 有开放边或多面共边: 网格没有明确的内外, 碎块可能出现缺口或开口
NOT_CLOSED = "NOT_CLOSED"
# 有互相穿插的面: 布尔求交可能丢失或重复几何
SELF_INTERSECTING = "SELF_INTERSECTING"


def _self_intersecting(bm):
    """用 BVH 找互相重叠的面对; 共享顶点的相邻面会被误报为重叠, 因此排除"""
    bm.faces.ensure_lookup_table()
    tree = BVHTree.FromBMesh(bm)
    faces = bm.faces
    return any(not set(faces[a].verts) & set(faces[b].verts) for a, b in tree.overlap(tree))


def mesh_issues(obj, depsgraph):
    """按求值后的网格检查一个源物体, 与切割使用的几何一致; 返回问题集合, 没有问题时为空"""
    evaluated = obj.evaluated_get(depsgraph)
    bm = bmesh.new()
    try:
        # from_mesh 复制了网格数据, 临时求值网格随即释放
        bm.from_mesh(evaluated.to_mesh())
        evaluated.to_mesh_clear()
        issues = set()
        if any(not edge.is_manifold for edge in bm.edges):
            issues.add(NOT_CLOSED)
        if _self_intersecting(bm):
            issues.add(SELF_INTERSECTING)
        return frozenset(issues)
    finally:
        bm.free()
