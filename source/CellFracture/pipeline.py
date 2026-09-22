"""切割流程编排, 以及失败时回收本次新建的数据"""

import random
from time import perf_counter

import bpy
from mathutils import Vector

from ..common.created_data import CreatedData
from . import mesh_ops
from .constants import RANDOM_SEED_DEFAULT
from .errors import FractureError
from .naming import NamingPlan


def _visible_collection(context, collection):
    """判断集合在当前视图层里是否可见, 不可见时不应往里放碎块"""

    def visit(layer):
        """递归检查目标集合对应视图层节点的可见性"""
        return (layer.collection == collection and layer.is_visible) or any(
            visit(child) for child in layer.children
        )

    return visit(context.view_layer.layer_collection)


def _cut(
    context, collection, obj, settings, level, naming, group=None, target=None, rng=None, *, tracker
):
    """对单个物体执行一次切割, 返回生成的碎块

    level 为 0 表示首次切割, 大于 0 表示递归细分;
    递归层换用递归种子上限, 以免子碎块生成过多胞体
    naming 是本次切割共用的命名计划; group 与 target 决定这批碎块属于哪一组
    """
    options = {
        key: settings[key]
        for key in (
            "source",
            "source_limit",
            "source_noise",
            "cell_scale",
            "use_smooth_faces",
            "use_data_match",
            "use_debug_points",
            "margin",
            "material_index",
            "use_debug_redraw",
        )
    }
    if level:
        options["source_limit"] = settings["recursion_source_limit"]
    # 源物体的显示模式与面隐藏状态会被临时改动, 无论成败都要还原
    old_display = obj.display_type
    old_hidden = [face.hide for face in obj.data.polygons]
    try:
        obj.select_set(False)
        if settings["use_debug_redraw"]:
            obj.display_type = "WIRE"
        cells = mesh_ops.cell_fracture_objects(
            context,
            collection,
            obj,
            tracker=tracker,
            naming=naming,
            level=level,
            group=group,
            target=target,
            rng=rng,
            **options,
        )
        cells = mesh_ops.cell_fracture_boolean(
            context,
            obj,
            cells,
            use_island_split=settings["use_island_split"],
            use_interior_hide=(settings["use_interior_vgroup"] or settings["use_sharp_edges"]),
            use_debug_bool=settings["use_debug_bool"],
            use_debug_redraw=settings["use_debug_redraw"],
            level=level,
            tracker=tracker,
        )
        if cells and settings["use_recenter"]:
            # 只移动原点, 不动几何
            with context.temp_override(
                object=cells[0],
                active_object=cells[0],
                selected_objects=cells,
                selected_editable_objects=cells,
            ):
                bpy.ops.object.origin_set(type="ORIGIN_GEOMETRY", center="MEDIAN")
        return cells
    finally:
        obj.display_type = old_display
        obj.data.polygons.foreach_set("hide", old_hidden)
        obj.data.update()


def _select_recursion(cells, settings, cursor, rng=None):
    """按比例与选择方式挑出本轮参与递归的碎块"""
    fraction = settings["recursion_chance"]
    selected = list(cells)
    if fraction == 1:
        return selected
    mode = settings["recursion_chance_select"]
    if mode == "RANDOM":
        if rng is None:
            rng = random.Random(settings.get("random_seed", RANDOM_SEED_DEFAULT))
        rng.shuffle(selected)
    elif mode.startswith("SIZE"):
        # 用世界空间包围盒体对角线长度排序, 省去开方
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
        # 游标模式按物体原点与 3D 游标的距离排序
        selected.sort(
            key=lambda obj: (obj.matrix_world.translation - cursor).length_squared,
            reverse=mode == "CURSOR_MAX",
        )
    return selected[: int(fraction * len(selected))]


