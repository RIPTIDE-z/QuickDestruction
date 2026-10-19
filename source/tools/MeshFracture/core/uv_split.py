"""按 UV 岛拆分 - 美术已经按裂纹切好 UV 时, 每个 UV 岛就是一块碎片, 不切割, 输出与切割一致的资产

薄壳模型常由外层与内层两张曲面组成, 两层的 UV 岛一一对应; 这里把相互对应的岛配成一对,
合进同一块碎块, 再沿裂缝把这一对岛的边界环桥接成侧壁, 碎块封闭
可以只拆编辑模式里选中的部件; 不参与拆分的连通块 (装饰件, 底座等) 并入最近的碎块, 各自成块或合成一块
一对岛也找不到时每个 UV 岛各成一块, 适用于单层曲面
应当封闭却没有封闭的碎块 (桥接失败, 两层朝向不一致) 记下名字交给调用方提示, 不静默放过
下游的物理, 驱动, 模拟与绑定只认资产清单与碎块标记, 不关心碎块是怎样得到的
"""

from dataclasses import dataclass, field
from time import perf_counter

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from ....common import shards
from ....common.blender_context import evaluated_mesh, mesh_edit
from ....common.created_data import CreatedData, run_all
from ..errors import MeshFractureError
from . import cleanup, interior, materials, shard_objects
from .naming import NamingPlan
from .pipeline import check_limit, output_collection

# 两个角上的 UV 坐标差小于该值时视为同一个 UV 点
UV_TOLERANCE = 1.0e-5
# 配对时两层的最大间距 = 岛所在连通块的包围盒对角线乘以该比例
# 用连通块而不用岛本身: 薄壳切得越碎岛越小, 而壳厚不变, 按岛算会让小碎片配不上对
PAIR_GAP_RATIO = 0.05
# 两层间距小于最大间距乘以该比例时视为重合: 重合的双面几何没有厚度, 不是薄壳
PAIR_COINCIDENT_RATIO = 1.0e-3
# 配对时岛的面积里至少要有该比例落在对方岛的间距范围内
PAIR_COVERAGE = 0.9
# 配对时两个岛的面积之比 (小比大) 的下限, 排除贴在大岛上的小装饰面
PAIR_AREA_RATIO = 0.5
# 两层必须背对背: 从对面最近点指向本面的方向与本面法线的夹角余弦下限, 与对面法线的夹角余弦上限的相反数
# 外层法线朝外, 内层法线朝内腔, 两层的面各自背向对方; 同向叠放的衬里与法线翻错的内层不会被配对
PAIR_FACING = 0.5
# 两条边界环的平均距离按这一比例放宽后仍是同一对岛里最小的, 才认为它们对应
LOOP_MATCH_SLACK = 1.0

# 没有 UV 的网格
NO_UV_MESSAGE = 'Mesh "{name}" has no UV map'
# 指定的 UV 不存在
MISSING_UV_MESSAGE = 'Mesh "{name}" has no UV map named "{uv}"'
# 只有一个 UV 岛
SINGLE_ISLAND_MESSAGE = 'Mesh "{name}" has only one UV island; mark the cracks as UV seams first'
# 只拆选中部件时没有选中的面
NO_SELECTION_MESSAGE = (
    'No faces of "{name}" are selected; select the parts to split in Edit Mode, '
    "or turn off Selected Parts Only"
)

# 碎块网格上的临时面层: 源网格里的面下标与所属 UV 岛序号, 写出网格之前删除
FACE_LAYER = "qd_face"
ISLAND_LAYER = "qd_island"


@dataclass
class Island:
    """一个 UV 岛: 所属连通块与面下标"""

    # 所属连通块的序号
    component: int
    # 面下标列表
    faces: list
    # 面积, 局部坐标
    area: float = 0.0
    # 包围盒最小角与最大角
    low: Vector = field(default_factory=Vector)
    high: Vector = field(default_factory=Vector)


@dataclass
class Group:
    """一块碎块的组成: 面下标与需要桥接侧壁的岛对"""

    # 面下标集合
    faces: set
    # 配对的岛序号对 (a, b), 只有它们的边界环之间补侧壁
    pairs: list = field(default_factory=list)


