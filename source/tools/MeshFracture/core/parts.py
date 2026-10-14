"""部件转碎块 - 把模型自带的部件直接当作碎块, 不切割, 输出与切割结果一致的资产

一个网格物体时, 它的每个连通块是一个部件; 选中多个物体时, 每个物体是一个部件
部件在模拟里互相穿插会被碰撞体猛地推开, 所以默认让部件互不重叠:
重叠的体积归体积大的部件, 小的部件减去它; 完全埋在别的部件里的部件没有可见几何, 被丢弃
下游的物理, 驱动, 模拟与绑定只认资产清单与碎块标记, 不关心碎块是切出来的还是模型自带的
"""

from dataclasses import dataclass
from time import perf_counter

import bpy
from mathutils.bvhtree import BVHTree

from ....common import shards as shard_contract
from ....common.blender_context import evaluated_mesh, mesh_edit
from ....common.created_data import CreatedData, run_all
from ....common.geometry import is_not_closed
from ....common.hollow import RAY_DIRECTIONS, RAY_HITS
from ..errors import MeshFractureError
from . import cleanup, islands, shard_objects
from .naming import NamingPlan
from .pipeline import check_limit, output_collection

# 只有一个连通块的网格没有部件可言时的错误
SINGLE_PART_MESSAGE = (
    'Mesh "{name}" has only one connected part; use Generate Fragments to cut it, '
    "or select several objects"
)
# 判断面在不在原部件表面上的容差 = 部件包围盒对角线乘以该比例
SURFACE_RATIO = 1.0e-5
# 射线越过命中点的距离 = 部件包围盒对角线乘以该比例
RAY_STEP_RATIO = 1.0e-5


@dataclass
class Part:
    """一个部件: 网格数据在局部坐标里, 世界矩阵单独记"""

    # 网格数据, 局部坐标
    mesh: object
    # 世界矩阵
    matrix: object
    # 来源物体, 用来把碎块对回源物体
    owner: object


def _evaluated_copy(obj, depsgraph, tracker):
    """物体求值后的网格副本, 带修改器的结果

    obj 源物体
    depsgraph 求值用的依赖图
    tracker 回收账本, 副本登记在里面
    返回 (网格数据, 世界矩阵); 没有面时抛 MeshFractureError
    """
    with evaluated_mesh(obj, depsgraph) as (evaluated, mesh):
        if mesh is None or not mesh.polygons:
            raise MeshFractureError('Mesh "{name}" has no faces', name=obj.name)
        copy = tracker.track("meshes", bpy.data.meshes.new_from_object(evaluated))
        return copy, evaluated.matrix_world.copy()


def _split_loose(obj, depsgraph, tracker):
    """一个网格物体按连通块拆成部件, 顺序由面的下标决定, 可复现

    obj 源物体
    depsgraph 求值用的依赖图
    tracker 回收账本
    返回 Part 列表; 只有一个连通块时抛 MeshFractureError
    """
    copy, matrix = _evaluated_copy(obj, depsgraph, tracker)
    with mesh_edit(copy) as bm:
        groups = [{face.index for face in part} for part in cleanup.face_components(bm)]
    if len(groups) < 2:
        raise MeshFractureError(SINGLE_PART_MESSAGE, name=obj.name)
    parts = []
    for keep in sorted(groups, key=min):
        mesh = tracker.track("meshes", copy.copy())
        islands.keep_faces(mesh, keep)
        parts.append(Part(mesh, matrix, obj))
    return parts


def _world_mesh(part, tracker):
    """部件网格的世界坐标副本

    part Part
    tracker 回收账本
    返回 网格数据
    """
    mesh = tracker.track("meshes", part.mesh.copy())
    mesh.transform(part.matrix)
    return mesh


def _tree(mesh):
    """世界坐标网格的求交树

    mesh 网格数据, 顶点已在世界坐标
    返回 BVHTree
    """
    return BVHTree.FromPolygons(
        [vert.co.copy() for vert in mesh.vertices], [tuple(poly.vertices) for poly in mesh.polygons]
    )


def _volume(mesh):
    """网格体积的绝对值; 不封闭的部件按带符号体积近似

    mesh 网格数据
    返回 体积
    """
    with mesh_edit(mesh) as bm:
        return abs(bm.calc_volume(signed=True))


def _is_open(mesh):
    """网格是否不封闭或非流形

    mesh 网格数据
    返回 不封闭为真
    """
    with mesh_edit(mesh) as bm:
        return is_not_closed(bm)


def _diagonal(mesh):
    """网格包围盒的体对角线长度

    mesh 网格数据
    返回 长度
    """
    coords = [vert.co for vert in mesh.vertices]
    sizes = [max(co[axis] for co in coords) - min(co[axis] for co in coords) for axis in range(3)]
    return sum(size * size for size in sizes) ** 0.5


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


