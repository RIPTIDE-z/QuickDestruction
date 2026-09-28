"""流程编排 - 逐源首次切割与递归细分, 统一命名和断面收尾, 失败时回收本次新建数据"""

from time import perf_counter

import bpy
from mathutils import Vector

from ....common.created_data import CreatedData
from ..constants import SHARD_LIMIT
from ..errors import MeshFractureError
from . import boolean, mesh_ops
from .naming import NamingPlan

# 超出碎块硬上限时的消息
LIMIT_MESSAGE = (
    "More than {limit} shards would be generated; lower Max Pieces, Max per Shard or Rounds"
)


def _check_sources(context, sources, settings):
    """动手之前完成全部前置检查, 参数非法时不会留下半成品"""
    if context.mode != "OBJECT" or not sources:
        raise MeshFractureError("Select editable mesh objects in Object Mode")
    index = settings["material_index"]
    for obj in sources:
        if obj.data.library is not None and mesh_ops.uses_interior(settings):
            # 标记断面时要临时改写源网格的面隐藏标记, 链接库里的网格不可写
            raise MeshFractureError(
                'Mesh data of "{name}" is linked from a library; '
                "turn off Sharp Edges and Interior VGroup to cut it",
                name=obj.name,
            )
        if not obj.data.polygons:
            raise MeshFractureError('Mesh "{name}" has no faces', name=obj.name)
        if index > 0 and index >= len(obj.material_slots):
            raise MeshFractureError(
                'Material slot {index} does not exist on "{name}"', index=index, name=obj.name
            )


def _visible_collection(context, collection):
    """集合在当前视图层里是否可见; 视图层的集合树与 bpy.data 是两套结构, 必须按层节点判断"""

    def visit(layer):
        """递归查找目标集合对应的层节点, 命中即可短路"""
        return (layer.collection == collection and layer.is_visible) or any(
            visit(child) for child in layer.children
        )

    return visit(context.view_layer.layer_collection)


def _output_collection(context, name, tracker):
    """解析输出集合: 空名用当前集合, 否则按名查找, 找不到时在当前集合下新建"""
    collection = bpy.data.collections.get(name) if name else context.collection
    if collection is None:
        collection = tracker.track("collections", bpy.data.collections.new(name))
        context.collection.children.link(collection)
        context.view_layer.update()
    if collection.library or not _visible_collection(context, collection):
        raise MeshFractureError(
            "Output collection must be editable and visible in the current view layer"
        )
    return collection


def _check_limit(count):
    """碎块数超过硬上限时报错, 由 run 回收已经生成的数据"""
    if count > SHARD_LIMIT:
        raise MeshFractureError(LIMIT_MESSAGE, limit=SHARD_LIMIT)


def _cut(context, obj, settings, batch, rng):
    """对单个物体执行一次切割, 返回按路径编号的碎块; 源物体的显示方式与面隐藏状态无论成败都还原"""
    old_display = obj.display_type
    # 只有首次切割且需要标记断面时才会清掉源网格的面隐藏标记, 其余情况不写源网格
    old_hidden = None
    if batch.first and mesh_ops.uses_interior(settings):
        old_hidden = [face.hide for face in obj.data.polygons]
    try:
        if settings["use_debug_redraw"]:
            # 线框显示方便观察切割进度, 结果不受影响
            obj.display_type = "WIRE"
        # 求值快照每次切割只取一次, 胞体构造与布尔求交共用
        snapshot = boolean.inspect_source(obj, context.evaluated_depsgraph_get())
        points = mesh_ops.seed_points(context, obj, settings, batch.first, rng)
        # 种子点数就已超限时, 在耗时的布尔求交之前报错
        _check_limit(len(points))
        cells = mesh_ops.build_cells(context, obj, snapshot, points, settings, batch, rng)
        cells = mesh_ops.intersect_cells(context, obj, snapshot, cells, settings, batch)
        if settings["use_recenter"]:
            mesh_ops.recenter(cells)
        batch.number(cells)
        return cells
    finally:
        obj.display_type = old_display
        if old_hidden is not None:
            obj.data.polygons.foreach_set("hide", old_hidden)
            obj.data.update()


