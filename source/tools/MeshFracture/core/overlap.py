"""部件重叠检测 - 只检测不修复: 找出表面互相穿插, 或者整个被另一个部件包住的部件对

结果只用来告警, 不改动任何几何
先按包围盒扫描找出可能相交的部件对, 再在每一对里用求交树判断, 不相交的部件对不花任何求交代价
两种重叠:
    穿插 - 一个部件的边穿过另一个部件的表面, 并且两端离表面都超过容差; 只是贴着或擦过表面不算
    包住 - 表面互不相交, 但一个部件整个在另一个封闭部件里面
"""

from array import array
from dataclasses import dataclass

from mathutils import Vector
from mathutils.bvhtree import BVHTree

from ....common.blender_context import mesh_edit
from ....common.geometry import is_not_closed
from ....common.hollow import RAY_DIRECTIONS, RAY_HITS

# 穿入深度容差 = 两个部件里较小的包围盒对角线乘以该比例; 浅于它的穿入当作接触
PENETRATION_RATIO = 1.0e-4
# 射线越过命中点的距离 = 被判断部件的包围盒对角线乘以该比例
RAY_STEP_RATIO = 1.0e-5


@dataclass
class Body:
    """一个部件的世界空间几何, 求交树在第一次用到时才建"""

    # 原网格, 只用来判断封闭性
    mesh: object
    # 世界空间包围盒的最小角点与最大角点
    low: tuple
    high: tuple
    # 包围盒体对角线长度
    diagonal: float
    # 世界坐标的顶点元组列表, 三角形的顶点下标元组列表
    points: list
    triangles: list
    # 求交树, 没建时为 None
    tree: object = None
    # 部件是否封闭, 没判断时为 None
    closed: object = None


def _triples(flat):
    """平铺序列按三个一组切成元组列表

    flat 平铺序列
    返回 元组列表
    """
    cursor = iter(flat)
    return list(zip(cursor, cursor, cursor, strict=True))


def _body(mesh, matrix, scratch):
    """部件的世界空间几何: 顶点经暂存网格一次变换到世界坐标, 三角形取自网格的三角化

    mesh 部件网格, 局部坐标
    matrix 部件的世界矩阵
    scratch 暂存网格, 每次调用前被清空; 用它做变换比逐个顶点乘矩阵快得多
    返回 Body
    """
    count = len(mesh.vertices)
    local = array("f", [0.0]) * (3 * count)
    mesh.vertices.foreach_get("co", local)
    scratch.clear_geometry()
    scratch.vertices.add(count)
    scratch.vertices.foreach_set("co", local)
    scratch.transform(matrix)
    world = array("f", [0.0]) * (3 * count)
    scratch.vertices.foreach_get("co", world)
    mesh.calc_loop_triangles()
    corners = array("i", [0]) * (3 * len(mesh.loop_triangles))
    mesh.loop_triangles.foreach_get("vertices", corners)
    low = tuple(min(world[axis::3]) for axis in range(3))
    high = tuple(max(world[axis::3]) for axis in range(3))
    diagonal = sum((top - bottom) ** 2 for bottom, top in zip(low, high, strict=True)) ** 0.5
    return Body(mesh, low, high, diagonal, _triples(world), _triples(corners))


def _tree(body):
    """部件的求交树, 第一次用到时建立

    body Body
    返回 BVHTree
    """
    if body.tree is None:
        body.tree = BVHTree.FromPolygons(body.points, body.triangles, all_triangles=True)
    return body.tree


def _is_closed(body):
    """部件是否封闭, 第一次用到时判断; 不封闭的部件没有明确的内外

    body Body
    返回 封闭为真
    """
    if body.closed is None:
        with mesh_edit(body.mesh) as bm:
            body.closed = not is_not_closed(bm)
    return body.closed


def _candidates(bodies):
    """包围盒互相接触的部件对: 沿跨度最大的轴扫描, 只和轴向区间重叠的部件比较

    bodies Body 列表
    返回 [(序号, 序号), ...], 前一个序号较小, 按序号排序
    """
    spans = [
        max(body.high[axis] for body in bodies) - min(body.low[axis] for body in bodies)
        for axis in range(3)
    ]
    axis = max(range(3), key=lambda index: spans[index])
    # 另外两个轴, 扫描轴上的重叠由活动列表保证
    first, second = (each for each in range(3) if each != axis)
    lows = [body.low for body in bodies]
    highs = [body.high for body in bodies]
    order = sorted(range(len(bodies)), key=lambda index: lows[index][axis])
    active, found = [], []
    for index in order:
        start = lows[index][axis]
        low_a, high_a = lows[index][first], highs[index][first]
        low_b, high_b = lows[index][second], highs[index][second]
        # 一趟扫描同时剔除轴向上已经结束的部件, 并比较另外两个轴
        alive = []
        for other in active:
            if highs[other][axis] < start:
                continue
            alive.append(other)
            if (
                lows[other][first] <= high_a
                and low_a <= highs[other][first]
                and lows[other][second] <= high_b
                and low_b <= highs[other][second]
            ):
                found.append((other, index) if other < index else (index, other))
        alive.append(index)
        active = alive
    found.sort()
    return found


