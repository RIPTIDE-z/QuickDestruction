"""胞体构造 - 从包围盒与中垂面出发做半空间求交, 为每个种子点生成有界 Voronoi 凸体"""

from math import sqrt

from mathutils import Vector
from mathutils.geometry import points_in_planes

# 初始包围盒向外扩出的距离, 保证只有一个种子点时胞体也有界
BOUNDS_MARGIN = 0.05
# points_in_planes 默认的共面容差与相交容差
EPSILON_COPLANAR = 1e-4
EPSILON_ISECT = 1e-6


def _cell_vertices(center, points, base_planes, scale, margin, epsilon):
    """一个种子点的胞体顶点, 顶点坐标相对种子点; scale 为 None 表示三轴等权"""
    # 把包围盒面的常数项平移到以此种子点为原点
    planes = [plane.copy() for plane in base_planes]
    for plane in planes:
        plane.w += plane.xyz.dot(center)
    vertices, _indices = points_in_planes(planes, EPSILON_COPLANAR, epsilon)
    # 剪枝半径的初值: 还没量出胞体大小, 不做提前收工
    distance_max = float("inf")
    # 由近到远试邻居, 近处的邻居才会真正切到这个胞体
    for neighbor in sorted(points, key=lambda point: (point - center).length_squared):
        normal = neighbor - center
        length = normal.length
        if length == 0:
            # 种子点自身定不出中垂面
            continue
        if scale is not None:
            # 非等轴权重改变法线与有效距离, 按权重缩放后再定平面
            alternative = Vector(tuple(normal[i] * scale[i] for i in range(3)))
            length *= alternative.normalized().dot(normal.normalized())
            normal = alternative
        # 只有等轴时距离才随排序单调, 才能安全地提前收工
        if scale is None and length > distance_max:
            break
        plane = normal.normalized().to_4d()
        # 中垂面位于连线中点; 内缩量让平面再往里挪, 相邻碎块之间留出缝隙
        plane.w = -length / 2.0 + margin
        planes.append(plane)
        vertices, indices = points_in_planes(planes, EPSILON_COPLANAR, epsilon)
        if not vertices:
            break
        if len(indices) != len(planes):
            # 已经被切掉的平面不再参与后续求交
            planes = [planes[index] for index in indices]
        # 距离大于 2(R + 内缩量) 的邻居切不到半径为 R 的胞体
        radius = sqrt(max(v.length_squared for v in vertices))
        distance_max = 2.0 * (radius + margin) + max(radius, 1.0) * 1e-6
    return vertices


def points_as_bmesh_cells(points, scale, margin, bounds):
    """为每个种子点求出胞体, 返回 [(种子点, 顶点列表), ...]; 切不出胞体的种子点被丢弃

    points 已去重, bounds 是源物体的世界包围盒 (最小角点, 最大角点)
    """
    lo = [value - BOUNDS_MARGIN for value in bounds[0]]
    hi = [value + BOUNDS_MARGIN for value in bounds[1]]
    # 半空间用四分量向量表示: 前三维是法线, w 是常数项, 法线与坐标点积加 w 不大于 0 为内侧
    base_planes = [
        Vector((1, 0, 0, -hi[0])),
        Vector((-1, 0, 0, lo[0])),
        Vector((0, 1, 0, -hi[1])),
        Vector((0, -1, 0, lo[1])),
        Vector((0, 0, 1, -hi[2])),
        Vector((0, 0, -1, lo[2])),
    ]
    # 三轴权重相等时不改变平面构造, 走等轴快路径
    weights = None if scale[0] == scale[1] == scale[2] else tuple(scale)
    # 相交容差是绝对长度, 小物体上会误删细小的胞体, 因此随尺度缩小, 但不超过默认值
    epsilon = EPSILON_ISECT * min(1.0, (Vector(bounds[1]) - Vector(bounds[0])).length)
    cells = []
    for center in points:
        vertices = _cell_vertices(center, points, base_planes, weights, margin, epsilon)
        if vertices:
            cells.append((center, list(vertices)))
    return cells
