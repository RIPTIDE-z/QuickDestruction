"""布尔边界策略 - 求值快照与求解器配置, 检查求交结果是否越界与内外判定是否可信, 不修改源几何

Exact 求交关闭自交处理时偶尔会把整块胞体的内外判反: 与源物体确实相交的胞体得到空结果,
落在源物体外的胞体得到整块胞体; 前者在碎块里留下空洞, 后者多出一块不属于源物体的碎块
始终开启自交处理能避免, 但切割慢 4 到 12 倍; 所以只复核空结果与整块结果, 判错时才开启自交处理重算
"""

from contextlib import contextmanager
from dataclasses import dataclass
from math import isfinite

import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ....common.blender_context import evaluated_mesh, mesh_edit
from ....common.errors import require_finite
from ....common.geometry import is_not_closed
from ..constants import WELD_SCALE_RATIO
from ..errors import NON_FINITE_MESSAGE, IntersectionBoundsError, MeshFractureError

# 判断点在不在网格里时的三条射线方向: 都不与坐标轴对齐, 彼此也不平行; 按多数票决定, 一条射线恰好擦过棱时不会判错
RAY_DIRECTIONS = tuple(
    Vector(direction).normalized()
    for direction in (
        (0.5773, 0.5774, 0.5776),
        (-0.6113, 0.4987, 0.6143),
        (0.4027, -0.7109, 0.5767),
    )
)
# 一条射线最多数的交点个数, 防止退化几何让射线反复命中同一处
RAY_HITS = 256
# 求交结果与胞体体积之差小于胞体体积乘它时, 视为整块胞体都在源物体里
WHOLE_RATIO = 1e-4


@dataclass(frozen=True)
class BooleanSource:
    """一次切割的求值源快照, 只保存数值, 不持有临时求值网格"""

    # 源物体名, 只用于错误消息
    name: str
    # 世界空间包围盒的最小角点
    minimum: tuple
    # 世界空间包围盒的最大角点
    maximum: tuple
    # 包围盒体对角线长度, 即本次切割的整体尺度
    diagonal: float
    # 允许的越界余量, 由物体尺度与浮点精度推出
    tolerance: float
    # 输入含非流形时启用 Hole Tolerant 求解
    use_hole_tolerant: bool

    @property
    def weld_distance(self):
        """焊接距离: 胞体阶段与求交阶段共用同一份快照尺度

        返回 焊接距离, 世界单位
        """
        return self.diagonal * WELD_SCALE_RATIO


@dataclass(frozen=True)
class SourceShape:
    """被切物体求值后的世界网格, 复核布尔的内外判定用; 每次切割建一次, 胞体之间共用"""

    # 世界坐标网格的求交树
    tree: BVHTree
    # 射线命中后往前挪的距离: 取快照的容差, 它已经计入物体尺度与世界坐标的浮点精度
    step: float


def source_shape(obj, depsgraph, source):
    """求值被切物体, 建它的世界坐标求交树

    obj 被切的物体
    depsgraph 求值用的依赖图
    source BooleanSource, 提供容差
    返回 SourceShape
    """
    with evaluated_mesh(obj, depsgraph) as (evaluated, mesh):
        return SourceShape(_world_tree(mesh, evaluated.matrix_world), source.tolerance)


def _world_tree(mesh, matrix):
    """网格在 matrix 下的世界坐标求交树

    mesh 网格数据
    matrix 世界矩阵
    返回 BVHTree
    """
    vertices = [matrix @ vertex.co for vertex in mesh.vertices]
    return BVHTree.FromPolygons(vertices, [tuple(polygon.vertices) for polygon in mesh.polygons])


def _world_volume(mesh, matrix):
    """网格在 matrix 下的体积

    mesh 网格数据
    matrix 世界矩阵
    返回 体积, 世界单位的立方
    """
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.transform(matrix)
        return bm.calc_volume(signed=False)
    finally:
        bm.free()


def _crossings(shape, point, direction):
    """沿一条射线数穿过封闭网格表面的次数

    shape SourceShape
    point 世界坐标的起点
    direction 单位方向
    返回 交点个数
    """
    hits, origin, last = 0, point.copy(), None
    for _ in range(RAY_HITS):
        location, _normal, index, _distance = shape.tree.ray_cast(origin, direction)
        if location is None:
            break
        # 挪过交点后浮点误差仍可能再次命中同一个面, 这样的命中不计
        if index != last:
            hits += 1
        last = index
        origin = location + direction * shape.step
    return hits


def _inside(shape, point):
    """点是否在封闭网格里: 三条射线各数交点奇偶, 按多数票决定

    shape SourceShape
    point 世界坐标的点
    返回 在里面为真
    """
    votes = sum(_crossings(shape, point, direction) % 2 for direction in RAY_DIRECTIONS)
    return votes * 2 > len(RAY_DIRECTIONS)