def _pierces(source, target, triangles, tolerance):
    """source 里指定三角形的边是否穿过 target 的表面

    用 target 的求交树沿边投射线; 边的两端必须分处命中面两侧, 并且离它都超过容差
    source 提供边的部件
    target 被穿过的部件
    triangles source 里要检查的三角形序号
    tolerance 穿入深度容差
    返回 穿过为真
    """
    tree = _tree(target)
    seen = set()
    for triangle in triangles:
        a, b, c = source.triangles[triangle]
        for start, end in ((a, b), (b, c), (c, a)):
            edge = (start, end) if start < end else (end, start)
            # 相邻三角形共用的边只检查一次
            if edge in seen:
                continue
            seen.add(edge)
            origin, finish = Vector(source.points[start]), Vector(source.points[end])
            direction = finish - origin
            length = direction.length
            if length == 0.0:
                continue
            location, normal, _face, _distance = tree.ray_cast(origin, direction / length, length)
            if location is None:
                continue
            # 两端到命中面的有向距离: 异号才是真正穿过, 绝对值都超过容差才不是擦边
            before = (origin - location).dot(normal)
            after = (finish - location).dot(normal)
            if before * after < 0.0 and min(abs(before), abs(after)) > tolerance:
                return True
    return False


def _crossing(first, second):
    """两个部件的表面是否互相穿插

    first 部件
    second 部件
    返回 穿插为真
    """
    # 求交树的重叠只比较三角形包围盒, 先用它缩小要逐边检查的三角形
    pairs = _tree(first).overlap(_tree(second))
    if not pairs:
        return False
    tolerance = min(first.diagonal, second.diagonal) * PENETRATION_RATIO
    return _pierces(first, second, sorted({a for a, _b in pairs}), tolerance) or _pierces(
        second, first, sorted({b for _a, b in pairs}), tolerance
    )


def _inside(tree, point, step):
    """点是否在封闭网格里: 三条射线数交点奇偶, 按多数票决定

    tree 网格的世界求交树
    point 世界坐标的点
    step 射线越过命中点的距离
    返回 在里面为真
    """
    votes = 0
    for direction in RAY_DIRECTIONS:
        crossings, last, origin = 0, None, point.copy()
        for _ in range(RAY_HITS):
            location, _normal, face, _distance = tree.ray_cast(origin, direction)
            if location is None:
                break
            if face != last:
                crossings += 1
            last = face
            origin = location + direction * step
        votes += crossings % 2
    return votes * 2 > len(RAY_DIRECTIONS)


def _enclosed(inner, outer):
    """inner 是否整个在封闭的 outer 里面; 调用前表面已经确认不穿插, 所以一个顶点就能代表整个部件

    inner 可能被包住的部件
    outer 可能包住它的部件
    返回 被包住为真
    """
    # 包围盒不在里面就不可能被包住, 不必判断封闭性与射线
    if any(
        inner.low[axis] < outer.low[axis] or inner.high[axis] > outer.high[axis]
        for axis in range(3)
    ):
        return False
    # 不封闭的部件没有明确的内外, 射线奇偶不可信
    if not _is_closed(outer):
        return False
    step = max(inner.diagonal * RAY_STEP_RATIO, 1.0e-9)
    return _inside(_tree(outer), Vector(inner.points[0]), step)


def find_overlaps(parts, scratch):
    """找出互相重叠的部件对, 不改动任何部件

    parts [(网格, 世界矩阵), ...] 部件网格在局部坐标
    scratch 暂存网格, 内容会被覆盖
    返回 [(序号, 序号), ...], 前一个序号较小, 按序号排序; 没有重叠时为空
    """
    bodies = [_body(mesh, matrix, scratch) for mesh, matrix in parts]
    return [
        (first, second)
        for first, second in _candidates(bodies)
        if _crossing(bodies[first], bodies[second])
        or _enclosed(bodies[first], bodies[second])
        or _enclosed(bodies[second], bodies[first])
    ]