@dataclass
class Surface:
    """源网格的面数据快照: 一次算好, 配对时反复使用"""

    # 每个面的中心, 面积与法线, 按面下标
    centers: list
    areas: list
    normals: list
    # 每个面所属连通块与 UV 岛的序号
    component_of: list
    island_of: list


def _bounds(coords):
    """一组坐标的包围盒 (最小角, 最大角)"""
    return (
        Vector(tuple(min(co[axis] for co in coords) for axis in range(3))),
        Vector(tuple(max(co[axis] for co in coords) for axis in range(3))),
    )


def _uv_continuous(edge, layer):
    """边两侧的 UV 是否连续; 边界边与非流形边视为不连续

    edge BMEdge
    layer UV 层
    返回 连续为真
    """
    if len(edge.link_loops) != 2:
        return False
    first, second = edge.link_loops
    # 两个面沿同一条边走向相反: 一侧的起点对另一侧的终点
    return (first[layer].uv - second.link_loop_next[layer].uv).length < UV_TOLERANCE and (
        first.link_loop_next[layer].uv - second[layer].uv
    ).length < UV_TOLERANCE


def _islands(bm, layer, surface):
    """按 UV 连续的边扩散求 UV 岛, 顺序由面下标决定, 可复现

    bm 网格的 BMesh, 面已建查找表
    layer UV 层
    surface Surface, 用到面积与连通块序号
    返回 Island 列表
    """
    seen = set()
    islands = []
    for face in bm.faces:
        if face.index in seen:
            continue
        seen.add(face.index)
        # 深度优先: 只跨过 UV 连续的边
        stack, members = [face], []
        while stack:
            current = stack.pop()
            members.append(current.index)
            for edge in current.edges:
                if not _uv_continuous(edge, layer):
                    continue
                for other in edge.link_faces:
                    if other.index not in seen:
                        seen.add(other.index)
                        stack.append(other)
        low, high = _bounds([vert.co for index in members for vert in bm.faces[index].verts])
        islands.append(
            Island(
                component=surface.component_of[face.index],
                faces=sorted(members),
                area=sum(surface.areas[index] for index in members),
                low=low,
                high=high,
            )
        )
    return islands


def _overlaps(island, low, high, margin):
    """岛的包围盒扩大 margin 后是否与另一个包围盒相交"""
    return all(
        island.low[axis] - margin <= high[axis] and island.high[axis] + margin >= low[axis]
        for axis in range(3)
    )


@dataclass
class Components:
    """连通块的求交树与统计, 配对时按连通块剔除不可能的对象"""

    # 每个连通块的 (BVHTree, 树内序号到面下标的映射)
    trees: list
    # 每个连通块的包围盒 (最小角, 最大角)
    bounds: list
    # 每个连通块的面积与包围盒对角线
    areas: list
    diagonals: list


def _components(bm, faces_by_component, surface):
    """建每个连通块的求交树并统计面积与包围盒

    bm 网格的 BMesh
    faces_by_component 面列表的列表
    surface Surface
    返回 Components
    """
    coords = [vert.co.copy() for vert in bm.verts]
    trees, bounds, areas, diagonals = [], [], [], []
    for faces in faces_by_component:
        indices = [face.index for face in faces]
        polygons = [[vert.index for vert in face.verts] for face in faces]
        trees.append((BVHTree.FromPolygons(coords, polygons), indices))
        low, high = _bounds([vert.co for face in faces for vert in face.verts])
        bounds.append((low, high))
        diagonals.append((high - low).length)
        areas.append(sum(surface.areas[index] for index in indices))
    return Components(trees, bounds, areas, diagonals)