def misclassified(cell, mesh, matrix, shape):
    """复核求交结果是不是把胞体的内外判反了; 只复核空结果与整块结果, 部分交集不复核

    胞体与源物体的表面相交时, 真实交集一定是部分的; 表面不相交时, 胞体要么整块在内要么整块在外,
    看胞体中心在不在源物体里就知道是哪一种
    cell 胞体物体, 网格仍是求交前的凸胞体
    mesh 求交结果的网格
    matrix 结果物体的世界矩阵
    shape SourceShape
    返回 判错为真
    """
    cell_matrix = cell.matrix_world
    if mesh.polygons:
        whole = _world_volume(cell.data, cell_matrix)
        # 只切掉了一部分: 两者的表面确实相交过, 结果可信
        if whole - _world_volume(mesh, matrix) > whole * WHOLE_RATIO:
            return False
        claimed_inside = True
    else:
        claimed_inside = False
    cell_tree = _world_tree(cell.data, cell_matrix)
    # 表面相交却得到空结果或整块结果, 一定判错了
    if cell_tree.overlap(shape.tree):
        return True
    # 凸胞体的顶点平均值一定在胞体内部, 用它代表整块胞体
    vertices = cell.data.vertices
    center = cell_matrix @ (sum((vertex.co for vertex in vertices), Vector()) / len(vertices))
    return _inside(shape, center) != claimed_inside


def bounds_diagonal(minimum, maximum):
    """包围盒的体对角线长度, 交给 mathutils 计算, 与 Blender 的浮点精度一致

    minimum 最小角点
    maximum 最大角点
    返回 对角线长度
    """
    return Vector(tuple(maximum[i] - minimum[i] for i in range(3))).length


def inspect_source(obj, depsgraph):
    """求值一次源物体, 量出本次切割共用的包围盒, 尺度, 容差与求解策略

    obj 源物体
    depsgraph 求值用的依赖图
    返回 BooleanSource; 源没有面或含非有限坐标时抛 MeshFractureError
    """
    with evaluated_mesh(obj, depsgraph) as (evaluated, mesh):
        # 没有面的网格无法求交
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
    """显式选 Exact 求交, 只有开放或非流形输入启用 Hole Tolerant

    modifier 布尔修改器
    source BooleanSource
    """
    modifier.operation = "INTERSECT"
    modifier.solver = "EXACT"
    modifier.use_self = False
    modifier.use_hole_tolerant = source.use_hole_tolerant


def validate_intersection(mesh, matrix_world, source):
    """交集不得明显超出源包围盒; 这是必要条件, 不证明凹形体内包含

    mesh 求交结果的网格
    matrix_world 结果物体的世界矩阵
    source BooleanSource
    超出时抛 IntersectionBoundsError, 含非有限坐标时抛 MeshFractureError
    """
    # 空结果 (胞体与源没有交集) 合法, 之后由调用方丢弃
    if not mesh.polygons:
        return
    for vertex in mesh.vertices:
        point = matrix_world @ vertex.co
        if any(not isfinite(value) for value in point):
            raise MeshFractureError(
                'Boolean intersection for "{name}" contains non-finite coordinates',
                name=source.name,
            )
        # 允许一点容差, 浮点误差不应被判成越界
        if any(
            value < source.minimum[i] - source.tolerance
            or value > source.maximum[i] + source.tolerance
            for i, value in enumerate(point)
        ):
            raise IntersectionBoundsError(
                'Boolean intersection extends outside "{name}"; check for open or invalid source geometry',
                name=source.name,
            )


def _needs_retry(cell, modifier, evaluated, mesh, source, shape):
    """求值结果是否要开启自交处理重算: 越界, 或内外判反

    已开启自交处理时越界直接报错, 内外判定不再复核, 因为没有更可靠的求解路径了
    cell 胞体物体
    modifier 它的布尔修改器
    evaluated 求值后的胞体物体
    mesh 求交结果的网格
    source BooleanSource
    shape SourceShape
    返回 要重算为真
    """
    try:
        validate_intersection(mesh, evaluated.matrix_world, source)
    except IntersectionBoundsError:
        if modifier.use_self:
            raise
        return True
    return not modifier.use_self and misclassified(cell, mesh, evaluated.matrix_world, shape)


@contextmanager
def validated_evaluation(cell, modifier, source, depsgraph, shape):
    """校验临时求值结果, 越界或内外判反时开启自交处理重算同一对操作数, 成功后交给调用方使用

    cell 带布尔修改器的胞体物体
    modifier 它的布尔修改器
    source BooleanSource
    depsgraph 求值用的依赖图
    shape SourceShape, 复核内外判定用
    产出 求值后的物体, 离开作用域时临时网格自动释放
    """
    while True:
        evaluated = cell.evaluated_get(depsgraph)
        try:
            mesh = evaluated.to_mesh()
            # 结果可信: 交给调用方, 离开作用域时由 finally 释放临时网格
            if not _needs_retry(cell, modifier, evaluated, mesh, source, shape):
                yield evaluated
                return
        finally:
            # 每次求值都释放临时网格, 无效候选不会进入 Blender 数据库
            evaluated.to_mesh_clear()
        # 不改种子或容差, 只换求解路径并刷新依赖图; 开启后 _needs_retry 不会再要求重算
        modifier.use_self = True
        depsgraph.update()
