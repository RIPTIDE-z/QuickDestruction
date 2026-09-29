"""布尔边界策略 - 求值快照与求解器配置, 检查求交结果是否越界, 不修改源几何"""

from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite

from mathutils import Vector

from ....common.blender_context import evaluated_mesh, mesh_edit
from ....common.errors import require_finite
from ..constants import WELD_SCALE_RATIO
from ..errors import NON_FINITE_MESSAGE, IntersectionBoundsError, MeshFractureError


@dataclass(frozen=True)
class BooleanSource:
    """一次切割的求值源快照, 只保存数值, 不持有临时求值网格"""

    # 源物体名, 只用于错误消息
    name: str
    # 世界空间包围盒的最小与最大角点
    minimum: tuple
    maximum: tuple
    # 包围盒体对角线长度, 即本次切割的整体尺度
    diagonal: float
    # 允许的越界余量, 由物体尺度与浮点精度推出
    tolerance: float
    # 输入含非流形时启用 Hole Tolerant 求解
    use_hole_tolerant: bool

    @property
    def weld_distance(self):
        """焊接距离: 胞体阶段与求交阶段共用同一份快照尺度"""
        return self.diagonal * WELD_SCALE_RATIO


def is_not_closed(bm):
    """网格不封闭: 有开放边, 多面共边, 或仅顶点处相接的非流形; 布尔求解策略与用户提示共用这一个判定"""
    return any(not edge.is_manifold for edge in bm.edges) or any(
        not vert.is_manifold for vert in bm.verts
    )


def bounds_diagonal(minimum, maximum):
    """包围盒的体对角线长度, 交给 mathutils 计算, 与 Blender 的浮点精度一致"""
    return Vector(tuple(maximum[i] - minimum[i] for i in range(3))).length


def inspect_source(obj, depsgraph):
    """求值一次源物体, 量出本次切割共用的包围盒, 尺度, 容差与求解策略"""
    with evaluated_mesh(obj, depsgraph) as (evaluated, mesh):
        if mesh is None or not mesh.vertices or not mesh.polygons:
            raise MeshFractureError('Mesh "{name}" has no faces', name=obj.name)
        vertices = tuple(evaluated.matrix_world @ vertex.co for vertex in mesh.vertices)
        require_finite((vertices,), MeshFractureError, NON_FINITE_MESSAGE)
        minimum = tuple(min(point[i] for point in vertices) for i in range(3))
        maximum = tuple(max(point[i] for point in vertices) for i in range(3))
        # 开边, 多面共边与仅顶点相接的非流形输入都采用容错求解; 这不是自相交检测
        with mesh_edit(mesh) as bm:
            needs_tolerance = is_not_closed(bm)
    diagonal = bounds_diagonal(minimum, maximum)
    magnitude = max(abs(value) for point in (minimum, maximum) for value in point)
    # 相对尺度容差, 加上世界坐标 float32 精度余量
    tolerance = max(diagonal * 1e-5, magnitude * 2**-21, 1e-9)
    return BooleanSource(obj.name, minimum, maximum, diagonal, tolerance, needs_tolerance)


def configure_intersection(modifier, source):
    """显式选 Exact 求交, 只有开放或非流形输入启用 Hole Tolerant"""
    modifier.operation = "INTERSECT"
    modifier.solver = "EXACT"
    modifier.use_self = False
    modifier.use_hole_tolerant = source.use_hole_tolerant


def validate_intersection(mesh, matrix_world, source):
    """交集不得明显超出源包围盒; 这是必要条件, 不证明凹形体内包含"""
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
    """校验临时求值结果, 越界时开启一次自交处理重算同一对操作数, 成功后交给调用方使用"""
    for attempt in range(2):
        evaluated = cell.evaluated_get(depsgraph)
        try:
            mesh = evaluated.to_mesh()
            validate_intersection(mesh, evaluated.matrix_world, source)
        except IntersectionBoundsError:
            # 已经重试过或已开启自交处理时不再尝试; 非有限值与程序错误不重试
            if attempt or modifier.use_self:
                raise
        else:
            yield evaluated
            return
        finally:
            # 每次求值都释放临时网格, 无效候选不会进入 Blender 数据库
            evaluated.to_mesh_clear()
        # 不改种子或容差, 只换求解路径并刷新依赖图
        modifier.use_self = True
        depsgraph.update()
