"""胞体建网格与布尔结果收尾"""

import random
import uuid
from math import isfinite

import bmesh
import bpy
from mathutils import Vector

from ...common.blender_context import mesh_edit
from ...common.created_data import track
from ..constants import (
    INTERIOR_VGROUP_NAME,
    RANDOM_SEED_DEFAULT,
    WELD_SCALE_RATIO,
    WELD_TO_JITTER_RATIO,
)
from ..errors import FractureError
from . import boolean
from .sampling import points_from_object, random_unit_vector


def _weld_distances(scale):
    """由物体尺度推出焊接距离与抖动半幅, 抖动取焊接距离的几分之一"""
    distance = scale * WELD_SCALE_RATIO
    return distance, distance / WELD_TO_JITTER_RATIO


def redraw():
    """后台模式下没有窗口, 跳过重绘"""
    if not bpy.app.background:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def remove_generated_object(obj):
    """删除物体, 并回收随之失去用户的网格数据"""
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def build_cells(
    context,
    collection,
    obj,
    source=frozenset({"PARTICLE_OWN"}),
    source_limit=0,
    source_noise=0.0,
    clean=True,
    use_smooth_faces=False,
    use_data_match=False,
    use_debug_points=False,
    margin=0.0,
    material_index=0,
    use_debug_redraw=False,
    cell_scale=(1.0, 1.0, 1.0),
    tracker=None,
    naming=None,
    level=0,
    group=None,
    target=None,
    rng=None,
):
    """采样种子点并生成凸胞体, 返回胞体物体列表

    naming 为 NamingPlan 时: level 与 group 分别是整数层号和组号,
    target 是这批碎块要落进的组集合 (可能为 None, 表示直接落在输出集合)
    这两项由调用方决定, 因为"这一批属于哪一组"是流程层面的信息, 这里不去猜
    naming 为 None 时使用 "<源物体名>_cell", 允许独立调用本阶段
    rng 由管线在各轮间共享; 单独调用未提供时使用固定默认种子的局部随机流
    """
    from .cells import points_as_bmesh_cells

    if rng is None:
        rng = random.Random(RANDOM_SEED_DEFAULT)

    def shard_name():
        # 切割期只用临时名; 最终名字在全部切完后由 apply_names() 统一分配,
        # 这样被丢弃的中间结果不会占掉序号, 也不会出现断号
        """从命名计划取临时名 - 独立调用沿用源物体名"""
        return naming.temp_name() if naming is not None else obj.name + "_cell"

    def shard_collection():
        """优先使用本轮指定集合 - 否则使用输出集合"""
        return target if target is not None else collection

    depsgraph = context.evaluated_depsgraph_get()
    points = points_from_object(context, depsgraph, obj, source)
    # 选定来源一个点都没有时回退到自身顶点, 避免白跑一趟
    if not points:
        points = points_from_object(context, depsgraph, obj, {"VERT_OWN"})
    if not points:
        return []
    if source_limit and source_limit < len(points):
        # 先打乱再截断, 得到的是随机子集而不是前 N 个
        rng.shuffle(points)
        points = points[:source_limit]
    # 精确去重: 按固定小数位取整会丢掉小尺寸物体的真实点
    points = list({tuple(point): point for point in points}.values())
    evaluated = obj.evaluated_get(depsgraph)
    try:
        evaluated_mesh = evaluated.to_mesh()
        verts = [obj.matrix_world @ v.co for v in evaluated_mesh.vertices]
    finally:
        evaluated.to_mesh_clear()
    if not verts:
        return []
    if any(not isfinite(value) for point in (*verts, *points) for value in point):
        raise FractureError("Source geometry contains non-finite coordinates")
    # 用包围盒对角线作为整体尺度, 焊接与抖动都按它派生
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    scale = Vector(tuple(hi[i] - lo[i] for i in range(3))).length
    weld_dist, jitter_half = _weld_distances(scale)
    if source_noise:
        # 扰动规则点阵, 避免出现过于整齐的切割面
        points = [
            p + random_unit_vector(rng) * (source_noise * scale * 0.5 * rng.random())
            for p in points
        ]
    if use_debug_points:
        # 诊断几何, 用于查看实际采样到的种子点位置; 跟随碎块一起落点
        mesh = track(tracker, "meshes", bpy.data.meshes.new("DebugPoints"))
        mesh.from_pydata(points, [], [])
        debug_object = track(tracker, "objects", bpy.data.objects.new("DebugPoints", mesh))
        shard_collection().objects.link(debug_object)
    cells = points_as_bmesh_cells(verts, points, cell_scale, margin_cell=margin)
    objects = []
    for center, cell_points in cells:
        with mesh_edit() as bm:
            # 轻微抖动有助焊接重合点, 抖动幅度远小于焊接距离
            for point in cell_points:
                point = point + Vector(
                    tuple(rng.uniform(-jitter_half, jitter_half) for _ in range(3))
                )
                bm.verts.new(point)
            bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_dist)
            # 少于四个顶点无法构成凸体, 直接丢弃
            if len(bm.verts) < 4:
                continue
            try:
                bmesh.ops.convex_hull(bm, input=list(bm.verts))
            except RuntimeError:
                continue
            if not bm.faces:
                continue
            if clean:
                # 合并近似共面的小面, 让胞体面数更少更规整
                bm.normal_update()
                bmesh.ops.dissolve_limit(bm, verts=list(bm.verts), angle_limit=0.001)
            for face in bm.faces:
                face.smooth = use_smooth_faces
                face.material_index = material_index
            # 网格和物体属于不同命名空间;切割期间使用临时名,收尾统一命名
            mesh = track(tracker, "meshes", bpy.data.meshes.new(shard_name() + "_cell"))
            bm.to_mesh(mesh)
        if use_data_match:
            # 复制材质槽与 UV/颜色层, 让布尔插值能带上外表面数据
            for material in obj.data.materials:
                mesh.materials.append(material)
            for layer in obj.data.uv_layers:
                mesh.uv_layers.new(name=layer.name)
            for attribute in obj.data.color_attributes:
                mesh.color_attributes.new(
                    name=attribute.name, type=attribute.data_type, domain=attribute.domain
                )
        cell = track(tracker, "objects", bpy.data.objects.new(shard_name(), mesh))
        shard_collection().objects.link(cell)
        # 胞体顶点相对于种子点,平移到 center 后恢复其世界空间位置
        cell.location = center
        if naming is not None:
            naming.mark(cell, naming.source_name, level, group)
        objects.append(cell)
        if use_data_match:
            # 保持材质槽的链接方式与来源一致; 目标槽位刚由 data.materials 拷贝而来,
            # 两侧长度实际一致, 但这里只是尽力同步, 不该因槽位数量差异中断整次切割,
            # 因此显式声明 strict=False
            for source_slot, target_slot in zip(
                obj.material_slots, cell.material_slots, strict=False
            ):
                target_slot.link = source_slot.link
                target_slot.material = source_slot.material
        if use_debug_redraw:
            context.view_layer.update()
            redraw()
    context.view_layer.update()
    return objects


