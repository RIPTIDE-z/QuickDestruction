"""切割流程编排, 以及失败时回收本次新建的数据。"""

import random
from time import perf_counter

import bpy
from mathutils import Vector

from . import qd_fracture_setup as setup
from .qd_settings import validated_values


class FractureError(Exception):
    """切割过程中的可预期错误; message 是可翻译文本, values 是格式化参数。"""

    def __init__(self, message, **values):
        super().__init__(message)
        self.message = message
        self.values = values


def _visible_collection(context, collection):
    """判断集合在当前视图层里是否可见, 不可见时不应往里放碎块。"""

    def visit(layer):
        return ((layer.collection == collection and layer.is_visible)
                or any(visit(child) for child in layer.children))

    return visit(context.view_layer.layer_collection)


def _cut(context, collection, obj, settings, level, naming, group=None, target=None):
    """对单个物体执行一次切割, 返回生成的碎块。

    level 为 0 表示首次切割, 大于 0 表示递归细分;
    递归层换用递归种子上限, 以免子碎块生成过多胞体。
    naming 是本次切割共用的命名计划; group 与 target 决定这批碎块属于哪一组。
    """
    options = {key: settings[key] for key in (
        'source', 'source_limit', 'source_noise', 'cell_scale', 'use_smooth_faces',
        'use_data_match', 'use_debug_points', 'margin', 'material_index', 'use_debug_redraw')}
    if level:
        options['source_limit'] = settings['recursion_source_limit']
    # 源物体的显示模式与面隐藏状态会被临时改动, 无论成败都要还原
    old_display = obj.display_type
    old_hidden = [face.hide for face in obj.data.polygons]
    try:
        obj.select_set(False)
        if settings['use_debug_redraw']:
            obj.display_type = 'WIRE'
        cells = setup.cell_fracture_objects(context, collection, obj,
                                            tracker=settings['_tracker'],
                                            naming=naming, level=level, group=group,
                                            target=target, **options)
        cells = setup.cell_fracture_boolean(
            context, collection, obj, cells,
            use_island_split=settings['use_island_split'],
            use_interior_hide=(settings['use_interior_vgroup'] or settings['use_sharp_edges']),
            use_debug_bool=settings['use_debug_bool'],
            use_debug_redraw=settings['use_debug_redraw'], level=level, tracker=settings['_tracker'])
        if cells and settings['use_recenter']:
            # 只移动原点, 不动几何
            with context.temp_override(object=cells[0], active_object=cells[0],
                                       selected_objects=cells, selected_editable_objects=cells):
                bpy.ops.object.origin_set(type='ORIGIN_GEOMETRY', center='MEDIAN')
        return cells
    finally:
        obj.display_type = old_display
        obj.data.polygons.foreach_set('hide', old_hidden)
        obj.data.update()


def _select_recursion(cells, settings, cursor):
    """按比例与选择方式挑出本轮参与递归的碎块。"""
    fraction = settings['recursion_chance']
    selected = list(cells)
    if fraction == 1:
        return selected
    mode = settings['recursion_chance_select']
    if mode == 'RANDOM':
        random.shuffle(selected)
    elif mode.startswith('SIZE'):
        # 用世界空间包围盒体对角线长度排序, 省去开方
        selected.sort(key=lambda obj: (
            obj.matrix_world @ Vector(obj.bound_box[0]) -
            obj.matrix_world @ Vector(obj.bound_box[6])).length_squared,
            reverse=mode == 'SIZE_MAX')
    else:
        # 游标模式按物体原点与 3D 游标的距离排序
        selected.sort(key=lambda obj: (obj.matrix_world.translation - cursor).length_squared,
                      reverse=mode == 'CURSOR_MAX')
    return selected[:int(fraction * len(selected))]


