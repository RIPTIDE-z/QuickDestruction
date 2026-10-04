"""网格几何量 - 封闭性判定, 带符号体积与体积质心, 凸包体积

网格破碎的求解策略与物理设置的质量计算共用
"""

import bmesh
from mathutils import Vector

# 体积相对包围盒立方的下限: 低于它视为退化, 质心不可信
DEGENERATE_RATIO = 1e-9


def is_not_closed(bm):
    """网格不封闭: 有开放边, 多面共边, 或仅顶点处相接的非流形

    布尔求解策略, 用户提示与质量计算共用这一个判定
    bm 要判定的 BMesh
    返回 不封闭为真
    """
    return any(not edge.is_manifold for edge in bm.edges) or any(
        not vert.is_manifold for vert in bm.verts
    )


def _extent(bm):
    """顶点包围盒的最大边长, 用作退化判定的尺度

    bm 要测量的 BMesh
    返回 三个轴向里最长的边长, 没有顶点时为 0
    """
    # 没有顶点时 max 会抛错, 先返回零
    if not bm.verts:
        return 0.0
    coords = [vert.co for vert in bm.verts]
    return max(max(co[axis] for co in coords) - min(co[axis] for co in coords) for axis in range(3))


def volume_centroid(bm):
    """带符号体积与体积质心, 坐标系与 bm 相同; 体积相对尺度接近零时质心为 None

    每个三角面与原点构成一个四面体, 累加它们的带符号体积与一阶矩; 网格整体绕序反向时体积为负,
    但质心是两者之比, 结果不变
    bm 要计算的 BMesh, 应当是封闭网格
    返回 (带符号体积, 体积质心 或 None)
    """
    total = 0.0
    moment = Vector()
    for loop_a, loop_b, loop_c in bm.calc_loop_triangles():
        a, b, c = loop_a.vert.co, loop_b.vert.co, loop_c.vert.co
        # 三个顶点与原点围成四面体, 体积等于标量三重积除以 6
        volume = a.dot(b.cross(c)) / 6.0
        total += volume
        # 四面体 (原点, a, b, c) 的质心是 (a + b + c) / 4
        moment += (a + b + c) * (volume / 4.0)
    # 体积相对包围盒立方过小时, 比值被噪声主导, 质心不可信
    if abs(total) <= DEGENERATE_RATIO * _extent(bm) ** 3:
        return total, None
    return total, moment / total


def vertex_mean(bm):
    """顶点平均位置, 退化网格的质心退路

    bm 要计算的 BMesh
    返回 顶点坐标的算术平均, 没有顶点时为原点
    """
    if not bm.verts:
        return Vector()
    return sum((vert.co for vert in bm.verts), Vector()) / len(bm.verts)


def convex_hull_volume(bm):
    """顶点凸包的体积, 不改动 bm; 网格不封闭时用它近似体积

    bm 要计算的 BMesh
    返回 凸包体积, 顶点少于 4 个时为 0
    """
    # 在一个临时 BMesh 里建凸包, 避免改动调用方的网格
    hull = bmesh.new()
    try:
        for vert in bm.verts:
            hull.verts.new(vert.co)
        # 少于 4 个点不能围成体
        if len(hull.verts) < 4:
            return 0.0
        bmesh.ops.convex_hull(hull, input=list(hull.verts))
        return abs(hull.calc_volume(signed=True))
    finally:
        hull.free()