def _select_recursion(cells, settings, cursor, rng):
    """按每轮细分比例与优先细分方式挑出本轮参与递归的碎块, 数量向下取整"""
    fraction = settings["recursion_chance"]
    selected = list(cells)
    if fraction == 1:
        # 全选时不消耗随机流
        return selected
    mode = settings["recursion_chance_select"]
    if mode == "RANDOM":
        # 洗牌后取前 N 个, 等价于无放回随机抽取
        rng.shuffle(selected)
    elif mode.startswith("SIZE"):
        # 按世界空间包围盒对角线的平方排序, 省去开方
        selected.sort(
            key=lambda obj: (
                (
                    obj.matrix_world @ Vector(obj.bound_box[0])
                    - obj.matrix_world @ Vector(obj.bound_box[6])
                ).length_squared
            ),
            reverse=mode == "SIZE_MAX",
        )
    else:
        # 按物体原点与 3D 游标的距离排序
        selected.sort(
            key=lambda obj: (obj.matrix_world.translation - cursor).length_squared,
            reverse=mode == "CURSOR_MAX",
        )
    return selected[: int(fraction * len(selected))]


def _finish(cells, settings, naming):
    """全部切割结束后的收尾: 合并共面面, 清理空集合, 统一命名与断面标记; 保留的父碎块一并处理"""
    debug = settings["use_debug_bool"]
    objects = cells + naming.parents
    if objects and not debug:
        mesh_ops.dissolve_final_shards(objects)
    naming.prune()
    naming.apply_names(objects)
    if objects and not debug and mesh_ops.uses_interior(settings):
        mesh_ops.finish_interior(objects, settings)


def main_object(context, collection, obj, settings, rng, tracker, done):
    """对单个源物体做首次切割并按需递归, 最后统一收尾, 返回最终碎块

    done 是之前的源已经生成的碎块数; 被细分的父块总是离开碎块列表, 删除或移进父碎块集合
    """
    naming = NamingPlan(obj.name, collection, settings, tracker)
    cells = _cut(context, obj, settings, naming.first_batch(), rng)
    _check_limit(done + len(cells))
    threshold = settings["recursion_clamp"]
    # 保留布尔修改器时递归会让引用失效, 直接跳过
    rounds = 0 if settings["use_debug_bool"] else settings["recursion"]
    for _round in range(rounds):
        if not cells or (threshold and len(cells) >= threshold):
            break
        candidates = _select_recursion(cells, settings, context.scene.cursor.location, rng)
        if not candidates:
            break
        for parent in candidates:
            # 每个父块细分前再判断一次阈值; 单次细分或孤岛拆分仍可能超出
            if threshold and len(cells) >= threshold:
                break
            batch = naming.next_batch(parent)
            children = _cut(context, parent, settings, batch, rng)
            if not children:
                # 细分没有产出时保留父块, 删除刚建的空组集合
                naming.drop(batch)
                continue
            cells.extend(children)
            _check_limit(done + len(cells))
            # 父块已被子块取代, 不再参与之后的递归与阈值计数
            cells.remove(parent)
            if settings["use_keep_parents"]:
                naming.keep_parent(parent)
            else:
                # 删除被替换的父块, 避免父子几何重叠
                mesh_ops.remove_generated_object(parent)
    _finish(cells, settings, naming)
    return cells


def run(context, sources, settings, rng):
    """按名称排序显式源物体并逐个切割, 返回碎块, 来源映射与耗时; 失败时回收本次新建数据"""
    # 固定多源处理顺序, 选择顺序不会改变随机流的分配
    sources = sorted((obj for obj in sources if obj.type == "MESH"), key=lambda obj: obj.name_full)
    _check_sources(context, sources, settings)
    # 只记录并回收本次显式创建的数据
    tracker = CreatedData()
    started = perf_counter()
    context.window_manager.progress_begin(0, len(sources))
    try:
        collection = _output_collection(context, settings["collection_name"], tracker)
        cells = []
        source_to_shards = {}
        for index, obj in enumerate(sources):
            created = main_object(context, collection, obj, settings, rng, tracker, len(cells))
            if not created:
                raise MeshFractureError('No valid shards were produced for "{name}"', name=obj.name)
            cells.extend(created)
            source_to_shards[obj] = tuple(created)
            context.window_manager.progress_update(index + 1)
        return cells, source_to_shards, perf_counter() - started
    except Exception:
        tracker.rollback()
        raise
    finally:
        # 进度条必须收尾, 否则界面会一直停在进度状态
        context.window_manager.progress_end()