def intersect_cells(
    context,
    obj,
    objects,
    use_debug_bool=False,
    clean=True,
    use_island_split=False,
    use_interior_hide=False,
    use_debug_redraw=False,
    level=0,
    remove_doubles=True,
    tracker=None,
):
    """把每个胞体与源物体求交, 得到贴合表面的碎块, 返回碎块列表"""
    # 焊接距离按源物体包围盒对角线派生, 与胞体阶段保持同一套尺度
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    diagonal = Vector(
        tuple(max(p[i] for p in corners) - min(p[i] for p in corners) for i in range(3))
    ).length
    weld_dist = _weld_distances(diagonal)[0]
    if use_interior_hide and level == 0:
        obj.data.polygons.foreach_set("hide", [False] * len(obj.data.polygons))
        obj.data.update()
    # 求解策略只在布尔边界决定,每个源/递归父块检查一次,所有胞体共用
    source = boolean.inspect_source(obj, context.evaluated_depsgraph_get())
    modifiers = {}
    for cell in objects:
        modifier = cell.modifiers.new(name="Boolean", type="BOOLEAN")
        modifier.object = obj
        boolean.configure_intersection(modifier, source)
        modifiers[cell] = modifier
        if use_interior_hide and not use_debug_bool:
            # 先用隐藏标记区分内外表面, 收尾阶段再据此判定内部断面
            cell.data.polygons.foreach_set("hide", [True] * len(cell.data.polygons))
            cell.data.update()
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    results = []
    for cell in objects:
        if not use_debug_bool:
            # 求值后的网格就是布尔结果, 直接取出来替换胞体网格并移除修改器
            # 名字这里不管: 全部切完后 apply_names() 会把物体与网格一起定名
            evaluated = cell.evaluated_get(depsgraph)
            mesh = track(
                tracker,
                "meshes",
                bpy.data.meshes.new_from_object(
                    evaluated, preserve_all_data_layers=True, depsgraph=depsgraph
                ),
            )
            boolean.validate_intersection(mesh, evaluated.matrix_world, source)
            old_mesh = cell.data
            cell.data = mesh
            cell.modifiers.remove(modifiers[cell])
            if old_mesh.users == 0:
                bpy.data.meshes.remove(old_mesh)
            # 求交结果为空说明胞体落在源物体之外, 丢弃
            if not mesh.polygons:
                remove_generated_object(cell)
                continue
            if clean or remove_doubles:
                with mesh_edit(mesh) as bm:
                    if clean:
                        bm.normal_update()
                        bmesh.ops.dissolve_limit(
                            bm, verts=list(bm.verts), edges=list(bm.edges), angle_limit=0.001
                        )
                    if remove_doubles:
                        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_dist)
                    bm.to_mesh(mesh)
        else:
            # 保留修改器时检查求值结果;基础凸胞体超出源轮廓是正常的
            evaluated = cell.evaluated_get(depsgraph)
            try:
                mesh = evaluated.to_mesh()
                boolean.validate_intersection(mesh, evaluated.matrix_world, source)
            finally:
                evaluated.to_mesh_clear()
        results.append(cell)
        if use_debug_redraw:
            redraw()
    if results and use_island_split and not use_debug_bool:
        # separate LOOSE 支持物体模式;先管理选择集,避免拆分其他物体
        # 视图层里可能残留已失效的空条目 (递归删除上级碎块后尤其明显),
        # 所以每一项都先判空再访问, 否则这里会抛 NoneType 属性错误
        for item in context.view_layer.objects:
            if item is not None and item.select_get():
                item.select_set(False)
        for cell in results:
            if cell is not None:
                cell.select_set(True)
        context.view_layer.objects.active = results[0]
        # 物体副本会连自定义属性一起复制, 因此用一次性标识认领本次拆分结果,
        # 这样即使别的插件改动了选择集也不会认错
        token = uuid.uuid4().hex
        for cell in results:
            cell["_qd_split_owner"] = token
        try:
            with context.temp_override(
                object=results[0],
                active_object=results[0],
                selected_objects=results,
                selected_editable_objects=results,
            ):
                bpy.ops.mesh.separate(type="LOOSE")
        finally:
            results = [cell for cell in bpy.data.objects if cell.get("_qd_split_owner") == token]
            for cell in results:
                track(tracker, "objects", cell)
                track(tracker, "meshes", cell.data)
                del cell["_qd_split_owner"]
    context.view_layer.update()
    return results


