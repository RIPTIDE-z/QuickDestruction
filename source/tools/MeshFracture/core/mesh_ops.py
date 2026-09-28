"""网格操作 - 胞体建网格, 布尔求交, 孤岛拆分与断面收尾

面合并只在全部切割结束后对最终碎块做一次, 原因见 doc/Architecture.md 的 MeshFracture 一节
"""

import uuid

import bmesh
import bpy
from mathutils import Matrix, Vector

from ....common.blender_context import mesh_edit, select_only
from ....common.errors import require_finite
from ..constants import (
    INTERIOR_VGROUP_NAME,
    WELD_SCALE_RATIO,
    WELD_TO_JITTER_RATIO,
)
from ..errors import NON_FINITE_MESSAGE, MeshFractureError
from . import boolean
from .cells import points_as_bmesh_cells
from .sampling import points_from_object, random_unit_vector

# 孤岛拆分时认领结果用的自定义属性名, 拆分结束即删除
SPLIT_TOKEN = "_qd_split_owner"


def uses_interior(settings):
    """是否需要区分内部断面: 内部顶点组与锐边都要按面隐藏标记判定断面"""
    return settings["use_interior_vgroup"] or settings["use_sharp_edges"]


def _redraw():
    """实时显示进度时立即重绘窗口, 后台模式没有窗口时跳过"""
    if not bpy.app.background:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def remove_generated_object(obj):
    """删除生成的物体, 并回收随之失去用户的网格"""
    # 先记下网格引用: 物体删除后 obj.data 不再可读
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def seed_points(context, obj, settings, level, rng):
    """采样种子点: 取来源, 为空则回退自身顶点, 按本层上限随机截断, 精确去重并检查有限性"""
    depsgraph = context.evaluated_depsgraph_get()
    points = points_from_object(context, depsgraph, obj, settings["source"])
    if not points:
        points = points_from_object(context, depsgraph, obj, {"VERT_OWN"})
    # 递归层换用递归种子上限
    limit = settings["recursion_source_limit"] if level else settings["source_limit"]
    if limit and limit < len(points):
        # 先打乱再截断, 得到随机子集而不是前 N 个
        rng.shuffle(points)
        points = points[:limit]
    # 精确去重: 按小数位取整会丢掉小尺寸物体的真实点
    points = list({tuple(point): point for point in points}.values())
    # 快照只检查源物体的顶点, 这里检查粒子, 子物体与注解等来源的种子点
    require_finite((points,), MeshFractureError, NON_FINITE_MESSAGE)
    return points


def _scatter_points(points, scale, source_noise, rng):
    """噪波: 每个种子点沿随机方向位移, 半径在 0 到上限之间均匀取值; 强度为 0 时不消耗随机流"""
    if not source_noise:
        return points
    return [
        point + random_unit_vector(rng) * (source_noise * scale * 0.5 * rng.random())
        for point in points
    ]


def _create_debug_points(points, batch):
    """建立 DebugPoints 诊断几何, 显示实际采样到的种子点"""
    tracker = batch.naming.tracker
    mesh = tracker.track("meshes", bpy.data.meshes.new("DebugPoints"))
    mesh.from_pydata(points, [], [])
    debug_object = tracker.track("objects", bpy.data.objects.new("DebugPoints", mesh))
    batch.collection.objects.link(debug_object)


def _dissolve_coplanar(bm, *, with_edges=False):
    """合并近似共面的小面; 胞体阶段只按顶点判断, 最终碎块还要按边判断"""
    bm.normal_update()
    options = {"verts": list(bm.verts), "angle_limit": 0.001}
    if with_edges:
        options["edges"] = list(bm.edges)
    bmesh.ops.dissolve_limit(bm, **options)


def _convex_cell_mesh(cell_points, weld_dist, jitter_half, settings, batch, rng):
    """把一个胞体的顶点表变成凸包网格; 顶点不足或凸包失败时返回 None"""
    with mesh_edit() as bm:
        # 轻微抖动有助焊接重合点; 每个顶点都先取随机数, 保证随机流的消费次序固定
        for point in cell_points:
            jitter = Vector(tuple(rng.uniform(-jitter_half, jitter_half) for _ in range(3)))
            bm.verts.new(point + jitter)
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_dist)
        if len(bm.verts) < 4:
            return None
        try:
            bmesh.ops.convex_hull(bm, input=list(bm.verts))
        except RuntimeError:
            # 退化输入会让凸包失败, 跳过该胞体而不是中断整次切割
            return None
        if not bm.faces:
            return None
        _dissolve_coplanar(bm)
        # 写在基础网格上的着色与材质会经布尔求值带到碎块上
        for face in bm.faces:
            face.smooth = settings["use_smooth_faces"]
            face.material_index = settings["material_index"]
        mesh = batch.new_mesh()
        bm.to_mesh(mesh)
    return mesh