def main_object(context, collection, obj, settings, rng=None, *, tracker):
    """对单个源物体做首次切割并按需递归, 最后统一处理内部断面"""
    if rng is None:
        rng = random.Random(settings.get("random_seed", RANDOM_SEED_DEFAULT))
    # 命名计划在这里建立: 源物体名只读这一次, 递归层沿用它, 避免旧实现里
    # 逐层追加 "_cell" 的问题; 分组集合也由它创建
    naming = NamingPlan(
        obj,
        collection,
        use_collections=settings["use_shard_collections"],
        prefix=settings["shard_prefix"],
        padding=settings["shard_index_padding"],
        tracker=tracker,
    )
    # 首次切割是 tier 0: 不建任何组集合, 全部碎块直接落在输出集合 (顶层),
    # 名字是纯序号 (cell_001 ...); 之后第 n 次递归的产物用 tier n
    cells = _cut(context, collection, obj, settings, 0, naming, 0, None, rng=rng, tracker=tracker)
    threshold = settings["recursion_clamp"]
    # 调试布尔模式保留未应用的修改器, 递归会让引用失效, 因此直接跳过
    rounds = 0 if settings["use_debug_bool"] else settings["recursion"]
    for level in range(1, rounds + 1):
        if not cells or (threshold and len(cells) >= threshold):
            break
        candidates = _select_recursion(cells, settings, context.scene.cursor.location, rng=rng)
        if not candidates:
            break
        for parent in candidates:
            # 阈值按每个源物体的当前碎块数计算, 单次细分仍可能超出
            if threshold and len(cells) >= threshold:
                break
            # 每个父碎块的子块自成一"组": 组号在层内递增, 序号在组内从 1 重计
            group = naming.next_group(level)
            sub = naming.next_collection(level, group)
            children = _cut(
                context,
                collection,
                parent,
                settings,
                level,
                naming,
                group,
                sub,
                rng=rng,
                tracker=tracker,
            )
            # 细分失败或结果为空时保留父碎块, 不能把它删掉; 刚建的组也要退回
            if not children:
                naming.drop_collection(sub, level)
                continue
            cells.extend(children)
            if settings["use_remove_original"]:
                cells.remove(parent)
                mesh_ops.remove_generated_object(parent)
    # 被递归替换掉的父碎块会腾空它所在的集合, 这里统一收回, 避免大纲里留下空壳
    naming.prune_empty_collections()
    # 全部切割结束后统一命名: 序号只按保留下来的碎块连续编, 不分组时因此不会断号,
    # 分组时每组从 001 起物体与网格数据一起改, 保持一致
    naming.apply_names(cells)
    if (
        cells
        and not settings["use_debug_bool"]
        and (settings["use_interior_vgroup"] or settings["use_sharp_edges"])
    ):
        mesh_ops.cell_fracture_interior_handle(
            cells,
            use_interior_vgroup=settings["use_interior_vgroup"],
            use_sharp_edges=settings["use_sharp_edges"],
            use_sharp_edges_apply=settings["use_sharp_edges_apply"],
        )
    return cells


def run(context, sources, settings, output_collection, *, rng=None):
    """按名称排序显式源物体并切割 - 失败回收本次新建数据

    返回碎块列表与来源映射及耗时 - 选择恢复由 API 作用域负责"""
    # 固定多源处理顺序, 不让选择顺序改变同一随机流的分配
    sources = sorted((obj for obj in sources if obj.type == "MESH"), key=lambda obj: obj.name_full)
    if context.mode != "OBJECT" or not sources:
        raise FractureError("Select editable mesh objects in Object Mode")
    # 先做完全部前置检查再动手, 避免切到一半才发现参数非法
    for obj in sources:
        if not obj.data.vertices or not obj.data.polygons:
            raise FractureError('Mesh "{name}" has no faces', name=obj.name)
        index = settings["material_index"]
        if settings["use_data_match"] and index > 0 and index >= len(obj.material_slots):
            raise FractureError(
                'Material slot {index} does not exist on "{name}"', index=index, name=obj.name
            )
    # 只记录并回收本次显式创建的数据, 不碰其它插件新增的内容
    tracker = CreatedData()
    # 一次运行共用局部随机流, 不读取或修改 Python/mathutils 的全局随机状态
    # 旧脚本未传 random_seed 时仍可执行, 默认种子与 RNA 一致
    if rng is None:
        rng = random.Random(settings.get("random_seed", RANDOM_SEED_DEFAULT))
    started = perf_counter()
    context.window_manager.progress_begin(0, len(sources))
    try:
        collection = output_collection if output_collection is not None else context.collection
        if isinstance(output_collection, str):
            collection = (
                bpy.data.collections.get(output_collection)
                if output_collection
                else context.collection
            )
            if collection is None:
                collection = tracker.track(
                    "collections", bpy.data.collections.new(output_collection)
                )
                context.collection.children.link(collection)
                context.view_layer.update()
        if collection.library or not _visible_collection(context, collection):
            raise FractureError(
                "Output collection must be editable and visible in the current view layer"
            )
        cells = []
        source_to_shards = {}
        for index, obj in enumerate(sources):
            created = main_object(context, collection, obj, settings, rng=rng, tracker=tracker)
            if not created:
                raise FractureError('No valid shards were produced for "{name}"', name=obj.name)
            cells.extend(created)
            source_to_shards[obj] = tuple(created)
            context.window_manager.progress_update(index + 1)
        return cells, source_to_shards, perf_counter() - started
    except Exception:
        # 任何一个物体失败都回收全部新建数据, 并把选择集还原
        tracker.rollback()
        raise
    finally:
        context.window_manager.progress_end()
