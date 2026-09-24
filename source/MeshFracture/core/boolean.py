"""布尔边界策略 - 诊断输入网格并选择求解器, 检查求交结果是否越界, 不修改源几何"""

from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite

import bmesh

from ...common import measure
from ...common.errors import require_finite
from ..errors import NON_FINITE_MESSAGE, IntersectionBoundsError, MeshFractureError


@dataclass(frozen=True)
class BooleanSource:
    """一次切割的求值源快照,只保存数值,不持有临时求值网格"""

    # 源物体名, 只用于错误消息
    name: str
    # 世界空间顶点坐标; 胞体构造的包围盒与尺度都取自这一份
    vertices: tuple
    # 世界空间包围盒的最小与最大角点
    minimum: tuple
    maximum: tuple
    # 包围盒体对角线长度, 即本次切割的整体尺度
    diagonal: float
    # 允许的越界余量, 由物体尺度与浮点精度推出
    tolerance: float
    # 输入含非流形时启用 Hole Tolerant 求解
    use_hole_tolerant: bool


def inspect_source(obj, depsgraph):
    """求值一次源物体并量出本批胞体共用的全部数值

    覆盖修改器产生或修复的开放拓扑; 世界空间顶点与包围盒只在这里算一次,
    胞体构造与布尔校验都复用它, 不再对同一源重复求值与测量
    """
    # 临时求值网格只在本次调用内存在, 量完所需数值立即释放
    evaluated = obj.evaluated_get(depsgraph)
    try:
        mesh = evaluated.to_mesh()
        if mesh is None or not mesh.vertices or not mesh.polygons:
            raise MeshFractureError('Mesh "{name}" has no faces', name=obj.name)
        vertices = tuple(evaluated.matrix_world @ vertex.co for vertex in mesh.vertices)
        require_finite((vertices,), MeshFractureError, NON_FINITE_MESSAGE)
        minimum, maximum = measure.bounds(vertices)
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
    diagonal = measure.diagonal(minimum, maximum)
    magnitude = max(abs(value) for point in (minimum, maximum) for value in point)
    # 相对尺度容差,加上世界坐标 float32 精度余量;不拿艺术间隙或焊接距离当容差
    tolerance = max(diagonal * 1e-5, magnitude * 2**-21, 1e-9)
    return BooleanSource(obj.name, vertices, minimum, maximum, diagonal, tolerance, needs_tolerance)


def configure_intersection(modifier, source):
    """显式选 Exact,只有开放/非流形输入启用 Hole Tolerant"""
    modifier.operation = "INTERSECT"
    modifier.solver = "EXACT"
    modifier.use_self = False
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
            raise IntersectionBoundsError(
                'Boolean intersection extends outside "{name}"; check for open or invalid source geometry',
                name=source.name,
            )


@contextmanager
def validated_evaluation(cell, modifier, source, depsgraph):
    """验证临时求值几何 - 越界时仅启用一次自交处理并重算同一对操作数

    成功后交给调用者烘焙或保留修改器
    每次求值都释放临时网格 - 无效候选不会进入 Blender 数据库
    """
    for attempt in range(2):
        evaluated = cell.evaluated_get(depsgraph)
        try:
            mesh = evaluated.to_mesh()
            validate_intersection(mesh, evaluated.matrix_world, source)
        except IntersectionBoundsError:
            # 非有限值和程序错误不重试 - 已开启自交处理时也不能重复尝试
            if attempt or modifier.use_self:
                raise
        else:
            yield evaluated
            return
        finally:
            evaluated.to_mesh_clear()
        # 不改种子或容差 - 只换求解路径并刷新依赖图
        modifier.use_self = True
        depsgraph.update()