def _cutters(worlds, trees, order):
    """每个部件要减去的部件: 体积更大, 并且表面相交或者整个包住它

    worlds 各部件的世界网格
    trees 各部件的世界求交树
    order 部件序号按体积从大到小的顺序, 体积相同时按序号
    返回 {部件序号: [要减去的部件序号, ...]}, 没有要减的部件不在其中
    """
    result = {}
    for position, target in enumerate(order):
        step = max(_diagonal(worlds[target]) * RAY_STEP_RATIO, 1.0e-9)
        probe = worlds[target].vertices[0].co
        found = [
            larger
            for larger in order[:position]
            # 表面相交, 或者整个埋在更大的部件里 (表面不相交但一个顶点在里面)
            if trees[target].overlap(trees[larger]) or _inside(trees[larger], probe, step)
        ]
        if found:
            result[target] = found
    return result


def _carve(context, worlds, plan, open_mesh, temp):
    """每个部件减去体积更大的重叠部件, 所有布尔在同一次视图层刷新里求值

    刀具用部件的原始几何, 与被减的物体分开建, 结果不依赖部件的处理顺序
    context Blender 上下文
    worlds 各部件的世界网格
    plan _cutters 的结果
    open_mesh 是否有不封闭的部件, 为真时启用容错求解
    temp 临时数据的回收账本
    返回 {部件序号: 减完之后的世界网格 或 None}, None 表示整个部件被减空
    """
    # 刀具物体: 每个被用到的部件一份, 多个被减的部件共用
    knives = {}

    def knife(index):
        """部件序号对应的刀具物体, 第一次用到时建立并链接进场景"""
        if index not in knives:
            mesh = temp.track("meshes", worlds[index].copy())
            obj = temp.track("objects", bpy.data.objects.new(f"knife_{index}", mesh))
            context.scene.collection.objects.link(obj)
            knives[index] = obj
        return knives[index]

    targets = {}
    for target, cutters in plan.items():
        holder = temp.track("collections", bpy.data.collections.new(f"cutters_{target}"))
        context.scene.collection.children.link(holder)
        for cutter in cutters:
            holder.objects.link(knife(cutter))
        mesh = temp.track("meshes", worlds[target].copy())
        obj = temp.track("objects", bpy.data.objects.new(f"target_{target}", mesh))
        context.scene.collection.objects.link(obj)
        modifier = obj.modifiers.new(name="Boolean", type="BOOLEAN")
        modifier.operation = "DIFFERENCE"
        modifier.operand_type = "COLLECTION"
        modifier.collection = holder
        modifier.solver = "EXACT"
        # 刀具之间也互相重叠, 自交处理按绕数把它们当作并集
        modifier.use_self = True
        modifier.use_hole_tolerant = open_mesh
        # 刀具带来的断面按各自的材质转入结果, 部件的材质槽可以不同
        modifier.material_mode = "TRANSFER"
        targets[target] = obj
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    results = {}
    for target, obj in targets.items():
        with evaluated_mesh(obj, depsgraph) as (evaluated, mesh):
            filled = mesh is not None and len(mesh.polygons) > 0
            results[target] = bpy.data.meshes.new_from_object(evaluated) if filled else None
    return results


def _mark_interior(mesh, original, tolerance):
    """写断面面属性: 不在原部件表面上的面是减掉重叠体积时新生成的断面

    mesh 减完之后的世界网格
    original 原部件的世界求交树
    tolerance 在表面上的容差
    """
    flags = []
    for polygon in mesh.polygons:
        distance = original.find_nearest(polygon.center)[3]
        flags.append(distance is None or distance > tolerance)
    layer = mesh.attributes.new(shard_contract.INTERIOR_ATTRIBUTE, "BOOLEAN", "FACE")
    layer.data.foreach_set("value", flags)


def _ensure_interior(mesh):
    """没有断面面属性的部件补上全为假的属性: 部件的面都是模型自带的, 没有切割断面

    mesh 网格数据
    """
    if mesh.attributes.get(shard_contract.INTERIOR_ATTRIBUTE) is None:
        layer = mesh.attributes.new(shard_contract.INTERIOR_ATTRIBUTE, "BOOLEAN", "FACE")
        layer.data.foreach_set("value", [False] * len(mesh.polygons))


def _separate(context, parts, temp):
    """让部件互不重叠: 重叠的体积归体积大的部件, 被减空的部件丢弃

    部件本身带非流形边或自相交时, 求差会留下少量残余的重叠 (金蛋实测: 重叠体积减少约八成, 再来一遍也不再减少)
    context Blender 上下文
    parts Part 列表, 其中被减过的部件原地换上新网格
    temp 临时数据的回收账本
    返回 保留下来的 Part 列表, 顺序不变
    """
    worlds = [_world_mesh(part, temp) for part in parts]
    trees = [_tree(mesh) for mesh in worlds]
    volumes = [_volume(mesh) for mesh in worlds]
    order = sorted(range(len(parts)), key=lambda index: (-volumes[index], index))
    plan = _cutters(worlds, trees, order)
    if not plan:
        return parts
    carved = _carve(context, worlds, plan, any(_is_open(mesh) for mesh in worlds), temp)
    kept = []
    for index, part in enumerate(parts):
        if index not in plan:
            kept.append(part)
            continue
        mesh = carved[index]
        # 整个部件被更大的部件包住: 没有可见几何, 也没有可模拟的体积
        if mesh is None:
            continue
        _mark_interior(mesh, trees[index], max(_diagonal(worlds[index]) * SURFACE_RATIO, 1.0e-9))
        # 结果在世界坐标, 换回部件的局部坐标
        mesh.transform(part.matrix.inverted())
        part.mesh = temp.track("meshes", mesh)
        kept.append(part)
    return kept