def finish_interior(
    objects, use_interior_vgroup=False, use_sharp_edges=False, use_sharp_edges_apply=False
):
    """全部切割完成后统一处理内部断面: 顶点组/锐边/以及取消隐藏"""
    for cell in objects:
        with mesh_edit(cell.data) as bm:
            if use_interior_vgroup:
                # 隐藏标记为真的面就是内部断面, 只属于内部面的顶点才计入组
                exterior = {v for face in bm.faces if not face.hide for v in face.verts}
                group = cell.vertex_groups.get(INTERIOR_VGROUP_NAME)
                if group is None:
                    group = cell.vertex_groups.new(name=INTERIOR_VGROUP_NAME)
                deform = bm.verts.layers.deform.verify()
                for vert in bm.verts:
                    if vert not in exterior:
                        # 必须先建组再按组索引写权重, 否则会写进用户的其它组
                        vert[deform][group.index] = 1.0
            if use_sharp_edges:
                # 两侧隐藏状态不同的边即为内外交界边
                for edge in bm.edges:
                    if len({face.hide for face in edge.link_faces}) == 2:
                        edge.smooth = False
                if use_sharp_edges_apply:
                    edges = [edge for edge in bm.edges if not edge.smooth]
                    if edges:
                        bm.normal_update()
                        bmesh.ops.split_edges(bm, edges=edges)
            # 收尾后取消全部隐藏, 隐藏状态只用于内部判定
            for face in bm.faces:
                face.hide = False
            bm.to_mesh(cell.data)