def _best_partner(index, islands, surface, components):
    """一个岛在别的连通块里最贴合的岛: 背对背, 有厚度, 覆盖率与面积比都达标时才算

    index 岛序号
    islands Island 列表
    surface Surface
    components Components
    返回 对方岛的序号, 没有时为 None
    """
    island = islands[index]
    gap = components.diagonals[island.component] * PAIR_GAP_RATIO
    coincident = gap * PAIR_COINCIDENT_RATIO
    votes = {}
    for component, (tree, indices) in enumerate(components.trees):
        # 自己的连通块, 太远的连通块, 总面积不够配对的连通块都不必查
        if (
            component == island.component
            or components.areas[component] < island.area * PAIR_AREA_RATIO
            or not _overlaps(island, *components.bounds[component], gap)
        ):
            continue
        for face_index in island.faces:
            center = surface.centers[face_index]
            location, normal, hit, distance = tree.find_nearest(center, gap)
            # 超出间距或与本面重合的面不投票, 对应的面积算作没有覆盖
            if location is None or distance <= coincident:
                continue
            direction = (center - location) / distance
            # 对面在本面背后, 且对面的法线背向本面
            if (
                direction.dot(surface.normals[face_index]) < PAIR_FACING
                or direction.dot(normal) > -PAIR_FACING
            ):
                continue
            other = surface.island_of[indices[hit]]
            votes[other] = votes.get(other, 0.0) + surface.areas[face_index]
    if not votes or island.area <= 0.0:
        return None
    best = max(votes, key=lambda other: (votes[other], -other))
    smaller, bigger = sorted((island.area, islands[best].area))
    if votes[best] < island.area * PAIR_COVERAGE or smaller < bigger * PAIR_AREA_RATIO:
        return None
    return best


def _split_components(islands, partner, selected):
    """要按岛拆开的连通块

    给了 selected 时从选中的连通块出发, 沿配对关系一直扩到不再增加: 薄壳的每一层都会一起拆,
    不会出现一层拆了, 与它配对的另一层却整块留下, 同一批面进两块碎块的情况
    没给时是有配对的连通块
    islands Island 列表
    partner 岛序号到配对岛序号的映射, 双向都有
    selected 选中的连通块序号集合, None 表示自动判断
    返回 连通块序号集合
    """
    if selected is None:
        return {islands[index].component for index in partner}
    split = set(selected)
    while True:
        grown = split | {
            islands[other].component
            for index, other in partner.items()
            if islands[index].component in split
        }
        if grown == split:
            return split
        split = grown


def _groups(bm, islands, surface, faces_by_component, unsplit, selected):
    """把 UV 岛分组成碎块: 互为最贴合的两个岛配成一对, 要拆的连通块按岛拆开, 其余连通块按 unsplit 处理

    bm 网格的 BMesh, 面已建查找表
    islands Island 列表
    surface Surface
    faces_by_component 面列表的列表
    unsplit 不拆分的连通块的处理方式 ATTACH, SEPARATE 或 TOGETHER
    selected 选中的连通块序号集合, None 表示自动判断
    返回 (Group 列表按最小面下标排序, 要拆的连通块数)
    """
    components = _components(bm, faces_by_component, surface)
    best = [_best_partner(index, islands, surface, components) for index in range(len(islands))]
    # 只接受互为最佳的配对, 避免一个大岛同时吸走几个小岛
    partner = {
        index: other
        for index, other in enumerate(best)
        if other is not None and best[other] == index
    }
    if selected is None and not partner:
        # 单层曲面: 每个 UV 岛各成一块, 没有侧壁
        groups = [Group({*island.faces}) for island in islands]
        return sorted(groups, key=lambda group: min(group.faces)), len(faces_by_component)
    split = _split_components(islands, partner, selected)
    groups, done = [], set()
    for index, island in enumerate(islands):
        if island.component not in split or index in done:
            continue
        if index in partner:
            other = partner[index]
            done.update((index, other))
            faces = {*island.faces, *islands[other].faces}
            groups.append(Group(faces, [(index, other)]))
        else:
            done.add(index)
            groups.append(Group({*island.faces}))
    loose = [faces for component, faces in enumerate(faces_by_component) if component not in split]
    if unsplit == "ATTACH":
        _attach(bm, groups, loose)
    elif unsplit == "TOGETHER":
        if loose:
            groups.append(Group({face.index for faces in loose for face in faces}))
    else:
        groups.extend(Group({face.index for face in faces}) for faces in loose)
    return sorted(groups, key=lambda group: min(group.faces)), len(split)