def _proxy_source(context, owners, depsgraph, temp):
    """选中多个物体时, 把它们合成一个物体作为资产的源物体: 原物体切换与弹道都要一个完整的源

    context Blender 上下文
    owners 各部件的来源物体
    depsgraph 求值用的依赖图
    temp 临时数据的回收账本, 副本网格登记在里面
    返回 合成的源物体, 不在任何集合里, 由调用方链接与登记
    """
    copies = []
    try:
        for obj in owners:
            mesh, matrix = _evaluated_copy(obj, depsgraph, temp)
            copy = bpy.data.objects.new(obj.name, mesh)
            copy.matrix_world = matrix
            context.scene.collection.objects.link(copy)
            copies.append(copy)
        first = copies[0]
        with context.temp_override(
            active_object=first,
            object=first,
            selected_objects=copies,
            selected_editable_objects=copies,
        ):
            bpy.ops.object.join()
    except Exception:
        # join 之前出错时, 副本物体还在场景里
        for copy in copies:
            if copy.name in bpy.data.objects:
                bpy.data.objects.remove(copy)
        raise
    context.scene.collection.objects.unlink(first)
    first.name = f"{owners[0].name}_Whole"
    first.data.name = first.name
    return first


def _create_shards(parts, naming, settings):
    """由部件建碎块物体: 复制网格与材质, 带上部件的世界矩阵, 重设原点, 编号与命名

    parts 保留下来的 Part 列表
    naming NamingPlan
    settings 参数值
    返回 碎块物体列表, 与 parts 同序
    """
    batch = naming.first_batch()
    cells = []
    for part in parts:
        _ensure_interior(part.mesh)
        mesh = batch.new_mesh()
        with mesh_edit(part.mesh) as bm:
            bm.to_mesh(mesh)
        for material in part.mesh.materials:
            mesh.materials.append(material)
        cell = batch.new_object(mesh, part.matrix.translation)
        cell.matrix_world = part.matrix
        cells.append(cell)
    if settings.use_recenter:
        shard_objects.recenter(cells)
    batch.number(cells)
    naming.apply_names(cells)
    return cells


def run(context, sources, settings):
    """把源物体的部件转成碎块并建资产; 失败时回收本次新建的数据

    context Blender 上下文, 物体模式
    sources 源物体序列, 非网格物体被忽略: 一个物体时按连通块拆, 多个物体时每个物体一个部件
    settings 参数值, 用到输出集合, 命名, 重设原点与是否让部件互不重叠
    返回 (碎块, 源到碎块的映射, 源到资产根集合的映射, 耗时)
    """
    started = perf_counter()
    meshes = sorted((obj for obj in sources if obj.type == "MESH"), key=lambda obj: obj.name_full)
    if context.mode != "OBJECT" or not meshes:
        raise MeshFractureError("Select editable mesh objects in Object Mode")
    tracker, temp = CreatedData(), CreatedData()
    try:
        depsgraph = context.evaluated_depsgraph_get()
        if len(meshes) == 1:
            parts = _split_loose(meshes[0], depsgraph, temp)
        else:
            parts = []
            for obj in meshes:
                mesh, matrix = _evaluated_copy(obj, depsgraph, temp)
                parts.append(Part(mesh, matrix, obj))
        if settings.use_parts_disjoint:
            parts = _separate(context, parts, temp)
        if not parts:
            raise MeshFractureError(
                'No valid shards were produced for "{name}"', name=meshes[0].name
            )
        check_limit(len(parts))
        collection = output_collection(context, settings.collection_name, tracker)
        # 源物体: 一个物体时就是它, 多个物体时合成一个放进资产根集合
        if len(meshes) == 1:
            source = meshes[0]
        else:
            source = tracker.track("objects", _proxy_source(context, meshes, depsgraph, temp))
            tracker.track("meshes", source.data)
        naming = NamingPlan(source, collection, settings, tracker, gap=0.0, hollow=False)
        if len(meshes) > 1:
            naming.root.objects.link(source)
            source.hide_render = True
            source.hide_set(True)
        cells = _create_shards(parts, naming, settings)
        by_owner = {}
        for part, cell in zip(parts, cells, strict=True):
            by_owner.setdefault(part.owner, []).append(cell)
        source_to_shards = {owner: tuple(found) for owner, found in by_owner.items()}
        source_to_asset = dict.fromkeys(source_to_shards, naming.root)
    except Exception:
        # 回收已经生成的数据
        run_all((tracker.rollback,))
        raise
    finally:
        # 临时物体, 网格与集合无论成败都回收
        run_all((temp.rollback,))
    return cells, source_to_shards, source_to_asset, perf_counter() - started
