"""流程编排 - 逐源首次切割与递归细分, 统一命名和断面收尾, 失败时回收本次新建数据"""

from time import perf_counter

import bpy

from ....common.created_data import CreatedData
from ..constants import SHARD_LIMIT
from ..errors import MeshFractureError
from ..settings import (
    bakes_booleans,
    finishes_interior,
    first_cut_limit,
    recursion_rounds,
    uses_interior,
)
from . import (
    boolean,
    cell_objects,
    cleanup,
    interior,
    intersect,
    recursion,
    sampling,
    shard_objects,
)
from .naming import NamingPlan

# 超出碎块硬上限时的消息
LIMIT_MESSAGE = (
    "More than {limit} shards would be generated; lower First Cut Pieces, Sub-Cut Pieces or Rounds"
)


def _check_sources(context, sources, settings):
    """动手之前完成全部前置检查, 参数非法时不会留下半成品"""
    if context.mode != "OBJECT" or not sources:
        raise MeshFractureError("Select editable mesh objects in Object Mode")
    index = settings.material_index
    for obj in sources:
        if obj.data.library is not None and uses_interior(settings):
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


def _cut(context, obj, settings, batch, rng, limit):
    """对单个物体执行一次切割, 种子最多 limit 个, 返回按路径编号的碎块

    源物体的显示方式与面隐藏状态无论成败都还原
    """
    old_display = obj.display_type
    # 只有首次切割且需要标记断面时才会清掉源网格的面隐藏标记, 其余情况不写源网格
    old_hidden = None
    if batch.first and uses_interior(settings):
        old_hidden = [face.hide for face in obj.data.polygons]
    try:
        if settings.use_debug_redraw:
            # 线框显示方便观察切割进度, 结果不受影响
            obj.display_type = "WIRE"
        # 求值快照每次切割只取一次, 胞体构造与布尔求交共用
        snapshot = boolean.inspect_source(obj, context.evaluated_depsgraph_get())
        points = sampling.seed_points(context, obj, settings, limit, rng)
        # 种子点数就已超限时, 在耗时的布尔求交之前报错
        _check_limit(len(points))
        cells = cell_objects.build_cells(context, obj, snapshot, points, settings, batch, rng)
        cells = intersect.intersect_cells(context, obj, snapshot, cells, settings, batch)
        if settings.use_recenter:
            shard_objects.recenter(cells)
        batch.number(cells)
        return cells
    finally:
        obj.display_type = old_display
        if old_hidden is not None:
            obj.data.polygons.foreach_set("hide", old_hidden)
            obj.data.update()


def _finish(cells, settings, naming):
    """全部切割结束后的收尾: 合并共面面, 统一命名与断面标记; 保留的父碎块一并处理"""
    objects = cells + naming.parents
    if objects and bakes_booleans(settings):
        cleanup.dissolve_final_shards(objects)
    naming.apply_names(objects)
    if objects and finishes_interior(settings):
        interior.finish_interior(objects, settings)


def _subdivide(context, parent, limit, cells, naming, settings, rng):
    """细分一个父块: 子块加入碎块列表, 父块离开列表并按设置删除或移进父碎块集合; 返回是否有产出"""
    batch = naming.next_batch(parent)
    children = _cut(context, parent, settings, batch, rng, limit)
    if not children:
        # 细分没有产出时保留父块
        return False
    cells.extend(children)
    # 父块已被子块取代, 不再参与之后的递归与上限计数
    cells.remove(parent)
    if settings.use_keep_parents:
        naming.keep_parent(parent)
    else:
        # 删除被替换的父块, 避免父子几何重叠
        shard_objects.remove_generated_object(parent)
    return True


def main_object(context, collection, obj, settings, rng, tracker, done):
    """对单个源物体做首次切割并按需递归, 最后统一收尾, 返回最终碎块

    done 是之前的源已经生成的碎块数; 碎块总数上限先约束首次切割的种子数, 每轮递归再按剩余预算规划
    """
    naming = NamingPlan(obj.name, collection, settings, tracker)
    cells = _cut(context, obj, settings, naming.first_batch(), rng, first_cut_limit(settings))
    _check_limit(done + len(cells))
    maximum = settings.recursion_clamp
    cursor = context.scene.cursor.location
    # 保留布尔修改器时递归会让引用失效, 直接跳过
    rounds = recursion_rounds(settings)
    for _round in range(rounds):
        # 上限为 0 表示不限, 预算记为 None
        budget = maximum - len(cells) if maximum else None
        if not cells or (budget is not None and budget <= 0):
            break
        plan = recursion.plan_round(context, cells, settings, cursor, rng, budget)
        if not plan:
            break
        for parent, limit in plan:
            if _subdivide(context, parent, limit, cells, naming, settings, rng):
                _check_limit(done + len(cells))
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
        collection = _output_collection(context, settings.collection_name, tracker)
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