def _attach(bm, groups, loose):
    """不拆分的连通块并入离它最近的碎块: 取连通块全部顶点到碎块表面的最近距离

    并入的部件不加入岛对, 它自己的开放边不会被桥接
    bm 网格的 BMesh, 面已建查找表
    groups Group 列表, 原地加入
    loose 不拆分的连通块, 每个是面列表
    """
    if not groups or not loose:
        return
    coords = [vert.co.copy() for vert in bm.verts]
    owner, polygons = [], []
    for group_index, group in enumerate(groups):
        for face_index in group.faces:
            owner.append(group_index)
            polygons.append([vert.index for vert in bm.faces[face_index].verts])
    tree = BVHTree.FromPolygons(coords, polygons)
    for faces in loose:
        nearest = None
        for vert in {vert for face in faces for vert in face.verts}:
            location, _normal, index, distance = tree.find_nearest(vert.co)
            if location is not None and (nearest is None or distance < nearest[0]):
                nearest = (distance, owner[index])
        if nearest is not None:
            groups[nearest[1]].faces.update(face.index for face in faces)


def _loops(edges):
    """开放边按共享顶点连成边环

    edges 边集合
    返回 边列表的列表
    """
    remaining = set(edges)
    loops = []
    while remaining:
        stack = [remaining.pop()]
        loop = []
        while stack:
            edge = stack.pop()
            loop.append(edge)
            for vert in edge.verts:
                for other in vert.link_edges:
                    if other in remaining:
                        remaining.discard(other)
                        stack.append(other)
        loops.append(loop)
    return loops


def _loop_distance(loop, other):
    """两条边界环的平均距离: 两个方向各取顶点到对方最近顶点的平均距离, 取较大者

    只看中心会把同心的外轮廓与孔的边界配错, 这里比较的是整条环的形状与位置
    loop other 边列表
    返回 距离
    """
    first = list({vert for edge in loop for vert in edge.verts})
    second = list({vert for edge in other for vert in edge.verts})

    def mean(points, targets):
        """points 到 targets 最近点的平均距离"""
        tree = KDTree(len(targets))
        for index, vert in enumerate(targets):
            tree.insert(vert.co, index)
        tree.balance()
        return sum(tree.find(vert.co)[2] for vert in points) / len(points)

    return max(mean(first, second), mean(second, first))


def _bridge(bm, island_layer, pairs):
    """只在配对的两个岛之间桥接侧壁: 每对岛的边界环按平均距离从近到远两两配对

    bm 一块碎块的 BMesh
    island_layer 面所属 UV 岛序号的面层
    pairs 配对的岛序号对列表
    返回 (条带列表, 桥接失败的环对数); 每条条带是 (第一个岛的边界环, 第二个岛的边界环, 侧壁面列表)
    """
    loops = _loops([edge for edge in bm.edges if len(edge.link_faces) == 1])
    by_island = {}
    for loop in loops:
        by_island.setdefault(loop[0].link_faces[0][island_layer], []).append(loop)
    strips, failed = [], 0
    for first, second in pairs:
        candidates = sorted(
            (_loop_distance(a, b), index_a, index_b)
            for index_a, a in enumerate(by_island.get(first, []))
            for index_b, b in enumerate(by_island.get(second, []))
        )
        used_a, used_b = set(), set()
        for _distance, index_a, index_b in candidates:
            if index_a in used_a or index_b in used_b:
                continue
            used_a.add(index_a)
            used_b.add(index_b)
            loop_a, loop_b = by_island[first][index_a], by_island[second][index_b]
            try:
                made = bmesh.ops.bridge_loops(bm, edges=loop_a + loop_b)["faces"]
            except (RuntimeError, ValueError):
                made = []
            # bridge_loops 对它处理不了的输入可能既不报错也不生成面, 两种情况都算失败
            if made:
                strips.append((loop_a, loop_b, made))
            else:
                failed += 1
    return strips, failed


