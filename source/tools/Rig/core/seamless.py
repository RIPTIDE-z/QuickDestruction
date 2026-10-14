"""静止整体 - 设计上静止 (锚定或排除) 的碎块合成一整块, 表面与未破碎的源物体一致

静止的碎块不必逐块处理, 它们代表的就是源物体的一部分, 所以从源物体出发而不是从碎片出发:
    裁剪 - 源物体先裁到静止碎块所在的范围, 范围外的碎块不必参与
    外扩 - 范围内的非静止碎块, 每个顶点按相邻面法线求位移, 使每个相邻面的平面各自外移外扩距离, 断面的权重远大于外表面
    相减 - 外扩后的碎块相邻处互相重叠, 不留间隙, 源物体里没有薄膜残留; 外表面就是源物体自己的几何
    清理 - 去掉不挨着静止碎块的残渣碎片, 标记断面
实测从静止碎块出发 (外扩静止碎块再与源物体求交) 在碎块交汇处会留下口袋状的残缺, 面数也多, 所以不用
"""

import bmesh
import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from ....common import asset
from ....common.blender_context import evaluated_mesh, mesh_edit
from ....common.created_data import CreatedData
from ....common.geometry import is_not_closed
from ....common.hollow import reversed_faces
from ....common.shards import INTERIOR_ATTRIBUTE
from ..errors import RigError
from . import merge

# 外扩距离 = 切割间隙乘以该比例: 相邻碎块各外扩一点, 重叠大约 0.6 个间隙, 实测间隙有波动也能盖住;
# 不取 1: 外扩的面会与邻块自己的面正好共面, Exact 求交在浮点误差下得到碎裂的结果 (圆环上实测)
INFLATE_RATIO = 0.8
# 外扩距离的下限 = 包围盒对角线乘以该比例: 没有间隙时也要让相邻碎块有一点重叠
MIN_INFLATE_RATIO = 2.0e-4
# 顶点位移长度的上限, 以外扩距离为单位: 尖锐的夹角处按相邻面求出的位移会很长
MAX_MITER = 4.0
# 断面法线的权重, 外表面法线的权重为 1: 保证断面的位置优先于外表面
CUT_WEIGHT = 1000.0
# 求位移的二次型加上的对角项, 相对于权重之和: 只有一两个独立法线时让求解仍然有解
RIDGE = 1.0e-6
# 判断面在不在源物体表面上的容差 = 包围盒对角线乘以该比例
SURFACE_RATIO = 1.0e-5
# 残渣判定的距离 = 切割间隙乘以该比例加容差: 离所有静止碎块都更远的连通块不是静止整体的一部分
DEBRIS_REACH_RATIO = 2.0

# 源物体被删除时的错误
NO_SOURCE_MESSAGE = 'The source object of "{name}" was deleted; merging anchored shards needs it'
# 求交没有产出时的错误
EMPTY_MESSAGE = "Merging the anchored shards of {name} produced no geometry"


def _plane_system(bm, tree, tolerance):
    """每个面的法线与权重: 与源物体表面重合的是外表面, 权重 1; 其余是断面, 权重大得多

    bm 碎块网格的 BMesh
    tree 源物体的世界求交树
    tolerance 在表面上的容差
    返回 {面: (法线, 权重)}, 退化面没有法线, 不在其中
    """
    bm.normal_update()
    planes = {}
    for face in bm.faces:
        if face.normal.length < 0.5:
            continue
        _location, _normal, _index, distance = tree.find_nearest(face.calc_center_median())
        on_surface = distance is not None and distance <= tolerance
        planes[face] = (face.normal.copy(), 1.0 if on_surface else CUT_WEIGHT)
    return planes