def _copy_source_layers(mesh, source_mesh):
    """复制材质槽与 UV, 颜色层; 布尔求交本身也会合并这些层, 预先建好才能保持活动层与材质槽一致"""
    for material in source_mesh.materials:
        mesh.materials.append(material)
    for layer in source_mesh.uv_layers:
        mesh.uv_layers.new(name=layer.name)
    for attribute in source_mesh.color_attributes:
        mesh.color_attributes.new(
            name=attribute.name, type=attribute.data_type, domain=attribute.domain
        )


def _sync_slot_links(source_object, cell):
    """让胞体的材质槽链接方式与来源一致"""
    # 槽位刚由网格材质拷贝而来, 两侧长度一致; strict=False 只是不因数量差异中断切割
    for source_slot, target_slot in zip(
        source_object.material_slots, cell.material_slots, strict=False
    ):
        target_slot.link = source_slot.link
        target_slot.material = source_slot.material


def build_cells(context, obj, snapshot, points, settings, batch, rng):
    """由种子点建凸胞体物体; 随机流在采样之后按噪波, 顶点抖动的次序消费"""
    if not points:
        return []
    points = _scatter_points(points, snapshot.diagonal, settings["source_noise"], rng)
    weld_dist = snapshot.diagonal * WELD_SCALE_RATIO
    jitter_half = weld_dist / WELD_TO_JITTER_RATIO
    if settings["use_debug_points"]:
        _create_debug_points(points, batch)
    cells = points_as_bmesh_cells(points, settings["cell_scale"], settings["margin"], snapshot)
    objects = []
    for center, cell_points in cells:
        mesh = _convex_cell_mesh(cell_points, weld_dist, jitter_half, settings, batch, rng)
        if mesh is None:
            continue
        _copy_source_layers(mesh, obj.data)
        cell = batch.new_object(mesh, center)
        objects.append(cell)
        _sync_slot_links(obj, cell)
        if settings["use_debug_redraw"]:
            context.view_layer.update()
            _redraw()
    context.view_layer.update()
    return objects


def recenter(cells):
    """把每个碎块的原点移到顶点平均位置, 即 Blender 重设原点的 MEDIAN 方式, 世界空间几何不动"""
    for cell in cells:
        mesh = cell.data
        count = len(mesh.vertices)
        flat = [0.0] * (count * 3)
        mesh.vertices.foreach_get("co", flat)
        center = Vector((sum(flat[0::3]), sum(flat[1::3]), sum(flat[2::3]))) / count
        # 网格反向平移, 物体矩阵正向平移, 两者抵消后世界坐标不变
        mesh.transform(Matrix.Translation(-center))
        cell.matrix_world = cell.matrix_world @ Matrix.Translation(center)


def _add_intersection(cell, obj, snapshot, hide_faces):
    """给胞体加布尔求交修改器; hide_faces 时把胞体面全部标记为隐藏, 返回修改器"""
    modifier = cell.modifiers.new(name="Boolean", type="BOOLEAN")
    modifier.object = obj
    boolean.configure_intersection(modifier, snapshot)
    if hide_faces:
        # 布尔结果里仍然隐藏的面来自胞体, 就是内部断面
        cell.data.polygons.foreach_set("hide", [True] * len(cell.data.polygons))
        cell.data.update()
    return modifier


def _bake_intersection(cell, modifier, snapshot, depsgraph, tracker):
    """取布尔求值结果替换胞体网格并移除修改器, 返回新网格"""
    with boolean.validated_evaluation(cell, modifier, snapshot, depsgraph) as evaluated:
        # 直接复制依赖图里已求值的网格, 避免按全部数据层重新求值修改器栈
        mesh = tracker.track("meshes", bpy.data.meshes.new_from_object(evaluated))
    old_mesh = cell.data
    cell.data = mesh
    cell.modifiers.remove(modifier)
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    return mesh


def _flatten_faces(bm, tolerance):
    """把平面偏离超过容差的面三角化; 三角形必然共面, 只看四边形及以上的面"""
    targets = []
    for face in bm.faces:
        if len(face.verts) <= 3:
            continue
        normal = face.normal
        origin = face.verts[0].co
        # 退化面没有法线, 同样需要三角化
        if (
            normal.length == 0
            or max(abs((vert.co - origin).dot(normal)) for vert in face.verts) > tolerance
        ):
            targets.append(face)
    if targets:
        bmesh.ops.triangulate(bm, faces=targets)


def _weld_intersection(mesh, weld_dist):
    """整理求交结果供递归使用: 焊接重合顶点, 再三角化非平面面; 结果仍需校验"""
    with mesh_edit(mesh) as bm:
        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_dist)
        # 焊接会改动拓扑, 重新算法线后才能判断面是否共面
        bm.normal_update()
        _flatten_faces(bm, weld_dist)
        bm.to_mesh(mesh)