def _chain(edges):
    """边界环的顶点按走向排好: 闭合环从下标最小的顶点出发, 开放链从一端出发

    edges 边列表
    返回 (顶点列表, 是否闭合); 环上有分叉时返回 (None, False)
    """
    neighbors = {}
    for edge in edges:
        a, b = edge.verts
        neighbors.setdefault(a, []).append(b)
        neighbors.setdefault(b, []).append(a)
    if any(len(found) > 2 for found in neighbors.values()):
        return None, False
    ends = [vert for vert, found in neighbors.items() if len(found) == 1]
    closed = not ends
    start = (
        min(neighbors, key=lambda vert: vert.index) if closed else min(ends, key=lambda v: v.index)
    )
    order, previous, current = [start], None, start
    while True:
        following = [vert for vert in neighbors[current] if vert is not previous]
        if not following or following[0] is start:
            break
        previous, current = current, following[0]
        order.append(current)
    # 顶点没有走全说明链断成了几段, 不按条带展开
    if len(order) != len(neighbors):
        return None, False
    return order, closed


def _strip_coordinates(loop_a, loop_b, walls, unit):
    """一条侧壁条带的展开坐标: U 是沿第一个环的弧长, V 是到第一个环的距离, 都乘每单位长度的 UV 长度

    第二个环上的顶点取与它相连的第一个环顶点里最近的那个的 U; 闭合环在起点处留一条接缝
    loop_a loop_b 两个边界环的边列表
    walls 这条条带的侧壁面
    unit 网格每单位长度对应的 UV 长度
    返回 ({顶点: (u, v)}, 条带全长 U, 是否闭合); 第一个环有分叉时返回 None
    """
    order, closed = _chain(loop_a)
    if order is None:
        return None
    arc, total = {}, 0.0
    for index, vert in enumerate(order):
        if index:
            total += (vert.co - order[index - 1].co).length * unit
        arc[vert] = total
    if closed:
        total += (order[0].co - order[-1].co).length * unit
    coords = {vert: (arc[vert], 0.0) for vert in order}
    on_a = set(order)
    for vert in {vert for edge in loop_b for vert in edge.verts}:
        # 与第一个环相连的侧壁边 (横档) 里最短的那条
        rungs = [
            edge.other_vert(vert)
            for edge in vert.link_edges
            if edge.other_vert(vert) in on_a and any(face in walls for face in edge.link_faces)
        ]
        if not rungs:
            return None
        nearest = min(rungs, key=lambda other: (other.co - vert.co).length)
        coords[vert] = (arc[nearest], (nearest.co - vert.co).length * unit)
    return coords, total, closed


def _unwrap_strips(bm, strips, unit, flag_layer):
    """把每条侧壁条带展开成独立的长条 UV 岛, 写进每一个 UV 层, 条带上下错开互不重叠

    展开成功的侧壁面在 flag_layer 上记为真, 绑定构建的盒投影据此跳过它们
    bm 一块碎块的 BMesh
    strips _bridge 返回的条带列表
    unit 网格每单位长度对应的 UV 长度
    flag_layer 已展开标记的面层
    """
    layers = list(bm.loops.layers.uv.values())
    offset = 0.0
    for loop_a, loop_b, walls in strips:
        found = _strip_coordinates(loop_a, loop_b, set(walls), unit)
        if found is None:
            continue
        coords, total, closed = found
        height = max(v for _u, v in coords.values())
        for face in walls:
            us = [coords[loop.vert][0] for loop in face.loops]
            # 闭合环跨过接缝的面: 起点附近的角移到条带末端之后, 面不会横跨整条条带
            wrap = closed and max(us) - min(us) > total / 2
            for loop in face.loops:
                u, v = coords[loop.vert]
                if wrap and u < total / 2:
                    u += total
                for layer in layers:
                    loop[layer].uv = (u, offset + v)
            face[flag_layer] = True
        # 下一条条带放在上面, 中间留出与条带同宽的空隙
        offset += 2.0 * height if height > 0.0 else 1.0e-3


