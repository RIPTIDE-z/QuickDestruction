"""布尔边界策略 - 诊断输入网格并选择求解器, 检查求交结果是否越界, 不修改源几何"""

from dataclasses import dataclass
from math import isfinite, sqrt

import bmesh

from ..errors import MeshFractureError


@dataclass(frozen=True)
class BooleanSource:
    """一次切割的求值源快照,只保存数值,不持有临时求值网格"""

    # 源物体名, 只用于错误消息
    name: str
    # 世界空间包围盒的最小与最大角点
    minimum: tuple
    maximum: tuple
    # 允许的越界余量, 由物体尺度与浮点精度推出
    tolerance: float
    # 输入含非流形时启用 Hole Tolerant 求解
    use_hole_tolerant: bool


def inspect_source(obj, depsgraph):
    """每批胞体检查一次实际求值源,覆盖修改器产生或修复的开放拓扑"""
    evaluated = obj.evaluated_get(depsgraph)
    try:
        mesh = evaluated.to_mesh()
        if mesh is None or not mesh.vertices or not mesh.polygons:
            raise MeshFractureError('Mesh "{name}" has no faces', name=obj.name)
        coordinates = [evaluated.matrix_world @ vertex.co for vertex in mesh.vertices]
        if any(not isfinite(value) for point in coordinates for value in point):
            raise MeshFractureError("Source geometry contains non-finite coordinates")
        minimum = tuple(min(point[i] for point in coordinates) for i in range(3))
        maximum = tuple(max(point[i] for point in coordinates) for i in range(3))
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            # 开边/多面共边及仅顶点相接的非流形输入都采用容错判定
            # 这不是自相交检测,也不把开放网格视为已修复的封闭实体
            needs_tolerance = any(not edge.is_manifold for edge in bm.edges) or any(
                not vertex.is_manifold for vertex in bm.verts
            )
        finally:
            bm.free()
    finally:
        evaluated.to_mesh_clear()
    # minimum/maximum 是同一个 range(3) 生成的两个三元组, 长度必然一致
    diagonal = sqrt(sum((hi - lo) ** 2 for lo, hi in zip(minimum, maximum, strict=True)))
    magnitude = max(abs(value) for point in (minimum, maximum) for value in point)
    # 相对尺度容差,加上世界坐标 float32 精度余量;不拿艺术间隙或焊接距离当容差
    tolerance = max(diagonal * 1e-5, magnitude * 2**-21, 1e-9)
    return BooleanSource(obj.name, minimum, maximum, tolerance, needs_tolerance)


def configure_intersection(modifier, source):
    """显式选 Exact,只有开放/非流形输入启用 Hole Tolerant"""
    modifier.operation = "INTERSECT"
    modifier.solver = "EXACT"
    modifier.use_hole_tolerant = source.use_hole_tolerant


def validate_intersection(mesh, matrix_world, source):
    """交集不得明显超出源 AABB;这是必要条件,不是凹形体内包含证明

    只检查,不裁顶点或自动修补;失败由上层回滚本次生成数据
    调试模式也必须传入求值网格,不能把合法的凸胞体基础网格当作结果
    """
    if not mesh.polygons:
        return
    for vertex in mesh.vertices:
        point = matrix_world @ vertex.co
        if any(not isfinite(value) for value in point):
            raise MeshFractureError(
                'Boolean intersection for "{name}" contains non-finite coordinates',
                name=source.name,
            )
        if any(
            value < source.minimum[i] - source.tolerance
            or value > source.maximum[i] + source.tolerance
            for i, value in enumerate(point)
        ):
            raise MeshFractureError(
                'Boolean intersection extends outside "{name}"; check for open or invalid source geometry',
                name=source.name,
            )
