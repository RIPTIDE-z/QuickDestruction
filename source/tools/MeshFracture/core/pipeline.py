"""流程编排 - 逐源首次切割与递归细分, 统一命名和断面收尾, 失败时回收本次新建数据"""

import traceback
from time import perf_counter

import bpy

from ....common.created_data import CreatedData
from ..constants import BATCH_PARENTS, SHARD_LIMIT
from ..errors import MeshFractureError
from ..settings import (
    bakes_booleans,
    finishes_interior,
    first_cut_seed_limit,
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
from .isolation import Isolation
from .naming import NamingPlan

# 超出碎块硬上限时的消息
LIMIT_MESSAGE = (
    "More than {limit} shards would be generated; lower First Cut Seeds, Sub-Cut Pieces or Rounds"
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


def _prepare(context, job, settings, rng, depsgraph):
    """一个被切物体的准备: 求值快照, 取种子, 建胞体并挂上求交修改器; 还没有求值任何布尔"""
    obj, batch, limit = job
    # 求值快照每次切割只取一次, 胞体构造与布尔求交共用
    snapshot = boolean.inspect_source(obj, depsgraph)
    points = sampling.seed_points(context, obj, settings, limit, rng, depsgraph)
    # 种子点数就已超限时, 在耗时的布尔求交之前报错
    _check_limit(len(points))
    cells = cell_objects.build_cells(context, obj, snapshot, points, settings, batch, rng)
    modifiers = intersect.attach(obj, snapshot, cells, settings, batch)
    return snapshot, cells, modifiers


def _cut_jobs(context, jobs, settings, rng):
    """一批物体一起切, 返回每个物体按路径编号的碎块列表; jobs 是 [(物体, 批次, 种子上限), ...]

    先逐个准备并挂上修改器, 再统一刷新一次视图层, 这一批的布尔在这一次里并行求值, 最后逐个取回结果
    随机流按物体顺序消费, 与逐个切割时完全相同; 显示方式与面隐藏状态无论成败都还原
    """
    saved = []
    for obj, batch, _limit in jobs:
        # 只有首次切割且需要标记断面时才会清掉源网格的面隐藏标记, 其余情况不写源网格
        hidden = None
        if batch.first and uses_interior(settings):
            hidden = [face.hide for face in obj.data.polygons]
        saved.append((obj, obj.display_type, hidden))
    try:
        if settings.use_debug_redraw:
            for obj, _display, _hidden in saved:
                # 线框显示方便观察切割进度, 结果不受影响
                obj.display_type = "WIRE"
        depsgraph = context.evaluated_depsgraph_get()
        prepared = [_prepare(context, job, settings, rng, depsgraph) for job in jobs]
        # 修改器与隐藏标记都要先刷新视图层, 依赖图里才会包含它们; 取依赖图要放在刷新之后
        context.view_layer.update()
        depsgraph = context.evaluated_depsgraph_get()
        outcomes = []
        for (_obj, batch, _limit), (snapshot, cells, modifiers) in zip(jobs, prepared, strict=True):
            cells = intersect.collect(snapshot, cells, modifiers, settings, batch, depsgraph)
            if settings.use_recenter:
                shard_objects.recenter(cells)
            batch.number(cells)
            outcomes.append(cells)
        return outcomes
    finally:
        for obj, display, hidden in saved:
            obj.display_type = display
            if hidden is not None:
                obj.data.polygons.foreach_set("hide", hidden)
                obj.data.update()


def _finish(cells, settings, naming):
    """全部切割结束后的收尾: 合并共面面, 统一命名与断面标记; 保留的父碎块一并处理"""
    objects = cells + naming.parents
    if objects and bakes_booleans(settings):
        cleanup.dissolve_final_shards(objects)
    naming.apply_names(objects)
    if objects and finishes_interior(settings):
        interior.finish_interior(objects, settings)


def _subdivide(context, group, cells, naming, settings, rng, state):
    """细分一批父块: 子块加入碎块列表, 父块离开列表并按设置丢弃或移进父碎块集合

    group 是 [(父块, 种子上限), ...]; 没有产出的父块保留
    """
    tracker, isolation = state
    with isolation.around([parent for parent, _limit in group], naming):
        jobs = [(parent, naming.next_batch(parent), limit) for parent, limit in group]
        for (parent, _limit), children in zip(
            group, _cut_jobs(context, jobs, settings, rng), strict=True
        ):
            if not children:
                continue
            cells.extend(children)
            # 父块已被子块取代, 不再参与之后的递归与上限计数
            cells.remove(parent)
            if settings.use_keep_parents:
                naming.keep_parent(parent)
            else:
                # 丢弃被替换的父块, 避免父子几何重叠
                shard_objects.discard_object(tracker, parent)


def main_object(context, collection, obj, settings, rng, state, done):
    """对单个源物体做首次切割并按需递归, 最后统一收尾, 返回最终碎块

    state 是本次生成的回收账本与依赖图隔离
    done 是之前的源已经生成的碎块数; 碎块总数上限先约束首次切割的种子数, 每轮递归再按剩余预算规划
    每轮开始前先按上界估算这一轮之后的总数, 必然超过硬上限时不再动手
    """
    tracker = state[0]
    naming = NamingPlan(obj.name, collection, settings, tracker)
    first_job = (obj, naming.first_batch(), first_cut_seed_limit(settings))
    (cells,) = _cut_jobs(context, [first_job], settings, rng)
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
        _check_limit(done + recursion.projected_count(context, cells, plan, settings))
        # 实时重绘要逐个父块地看到进度, 那时一批只有一个父块
        size = 1 if settings.use_debug_redraw else BATCH_PARENTS
        for start in range(0, len(plan), size):
            _subdivide(context, plan[start : start + size], cells, naming, settings, rng, state)
            _check_limit(done + len(cells))
        # 每轮结束统一批量删除被替换的数据
        tracker.flush()
    # 首次切割丢弃的空胞体, 以及提前结束的轮次留下的待删数据
    tracker.flush()
    _finish(cells, settings, naming)
    return cells


def run(context, sources, settings, rng):
    """按名称排序显式源物体并逐个切割, 返回碎块, 来源映射与耗时; 失败时回收本次新建数据"""
    # 固定多源处理顺序, 选择顺序不会改变随机流的分配
    sources = sorted((obj for obj in sources if obj.type == "MESH"), key=lambda obj: obj.name_full)
    _check_sources(context, sources, settings)
    # 只记录并回收本次显式创建的数据
    tracker = CreatedData()
    # 实时重绘要让人看到已完成的碎块, 那时不隔离
    isolation = Isolation(context, tracker, not settings.use_debug_redraw)
    state = (tracker, isolation)
    started = perf_counter()
    try:
        context.window_manager.progress_begin(0, len(sources))
        collection = _output_collection(context, settings.collection_name, tracker)
        cells = []
        source_to_shards = {}
        for index, obj in enumerate(sources):
            created = main_object(context, collection, obj, settings, rng, state, len(cells))
            if not created:
                raise MeshFractureError('No valid shards were produced for "{name}"', name=obj.name)
            cells.extend(created)
            source_to_shards[obj] = tuple(created)
            context.window_manager.progress_update(index + 1)
        seconds = perf_counter() - started
        # 清理也属于成功路径 - 失败时仍需回收已经生成的数据
        isolation.release()
        context.window_manager.progress_end()
        return cells, source_to_shards, seconds
    except Exception:
        # 恢复失败不能覆盖原异常或阻止其他收尾动作
        for cleanup_action in (
            tracker.rollback,
            isolation.release,
            context.window_manager.progress_end,
        ):
            try:
                cleanup_action()
            except Exception:
                traceback.print_exc()
        raise