def _orient(bm, walls):
    """侧壁面的朝向跟随相邻的原网格面: 共享边在两个面里走向相同说明侧壁反了, 按多数票翻转

    bm 一块碎块的 BMesh
    walls 侧壁面列表
    """
    wall_set = set(walls)
    flip = []
    for face in walls:
        votes = 0
        for loop in face.loops:
            for other in loop.link_loops:
                if other.face in wall_set:
                    continue
                # 一致朝向时相邻两个面在共享边上的起点不同
                votes += 1 if other.vert == loop.vert else -1
        if votes > 0:
            flip.append(face)
    if flip:
        bmesh.ops.reverse_faces(bm, faces=flip)


def _closed(bm, island_layer, paired):
    """配对岛这部分是否封闭且朝向一致: 不应留下开放边, 侧壁两侧的面走向要相反

    bm 一块碎块的 BMesh
    island_layer 面所属 UV 岛序号的面层
    paired 配对岛序号集合
    返回 封闭为真
    """
    for edge in bm.edges:
        faces = edge.link_faces
        if not any(face[island_layer] in paired for face in faces):
            continue
        if len(faces) != 2 or not edge.is_contiguous:
            return False
    return True


def _halves(bm, groups):
    """把一个 BMesh 按分组递归对半拆开, 每个叶子只含一组的面

    每层只复制一次当前的 BMesh, 总代价约为 面数 乘以 组数的对数, 而不是每组复制一遍整个网格
    bm 含这些分组全部面的 BMesh, 带 FACE_LAYER 面层; 交给本函数后由它释放, 中途出错也会释放
    groups [(组序号, Group), ...]
    产出 (组序号, Group, 只含该组面的 BMesh); 产出的 BMesh 由使用方在下一次迭代前用完
    """
    try:
        if len(groups) == 1:
            yield (*groups[0], bm)
            return
        middle = len(groups) // 2
        for part in (groups[:middle], groups[middle:]):
            keep = {face for _index, group in part for face in group.faces}
            half = bm.copy()
            # 面层句柄属于各自的 BMesh, 复制出来的要重新取
            layer = half.faces.layers.int[FACE_LAYER]
            bmesh.ops.delete(
                half, geom=[face for face in half.faces if face[layer] not in keep], context="FACES"
            )
            yield from _halves(half, part)
    finally:
        bm.free()


def _shard_meshes(mesh, groups, island_of, settings, batch, unit):
    """建每块碎块的网格: 只保留本组的面, 在配对岛之间桥接侧壁, 侧壁按断面处理并展开成长条 UV

    mesh 源网格的求值副本, 不改动
    groups Group 列表
    island_of 每个面所属岛的序号列表
    settings 参数值, 用到断面材质与平滑断面
    batch ShardBatch, 新网格登记在它的账本里
    unit 网格每单位长度对应的 UV 长度
    返回 (网格列表与 groups 同序, 没有封闭的组序号集合)
    """
    meshes = [None] * len(groups)
    open_groups = set()
    # 交给 _halves 之后由它释放
    bm = bmesh.new()
    bm.from_mesh(mesh)
    for name in (FACE_LAYER, ISLAND_LAYER):
        bm.faces.layers.int.new(name)
    # 已展开标记要在取面与桥接之前建好: 之后再加层会让已经取到的面引用失效
    if bm.faces.layers.bool.get(shards.INTERIOR_UV_ATTRIBUTE) is None:
        bm.faces.layers.bool.new(shards.INTERIOR_UV_ATTRIBUTE)
    face_layer = bm.faces.layers.int[FACE_LAYER]
    island_layer = bm.faces.layers.int[ISLAND_LAYER]
    # 新建层之后再取面: 加层会让之前取到的面引用失效
    for face in bm.faces:
        face[face_layer] = face.index
        face[island_layer] = island_of[face.index]
        face.hide = False
    for index, group, part in _halves(bm, list(enumerate(groups))):
        island_layer = part.faces.layers.int[ISLAND_LAYER]
        if group.pairs:
            strips, failed = _bridge(part, island_layer, group.pairs)
            walls = [face for _a, _b, made in strips for face in made]
            _orient(part, walls)
            # 朝向修正之后再展开: 翻转面会重排面角, UV 按顶点写在面角上
            _unwrap_strips(part, strips, unit, part.faces.layers.bool[shards.INTERIOR_UV_ATTRIBUTE])
            for face in walls:
                face.hide = True
                face.material_index = settings.material_index
                face.smooth = settings.use_smooth_faces
            paired = {island for pair in group.pairs for island in pair}
            if failed or not _closed(part, island_layer, paired):
                open_groups.add(index)
        # 删掉一个层后其余层的句柄可能失效, 每次都按名字重新取
        for name in (FACE_LAYER, ISLAND_LAYER):
            part.faces.layers.int.remove(part.faces.layers.int[name])
        shard_mesh = batch.new_mesh()
        part.to_mesh(shard_mesh)
        meshes[index] = shard_mesh
    return meshes, open_groups