def main_object(context, collection, obj, settings):
    """对单个源物体做首次切割并按需递归, 最后统一处理内部断面。"""
    # 命名计划在这里建立: 源物体名只读这一次, 递归层沿用它, 避免旧实现里
    # 逐层追加 "_cell" 的问题; 分组集合也由它创建
    naming = setup.NamingPlan(
        obj, collection,
        use_collections=settings['use_shard_collections'],
        prefix=settings['shard_prefix'],
        padding=settings['shard_index_padding'],
        tracker=settings['_tracker'])
    # 首次切割是 tier 0: 不建任何组集合, 全部碎块直接落在输出集合 (顶层),
    # 名字是纯序号 (cell_001 ...); 之后第 n 次递归的产物用 tier n
    cells = _cut(context, collection, obj, settings, 0, naming, 0, None)
    threshold = settings['recursion_clamp']
    # 调试布尔模式保留未应用的修改器, 递归会让引用失效, 因此直接跳过
    rounds = 0 if settings['use_debug_bool'] else settings['recursion']
    for level in range(1, rounds + 1):
        if not cells or (threshold and len(cells) >= threshold):
            break
        candidates = _select_recursion(cells, settings, context.scene.cursor.location)
        if not candidates:
            break
        for parent in candidates:
            # 阈值按每个源物体的当前碎块数计算, 单次细分仍可能超出
            if threshold and len(cells) >= threshold:
                break
            # 每个父碎块的子块自成一"组": 组号在层内递增, 序号在组内从 1 重计
            group = naming.group_counts.get(level, 0) + 1
            sub = naming.next_collection(level, group)
            children = _cut(context, collection, parent, settings, level, naming, group, sub)
            # 细分失败或结果为空时保留父碎块, 不能把它删掉; 刚建的组也要退回
            if not children:
                naming.drop_collection(sub, level)
                continue
            cells.extend(children)
            if settings['use_remove_original']:
                cells.remove(parent)
                setup.remove_generated_object(parent)
    # 被递归替换掉的父碎块会腾空它所在的集合, 这里统一收回, 避免大纲里留下空壳
    naming.prune_empty_collections()
    # 全部切割结束后统一命名: 序号只按保留下来的碎块连续编, 不分组时因此不会断号,
    # 分组时每组从 001 起。物体与网格数据一起改, 保持一致
    naming.apply_names(cells)
    if cells and not settings['use_debug_bool'] and (
            settings['use_interior_vgroup'] or settings['use_sharp_edges']):
        setup.cell_fracture_interior_handle(
            cells, use_interior_vgroup=settings['use_interior_vgroup'],
            use_sharp_edges=settings['use_sharp_edges'],
            use_sharp_edges_apply=settings['use_sharp_edges_apply'])
    return cells


def _restore_selection(context, selection, active):
    """把选择集恢复成操作前的样子, 成功与失败都走这一条路径。"""
    context.view_layer.update()
    for obj in context.view_layer.objects:
        # 物体可能已被删除而残留空条目, 需要跳过
        if obj is not None and obj.select_get():
            obj.select_set(False)
    for obj in selection:
        obj.select_set(True)
    context.view_layer.objects.active = active


def main(context, **settings):
    """切割入口: 校验参数与场景, 逐个源物体切割, 出错则整体回滚。

    返回 (碎块列表, 耗时秒数)。
    """
    settings = validated_values(settings)
    sources = [obj for obj in context.selected_editable_objects if obj.type == 'MESH']
    if context.mode != 'OBJECT' or not sources:
        raise FractureError("Select editable mesh objects in Object Mode")
    # 先做完全部前置检查再动手, 避免切到一半才发现参数非法
    for obj in sources:
        if not obj.data.vertices or not obj.data.polygons:
            raise FractureError('Mesh "{name}" has no faces', name=obj.name)
        index = settings['material_index']
        if settings['use_data_match'] and index > 0 and index >= len(obj.material_slots):
            raise FractureError('Material slot {index} does not exist on "{name}"',
                                index=index, name=obj.name)
    selection = list(context.selected_objects)
    active = context.view_layer.objects.active
    # 只记录并回收本次显式创建的数据, 不碰其它插件新增的内容
    tracker = setup.CreatedData()
    settings['_tracker'] = tracker
    started = perf_counter()
    context.window_manager.progress_begin(0, len(sources))
    try:
        collection = context.collection
        if settings['collection_name']:
            collection = bpy.data.collections.get(settings['collection_name'])
            if collection is None:
                collection = tracker.track('collections', bpy.data.collections.new(settings['collection_name']))
                context.collection.children.link(collection)
                context.view_layer.update()
        if collection.library or not _visible_collection(context, collection):
            raise FractureError("Output collection must be editable and visible in the current view layer")
        cells = []
        for index, obj in enumerate(sources):
            created = main_object(context, collection, obj, settings)
            if not created:
                raise FractureError('No valid shards were produced for "{name}"', name=obj.name)
            cells.extend(created)
            context.window_manager.progress_update(index + 1)
        _restore_selection(context, cells, cells[0])
        return cells, perf_counter() - started
    except Exception:
        # 任何一个物体失败都回收全部新建数据, 并把选择集还原
        tracker.rollback()
        _restore_selection(context, selection, active)
        raise
    finally:
        context.window_manager.progress_end()