def inflate(mesh, tree, tolerance, distance):
    """把碎块网格外扩: 每个顶点按相邻面求位移, 使每个相邻面的平面各自外移 distance

    位移是加权最小二乘的解 M d = distance * sum(w n), M = sum(w n n^T); 一个面时位移就是 distance 乘法线,
    两个面交界处正好各移出 distance, 角点处同理; 断面权重远大于外表面, 外表面只是顺带外移, 之后会被源物体裁掉
    mesh 碎块网格数据, 顶点已在世界坐标, 原地改写
    tree 源物体的世界求交树, 用来区分外表面与断面
    tolerance 在表面上的容差
    distance 外扩距离
    """
    limit = MAX_MITER * distance
    with mesh_edit(mesh) as bm:
        planes = _plane_system(bm, tree, tolerance)
        deltas = []
        for vert in bm.verts:
            system = Matrix(((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
            pull = Vector()
            weight_sum = 0.0
            for face in vert.link_faces:
                if face not in planes:
                    continue
                normal, weight = planes[face]
                weight_sum += weight
                pull += normal * weight
                for row in range(3):
                    for column in range(3):
                        system[row][column] += weight * normal[row] * normal[column]
            if weight_sum == 0.0:
                deltas.append(Vector())
                continue
            for axis in range(3):
                system[axis][axis] += RIDGE * weight_sum
            delta = system.inverted_safe() @ (pull * distance)
            if delta.length > limit:
                delta *= limit / delta.length
            deltas.append(delta)
        for vert, delta in zip(bm.verts, deltas, strict=True):
            vert.co += delta
        bm.to_mesh(mesh)


class _Source:
    """源物体求值后的临时副本与它的几何量, 一次合并共用"""

    def __init__(self, context, source, tracker, hollow):
        """建临时副本: 带修改器的结果, 在场景里可见, 才能作为布尔操作数

        context Blender 上下文
        source 源物体, 可以是隐藏的
        tracker 创建账本, 副本的物体与网格登记在里面
        hollow 切割时把嵌套的内层曲面当作空腔时为真: 副本里的内层曲面要翻成朝内, 内腔才不会被当成实体
        """
        depsgraph = context.evaluated_depsgraph_get()
        evaluated = source.evaluated_get(depsgraph)
        mesh = tracker.track("meshes", bpy.data.meshes.new_from_object(evaluated))
        if hollow:
            with mesh_edit(mesh) as bm:
                flipped = reversed_faces(bm)
                if flipped:
                    bmesh.ops.reverse_faces(bm, faces=flipped)
                    bm.to_mesh(mesh)
        # 副本物体, 世界矩阵与源物体一致, 网格仍在局部坐标
        self.copy = tracker.track("objects", bpy.data.objects.new(f"{mesh.name}_source", mesh))
        self.copy.matrix_world = evaluated.matrix_world.copy()
        context.scene.collection.objects.link(self.copy)
        self.matrix = self.copy.matrix_world.copy()
        vertices = [self.matrix @ vert.co for vert in mesh.vertices]
        # 世界坐标的求交树, 区分外表面与断面用
        self.tree = BVHTree.FromPolygons(vertices, [tuple(poly.vertices) for poly in mesh.polygons])
        with mesh_edit(mesh) as bm:
            # 开放或非流形的源物体启用容错求解
            self.open_mesh = is_not_closed(bm)
        sizes = [max(v[i] for v in vertices) - min(v[i] for v in vertices) for i in range(3)]
        # 包围盒体对角线, 本次合并的整体尺度
        self.diagonal = Vector(sizes).length
        # 判断面在不在表面上的容差
        self.tolerance = self.diagonal * SURFACE_RATIO


def _inflated_object(context, items, ordinals, tracker, source, distance):
    """外扩一组碎块并合成一个物体, 顶点在世界坐标

    context Blender 上下文
    items 资产的全部碎块
    ordinals 要外扩的碎块序号
    tracker 创建账本
    source _Source
    distance 外扩距离
    返回 合成后的物体
    """
    objects = []
    for ordinal in ordinals:
        shard = items[ordinal]
        mesh = tracker.track("meshes", shard.data.copy())
        # 碎块网格变换到静止世界姿态, 与蒙皮网格里的它完全一致
        mesh.transform(asset.rest_matrix(shard))
        inflate(mesh, source.tree, source.tolerance, distance)
        obj = tracker.track("objects", bpy.data.objects.new(f"{mesh.name}_part", mesh))
        context.scene.collection.objects.link(obj)
        objects.append(obj)
    return merge.join_objects(context, objects)


def _evaluate(context, obj, name):
    """物体求值后的网格副本, 不在创建账本里

    context Blender 上下文
    obj 带布尔修改器的物体
    name 资产名, 求值为空时用在错误里
    返回 网格数据; 求值没有面时抛 RigError
    """
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    with evaluated_mesh(obj, depsgraph) as (evaluated, mesh):
        if mesh is None or not mesh.polygons:
            raise RigError(EMPTY_MESSAGE, name=name)
        return bpy.data.meshes.new_from_object(evaluated)


def _boolean(obj, operation, operand, source):
    """给物体加布尔修改器

    obj 被求值的物体
    operation DIFFERENCE 或 INTERSECT
    operand 另一个操作数物体
    source _Source, 提供是否容错求解
    """
    modifier = obj.modifiers.new(name="Boolean", type="BOOLEAN")
    modifier.operation = operation
    modifier.solver = "EXACT"
    modifier.object = operand
    # 自交处理按绕数求值, 互相重叠的外扩碎块就成了并集
    modifier.use_self = True
    modifier.use_hole_tolerant = source.open_mesh


def _world_box(shard):
    """碎块在静止姿态下的世界包围盒

    shard 碎块物体
    返回 (最小角点, 最大角点)
    """
    rest = asset.rest_matrix(shard)
    corners = [rest @ Vector(corner) for corner in shard.bound_box]
    low = Vector(tuple(min(point[axis] for point in corners) for axis in range(3)))
    high = Vector(tuple(max(point[axis] for point in corners) for axis in range(3)))
    return low, high


def _clip_bounds(items, base, pad):
    """静止碎块的世界包围盒向外扩 pad: 静止整体一定在它里面, 它的面不会碰到静止整体

    items 资产的全部碎块
    base 静止碎块的序号
    pad 外扩距离
    返回 (最小角点, 最大角点)
    """
    boxes = [_world_box(items[ordinal]) for ordinal in base]
    low = Vector(tuple(min(box[0][axis] for box in boxes) - pad for axis in range(3)))
    high = Vector(tuple(max(box[1][axis] for box in boxes) + pad for axis in range(3)))
    return low, high


def _reaches(shard, bounds, margin):
    """碎块外扩 margin 之后是否碰到包围盒

    shard 碎块物体
    bounds (最小角点, 最大角点)
    margin 碎块外扩的距离
    返回 碰到为真
    """
    low, high = _world_box(shard)
    return all(
        low[axis] - margin <= bounds[1][axis] and high[axis] + margin >= bounds[0][axis]
        for axis in range(3)
    )


def _box_object(context, tracker, bounds):
    """与世界轴对齐的长方体物体, 顶点在世界坐标, 用来裁出静止整体所在的范围

    context Blender 上下文
    tracker 创建账本
    bounds (最小角点, 最大角点)
    返回 物体
    """
    low, high = bounds
    mesh = tracker.track("meshes", bpy.data.meshes.new("clip"))
    with mesh_edit() as bm:
        bmesh.ops.create_cube(bm, size=1.0)
        # 单位立方体缩放到包围盒的尺寸, 再平移到中心
        center, size = (low + high) / 2.0, high - low
        bm.transform(Matrix.LocRotScale(center, None, size))
        bm.to_mesh(mesh)
    obj = tracker.track("objects", bpy.data.objects.new("clip", mesh))
    context.scene.collection.objects.link(obj)
    return obj


def _carve(context, items, base, others, tracker, source, distance, name):
    """源物体先裁到静止碎块所在的范围, 再减去范围内外扩后的非静止碎块, 得到静止整体

    裁剪范围是静止碎块的包围盒向外扩两个外扩距离; 不碰到这个范围的碎块不会影响结果, 不必参与求交,
    所以耗时只随静止碎块周围的碎块数增长, 不随整个资产的碎块数增长
    context Blender 上下文
    items 资产的全部碎块
    base 静止碎块的序号
    others 非静止碎块的序号
    tracker 创建账本
    source _Source
    distance 外扩距离
    name 资产名
    返回 世界坐标的网格数据, 不在创建账本里
    """
    bounds = _clip_bounds(items, base, 2.0 * distance)
    _boolean(source.copy, "INTERSECT", _box_object(context, tracker, bounds), source)
    near = [ordinal for ordinal in others if _reaches(items[ordinal], bounds, distance)]
    if near:
        cutter = _inflated_object(context, items, near, tracker, source, distance)
        _boolean(source.copy, "DIFFERENCE", cutter, source)
    mesh = _evaluate(context, source.copy, name)
    # 结果在源物体的局部坐标里, 换到世界坐标
    mesh.transform(source.matrix)
    return mesh


def _rest_tree(items, ordinals):
    """一组碎块在静止姿态下的世界求交树

    items 资产的全部碎块
    ordinals 碎块序号
    返回 BVHTree
    """
    vertices, polygons = [], []
    for ordinal in ordinals:
        shard = items[ordinal]
        rest = asset.rest_matrix(shard)
        base = len(vertices)
        vertices += [rest @ vert.co for vert in shard.data.vertices]
        polygons += [tuple(base + index for index in poly.vertices) for poly in shard.data.polygons]
    return BVHTree.FromPolygons(vertices, polygons)


def _drop_debris(mesh, tree, reach):
    """删掉离静止碎块都很远的连通块: 它们是会动碎块之间的薄膜碎片, 不是静止整体的一部分

    mesh 静止整体的网格数据, 在世界坐标, 原地改写
    tree 静止碎块的世界求交树
    reach 判定距离
    """
    with mesh_edit(mesh) as bm:
        seen = set()
        doomed = []
        for start in bm.verts:
            if start in seen:
                continue
            seen.add(start)
            stack, component = [start], []
            while stack:
                vert = stack.pop()
                component.append(vert)
                for edge in vert.link_edges:
                    other = edge.other_vert(vert)
                    if other not in seen:
                        seen.add(other)
                        stack.append(other)
            # 任一顶点挨着静止碎块就保留整个连通块
            if not any((tree.find_nearest(vert.co)[3] or 0.0) <= reach for vert in component):
                doomed.extend(component)
        if doomed:
            bmesh.ops.delete(bm, geom=doomed, context="VERTS")
            bm.to_mesh(mesh)


def _mark_interior(mesh, tree, tolerance):
    """写断面面属性: 不在源物体表面上的面就是断面

    mesh 静止整体的网格数据, 在世界坐标
    tree 源物体的世界求交树
    tolerance 在表面上的容差
    """
    flags = []
    for polygon in mesh.polygons:
        distance = tree.find_nearest(polygon.center)[3]
        flags.append(distance is None or distance > tolerance)
    layer = mesh.attributes.new(INTERIOR_ATTRIBUTE, "BOOLEAN", "FACE")
    layer.data.foreach_set("value", flags)


def build_base(context, root, items, base, tracker):
    """求静止整体的网格: 与未破碎的源物体一致, 在世界坐标, 带断面面属性

    从源物体出发: 裁到静止碎块所在的范围, 再减去范围内外扩后的非静止碎块, 静止碎块自己的几何不参与
    临时物体与网格无论成败都回收, 结果网格登记在 tracker 里
    context Blender 上下文
    root 资产根集合
    items 资产的全部碎块, 按路径排序
    base 静止碎块的序号, 不能为空
    tracker 创建账本, 结果网格登记在里面
    返回 网格数据; 源物体已被删除或结果没有几何时抛 RigError
    """
    source_object = asset.source_of(root)
    if source_object is None:
        raise RigError(NO_SOURCE_MESSAGE, name=root.name)
    name = root.name
    chosen = set(base)
    others = [ordinal for ordinal in range(len(items)) if ordinal not in chosen]
    temp = CreatedData()
    result = None
    try:
        source = _Source(context, source_object, temp, asset.hollow_of(root))
        distance = max(INFLATE_RATIO * asset.gap_of(root), MIN_INFLATE_RATIO * source.diagonal)
        result = _carve(context, items, sorted(chosen), others, temp, source, distance, name)
        reach = DEBRIS_REACH_RATIO * asset.gap_of(root) + source.tolerance
        _drop_debris(result, _rest_tree(items, sorted(chosen)), reach)
        if not result.polygons:
            raise RigError(EMPTY_MESSAGE, name=name)
        _mark_interior(result, source.tree, source.tolerance)
    except Exception:
        if result is not None:
            bpy.data.meshes.remove(result)
        raise
    finally:
        # 临时物体与网格无论成败都回收
        temp.rollback()
    return tracker.track("meshes", result)