def _source_mesh(obj, depsgraph, settings, tracker):
    """源物体求值后的网格副本与它的 UV 层名

    obj 源物体
    depsgraph 求值用的依赖图
    settings 参数值, 用到 uv_map
    tracker 回收账本
    返回 (网格数据, 世界矩阵, UV 层名)
    """
    with evaluated_mesh(obj, depsgraph) as (evaluated, mesh):
        if mesh is None or not mesh.polygons:
            raise MeshFractureError('Mesh "{name}" has no faces', name=obj.name)
        if not mesh.uv_layers:
            raise MeshFractureError(NO_UV_MESSAGE, name=obj.name)
        name = settings.uv_map or mesh.uv_layers.active.name
        if name not in mesh.uv_layers:
            raise MeshFractureError(MISSING_UV_MESSAGE, name=obj.name, uv=name)
        copy = tracker.track("meshes", bpy.data.meshes.new_from_object(evaluated))
        return copy, evaluated.matrix_world.copy(), name


@dataclass(frozen=True)
class Plan:
    """一个源物体的拆分计划"""

    # 源物体
    source: object
    # 求值副本与世界矩阵
    mesh: object
    matrix: object
    # Group 列表与每个面所属岛的序号
    groups: list
    island_of: list
    # 被按岛拆开的连通块数与连通块总数
    split_parts: int
    total_parts: int


def _plan(obj, mesh, matrix, uv_name, settings):
    """按 UV 岛给一个源物体分组

    obj 源物体, 用于报错
    mesh 源网格的求值副本, 面的选中状态来自编辑模式里的选择
    matrix 源物体的世界矩阵
    uv_name UV 层名
    settings 参数值, 用到 use_uv_selected 与 uv_unsplit
    返回 Plan
    """
    with mesh_edit(mesh) as bm:
        bm.faces.ensure_lookup_table()
        bm.normal_update()
        layer = bm.loops.layers.uv[uv_name]
        faces_by_component = cleanup.face_components(bm)
        component_of = [0] * len(bm.faces)
        for index, faces in enumerate(faces_by_component):
            for face in faces:
                component_of[face.index] = index
        surface = Surface(
            centers=[face.calc_center_median() for face in bm.faces],
            areas=[face.calc_area() for face in bm.faces],
            normals=[face.normal.copy() for face in bm.faces],
            component_of=component_of,
            island_of=[0] * len(bm.faces),
        )
        islands = _islands(bm, layer, surface)
        for index, island in enumerate(islands):
            for face_index in island.faces:
                surface.island_of[face_index] = index
        selected = None
        if settings.use_uv_selected:
            # 含有任意一个选中面的连通块算选中
            selected = {component_of[face.index] for face in bm.faces if face.select}
            if not selected:
                raise MeshFractureError(NO_SELECTION_MESSAGE, name=obj.name)
        groups, split_parts = _groups(
            bm, islands, surface, faces_by_component, settings.uv_unsplit, selected
        )
    if len(groups) < 2:
        raise MeshFractureError(SINGLE_ISLAND_MESSAGE, name=obj.name)
    return Plan(obj, mesh, matrix, groups, surface.island_of, split_parts, len(faces_by_component))