def dissolve_final_shards(cells):
    """对最终碎块合并近共面面, 只在全部切割结束后调用一次"""
    for cell in cells:
        with mesh_edit(cell.data) as bm:
            _dissolve_coplanar(bm, with_edges=True)
            bm.to_mesh(cell.data)
        cell.data.update()


def _island_sort_key(obj):
    """孤岛结果的稳定排序键: 只用几何本身, 位置优先, 再比包围盒与顶点数"""
    mesh = obj.data
    count = len(mesh.vertices)
    if not count:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0)
    flat = [0.0] * (count * 3)
    mesh.vertices.foreach_get("co", flat)
    axes = (flat[0::3], flat[1::3], flat[2::3])
    return (
        tuple(round(min(axis), 6) for axis in axes),
        tuple(round(max(axis), 6) for axis in axes),
        count,
    )


def _split_islands(context, cells, batch):
    """按不连通块拆分并认领结果, 按几何排序后返回; separate 的产出顺序不定, 排序保证可复现"""
    # separate 在物体模式下作用于选中的物体, 因此只选中本批碎块
    select_only(context, cells, cells[0])
    # 物体副本会连自定义属性一起复制, 用一次性令牌认领本次拆分的结果
    token = uuid.uuid4().hex
    for cell in cells:
        cell[SPLIT_TOKEN] = token
    try:
        with context.temp_override(
            object=cells[0],
            active_object=cells[0],
            selected_objects=cells,
            selected_editable_objects=cells,
        ):
            bpy.ops.mesh.separate(type="LOOSE")
    finally:
        # 拆出的物体与原物体在同一集合里, 只扫描本批集合; 抛错时也要完成认领与清理
        claimed = [item for item in batch.collection.objects if item.get(SPLIT_TOKEN) == token]
        for item in claimed:
            batch.naming.tracker.track("objects", item)
            batch.naming.tracker.track("meshes", item.data)
            del item[SPLIT_TOKEN]
    return sorted(claimed, key=_island_sort_key)


def intersect_cells(context, obj, snapshot, cells, settings, batch):
    """把每个胞体与源物体求交得到贴合表面的碎块; 只焊接与压平结果, 不合并面"""
    # 焊接距离与胞体阶段共用同一份快照尺度
    weld_dist = snapshot.diagonal * WELD_SCALE_RATIO
    debug = settings["use_debug_bool"]
    use_hide = uses_interior(settings)
    if use_hide and batch.level == 0:
        # 首次切割先清掉源物体原有的面隐藏标记, 内部断面判定要从干净的起点开始
        obj.data.polygons.foreach_set("hide", [False] * len(obj.data.polygons))
        obj.data.update()
    modifiers = [_add_intersection(cell, obj, snapshot, use_hide and not debug) for cell in cells]
    # 修改器与隐藏标记都要先刷新视图层, 依赖图里才会包含它们
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    results = []
    for cell, modifier in zip(cells, modifiers, strict=True):
        if debug:
            # 保留修改器, 只校验求值结果是否越界
            with boolean.validated_evaluation(cell, modifier, snapshot, depsgraph):
                pass
        else:
            mesh = _bake_intersection(cell, modifier, snapshot, depsgraph, batch.naming.tracker)
            if not mesh.polygons:
                # 求交为空说明胞体落在源物体之外
                remove_generated_object(cell)
                continue
            _weld_intersection(mesh, weld_dist)
        results.append(cell)
        if settings["use_debug_redraw"]:
            _redraw()
    if results and settings["use_island_split"] and not debug:
        results = _split_islands(context, results, batch)
    context.view_layer.update()
    return results


def _mark_interior_group(cell, bm):
    """只属于内部断面的顶点计入内部顶点组, 与外表面共享的边界顶点不计入"""
    exterior = {vert for face in bm.faces if not face.hide for vert in face.verts}
    group = cell.vertex_groups.get(INTERIOR_VGROUP_NAME)
    if group is None:
        group = cell.vertex_groups.new(name=INTERIOR_VGROUP_NAME)
    deform = bm.verts.layers.deform.verify()
    for vert in bm.verts:
        if vert not in exterior:
            # 先建组再按组索引写权重, 否则会写进用户的其它组
            vert[deform][group.index] = 1.0


def _mark_sharp_edges(bm):
    """两侧隐藏状态不同的边就是内外交界边, 标记为锐边; 着色直接读取锐边, 不拆开几何"""
    for edge in bm.edges:
        if len({face.hide for face in edge.link_faces}) == 2:
            edge.smooth = False


def finish_interior(cells, settings):
    """全部切割完成后统一处理内部断面: 顶点组, 锐边, 最后取消全部隐藏"""
    for cell in cells:
        with mesh_edit(cell.data) as bm:
            if settings["use_interior_vgroup"]:
                _mark_interior_group(cell, bm)
            if settings["use_sharp_edges"]:
                _mark_sharp_edges(bm)
            # 隐藏标记只用于内部判定, 收尾后全部取消
            for face in bm.faces:
                face.hide = False
            bm.to_mesh(cell.data)
