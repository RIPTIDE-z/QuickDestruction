"""胞体构造 - 从包围盒与中垂面出发做半空间求交, 为每个种子点生成有界 Voronoi 凸体"""

from math import isfinite, sqrt

from mathutils import Vector
from mathutils.geometry import points_in_planes

from ...common import measure
from ...common.errors import InvalidParameterError, require_finite
from ..constants import CELL_SCALE_MIN
from ..errors import NON_FINITE_MESSAGE, MeshFractureError


def points_as_bmesh_cells(
    verts, points, points_scale=None, margin_bounds=0.05, margin_cell=0.0, bounds=None
):
    """为每个种子点求出一个胞体, 返回 [(种子点, 顶点列表), ...]

    做法是从包围盒六个面出发, 依次用"种子点与邻居连线的中垂面"去切,
    只保留仍然切得到的那一侧, 最终得到该种子点的 Voronoi 胞体
    verts 只用于初始包围盒; 调用方已经量过时可用 bounds 直接传入, 省掉一遍遍历
    """
    if not verts or not points:
        return []
    require_finite((verts, points), MeshFractureError, NON_FINITE_MESSAGE)
    scale = tuple(points_scale) if points_scale is not None else (1.0, 1.0, 1.0)
    if len(scale) != 3 or any(not isfinite(v) or v < CELL_SCALE_MIN for v in scale):
        raise InvalidParameterError("cell_scale")
    isotropic = scale[0] == scale[1] == scale[2]
    # 三轴权重相等时不改变上游的平面构造, 走等轴快路径
    points_scale = None if isotropic else scale
    points = list({tuple(point): point for point in points}.values())
    # 包围盒外扩一点作为初始半空间, 保证单种子时也有界
    if bounds is None:
        bounds = measure.bounds(verts)
    lo = [value - margin_bounds for value in bounds[0]]
    hi = [value + margin_bounds for value in bounds[1]]
    # 半空间用四分量向量表示: 前三维是法线, w 是常数项, 法线与坐标的点积加 w 不大于 0 的一侧为内侧
    # 这是不可变的模板: 每个种子点都从它复制一份再平移到自己的坐标系
    base_planes = [
        Vector((1, 0, 0, -hi[0])),
        Vector((-1, 0, 0, lo[0])),
        Vector((0, 1, 0, -hi[1])),
        Vector((0, -1, 0, lo[1])),
        Vector((0, 0, 1, -hi[2])),
        Vector((0, 0, -1, lo[2])),
    ]
    cells = []
    for center in points:
        planes = [plane.copy() for plane in base_planes]
        for plane in planes:
            # 把包围盒面的常数项平移到以此种子点为原点
            plane.w += plane.xyz.dot(center)
        # 只有一个种子点时, 包围盒本身就是合法的胞体
        vertices, _indices = points_in_planes(planes)
        # 剪枝半径的初值: 还没量出胞体大小, 不做提前收工
        distance_max = float("inf")
        # 由近到远试邻居, 近处的邻居才会真正切到这个胞体
        neighbors = sorted(points, key=lambda point: (point - center).length_squared)
        for neighbor in neighbors:
            normal = neighbor - center
            length = normal.length
            if length == 0:
                # 与种子点重合的邻居定不出中垂面
                continue
            if points_scale is not None:
                # 非等轴权重改变法线与有效距离, 按权重缩放后再定平面
                alternative = Vector(tuple(normal[i] * points_scale[i] for i in range(3)))
                length *= alternative.normalized().dot(normal.normalized())
                normal = alternative
            # 只有等轴时距离才随排序单调, 才能安全地提前收工
            if isotropic and length > distance_max:
                break
            plane = normal.normalized().to_4d()
            # 中垂面位于连线中点; 内缩量让平面再往里挪一点, 于是相邻块之间留出缝隙
            plane.w = -length / 2.0 + margin_cell
            planes.append(plane)
            vertices, indices = points_in_planes(planes)
            if not vertices:
                break
            if len(indices) != len(planes):
                # 已经被切掉的平面不必再参与后续求交
                planes = [planes[index] for index in indices]
            # 当前胞体半径 R 已知: 距离大于 2(R+内缩量) 的邻居切不进来
            radius = sqrt(max(v.length_squared for v in vertices))
            distance_max = 2.0 * (radius + margin_cell) + max(radius, 1.0) * 1e-6
        if vertices:
            # 顶点为空说明该种子点没有可用胞体, 直接丢弃
            cells.append((center, list(vertices)))
    return cells