def _create_shards(plan, naming, settings):
    """由分组建碎块物体: 复制材质, 带上源物体的世界矩阵, 重设原点, 编号与命名, 断面收尾

    plan Plan
    naming NamingPlan
    settings 参数值
    返回 (碎块物体列表与分组同序, 没有封闭的碎块物体列表)
    """
    batch = naming.first_batch()
    # 条带 UV 按世界长度计: 网格在局部坐标里, 乘源物体的平均缩放换成世界长度
    scale = plan.matrix.to_scale()
    unit = settings.strip_uv_scale * (abs(scale.x) + abs(scale.y) + abs(scale.z)) / 3.0
    meshes, open_groups = _shard_meshes(
        plan.mesh, plan.groups, plan.island_of, settings, batch, unit
    )
    cells = []
    for shard_mesh in meshes:
        for material in plan.mesh.materials:
            shard_mesh.materials.append(material)
        # 断面材质索引指向最后一个槽之后时, 碎块多一个断面材质槽, 侧壁就用它
        materials.append_interior_slot(
            shard_mesh, plan.source, settings.material_index, naming.tracker
        )
        cell = batch.new_object(shard_mesh, plan.matrix.translation)
        cell.matrix_world = plan.matrix
        cells.append(cell)
    if settings.use_recenter:
        shard_objects.recenter(cells)
    batch.number(cells)
    naming.apply_names(cells)
    # 侧壁面带隐藏标记: 写断面属性, 断面顶点组与锐边, 然后取消隐藏
    interior.finish_interior(cells, settings)
    return cells, [cells[index] for index in sorted(open_groups)]


@dataclass(frozen=True)
class Outcome:
    """一次按 UV 岛拆分的结果"""

    # 全部碎块, 源到碎块的映射, 源到资产根集合的映射
    shards: list
    source_to_shards: dict
    source_to_asset: dict
    # 应当封闭却没有封闭的碎块
    open_shards: tuple
    # 被按岛拆开的连通块数与连通块总数, 全部源物体相加
    split_parts: int
    total_parts: int
    # 耗时
    seconds: float


def run(context, sources, settings):
    """把每个源物体按 UV 岛拆成碎块并各自建资产; 失败时回收本次新建的数据

    context Blender 上下文, 物体模式
    sources 源物体序列, 非网格物体被忽略, 按名称排序处理
    settings 参数值, 用到 UV 层, 选中部件, 未拆分部件, 断面, 输出集合, 命名与重设原点
    返回 Outcome
    """
    started = perf_counter()
    meshes = sorted((obj for obj in sources if obj.type == "MESH"), key=lambda obj: obj.name_full)
    if context.mode != "OBJECT" or not meshes:
        raise MeshFractureError("Select editable mesh objects in Object Mode")
    for obj in meshes:
        # 材质槽编号 0 总是合法, 其余要么是已有的槽, 要么是最后一个槽之后那个自动新建的断面材质槽
        materials.check_slot(obj, settings.material_index)
    tracker, temp = CreatedData(), CreatedData()
    try:
        depsgraph = context.evaluated_depsgraph_get()
        # 先完成全部源物体的分组, 有一个失败就不会留下半成品
        plans = []
        for obj in meshes:
            mesh, matrix, uv_name = _source_mesh(obj, depsgraph, settings, temp)
            plans.append(_plan(obj, mesh, matrix, uv_name, settings))
        check_limit(sum(len(plan.groups) for plan in plans))
        collection = output_collection(context, settings.collection_name, tracker)
        all_cells, open_shards, source_to_shards, source_to_asset = [], [], {}, {}
        for plan in plans:
            naming = NamingPlan(plan.source, collection, settings, tracker, gap=0.0, hollow=False)
            cells, unclosed = _create_shards(plan, naming, settings)
            all_cells.extend(cells)
            open_shards.extend(unclosed)
            source_to_shards[plan.source] = tuple(cells)
            source_to_asset[plan.source] = naming.root
    except Exception:
        # 回收已经生成的数据
        run_all((tracker.rollback,))
        raise
    finally:
        # 临时网格无论成败都回收
        run_all((temp.rollback,))
    return Outcome(
        all_cells,
        source_to_shards,
        source_to_asset,
        tuple(open_shards),
        sum(plan.split_parts for plan in plans),
        sum(plan.total_parts for plan in plans),
        perf_counter() - started,
    )
