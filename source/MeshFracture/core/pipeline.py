"""流程编排 - 逐源首次切割与递归细分, 统一命名和断面收尾, 失败时回收本次新建数据"""

from time import perf_counter

import bpy
from mathutils import Vector

from ...common.created_data import CreatedData
from ..constants import SHARD_LIMIT
from ..errors import MeshFractureError
from . import boolean, mesh_ops
from .naming import NamingPlan


def _check_sources(context, sources, settings):
    """动手之前完成全部前置检查, 参数非法时不会留下半成品"""
    if context.mode != "OBJECT" or not sources:
        raise MeshFractureError("Select editable mesh objects in Object Mode")
    index = settings["material_index"]
    for obj in sources:
        if obj.data.library is not None:
            # 切割期间要临时改写源网格的面隐藏标记, 链接库里的网格不可写
            raise MeshFractureError(
                'Mesh data of "{name}" is linked from a library and cannot be cut', name=obj.name
            )
        if not obj.data.polygons:
            raise MeshFractureError('Mesh "{name}" has no faces', name=obj.name)
        if settings["use_data_match"] and index > 0 and index >= len(obj.material_slots):
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


def _output_collection(context, output, tracker):
    """解析输出集合: 集合对象原样使用, 集合名先查找再新建, None 或空名用当前集合"""
    collection = output
    if output is None or output == "":
        collection = context.collection
    elif isinstance(output, str):
        collection = bpy.data.collections.get(output)
        if collection is None:
            collection = tracker.track("collections", bpy.data.collections.new(output))
            context.collection.children.link(collection)
            context.view_layer.update()
    if collection.library or not _visible_collection(context, collection):
        raise MeshFractureError(
            "Output collection must be editable and visible in the current view layer"
        )
    return collection


def _check_limit(count):
    """本次生成的碎块总数超过硬上限时报错, 由 run 回收已经生成的数据"""
    if count > SHARD_LIMIT:
        raise MeshFractureError(mesh_ops.LIMIT_MESSAGE, limit=SHARD_LIMIT)


def _cut(context, obj, settings, batch, rng):
    """对单个物体执行一次切割, 返回生成的碎块; 源物体的显示方式与面隐藏状态无论成败都还原"""
    old_display = obj.display_type
    old_hidden = [face.hide for face in obj.data.polygons]
    try:
        if settings["use_debug_redraw"]:
            # 线框显示方便观察切割进度, 结果不受影响
            obj.display_type = "WIRE"
        # 求值快照每次切割只取一次, 胞体构造与布尔求交共用
        snapshot = boolean.inspect_source(obj, context.evaluated_depsgraph_get())
        cells = mesh_ops.build_cells(context, obj, snapshot, settings, batch, rng)
        cells = mesh_ops.intersect_cells(context, obj, snapshot, cells, settings, batch)
        if settings["use_recenter"]:
            mesh_ops.recenter(cells)
        return cells
    finally:
        obj.display_type = old_display
        obj.data.polygons.foreach_set("hide", old_hidden)
        obj.data.update()


def _select_recursion(cells, settings, cursor, rng):
    """按比例与选择方式挑出本轮参与递归的碎块, 数量向下取整"""
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
    """全部切割结束后的收尾: 合并共面面, 清理空集合, 统一命名与断面标记"""
    debug = settings["use_debug_bool"]
    if cells and not debug:
        mesh_ops.dissolve_final_shards(cells)
    naming.prune()
    naming.apply_names(cells)
    if cells and not debug and (settings["use_interior_vgroup"] or settings["use_sharp_edges"]):
        mesh_ops.finish_interior(cells, settings)


def main_object(context, collection, obj, settings, rng, tracker, done):
    """对单个源物体做首次切割并按需递归, 最后统一收尾; done 是之前的源已经生成的碎块数"""
    naming = NamingPlan(obj.name, collection, settings, tracker)
    cells = _cut(context, obj, settings, naming.first_batch(), rng)
    _check_limit(done + len(cells))
    threshold = settings["recursion_clamp"]
    # 保留布尔修改器时递归会让引用失效, 直接跳过
    rounds = 0 if settings["use_debug_bool"] else settings["recursion"]
    for level in range(1, rounds + 1):
        if not cells or (threshold and len(cells) >= threshold):
            break
        candidates = _select_recursion(cells, settings, context.scene.cursor.location, rng)
        if not candidates:
            break
        for parent in candidates:
            # 每个父块细分前再判断一次阈值; 单次细分或孤岛拆分仍可能超出
            if threshold and len(cells) >= threshold:
                break
            batch = naming.next_batch(level)
            children = _cut(context, parent, settings, batch, rng)
            if not children:
                # 细分没有产出时保留父块, 退回刚分配的组
                naming.drop(batch)
                continue
            cells.extend(children)
            _check_limit(done + len(cells))
            if settings["use_remove_original"]:
                # 删除被替换的父块, 避免父子几何重叠
                cells.remove(parent)
                mesh_ops.remove_generated_object(parent)
    _finish(cells, settings, naming)
    return cells


def run(context, sources, settings, output_collection, rng):
    """按名称排序显式源物体并逐个切割, 返回碎块, 来源映射与耗时; 失败时回收本次新建数据"""
    # 固定多源处理顺序, 选择顺序不会改变随机流的分配
    sources = sorted((obj for obj in sources if obj.type == "MESH"), key=lambda obj: obj.name_full)
    _check_sources(context, sources, settings)
    # 只记录并回收本次显式创建的数据
    tracker = CreatedData()
    started = perf_counter()
    context.window_manager.progress_begin(0, len(sources))
    try:
        collection = _output_collection(context, output_collection, tracker)
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
