"""网格操作 - 胞体建网格, 布尔求交, 孤岛拆分与断面收尾

两个主函数 build_cells 与 intersect_cells 只做阶段编排,
每一步都拆成下面的私有帮手; ShardBatch 负责携带一批碎块的落点与身份
减少操作数的非平面面, 且面合并只在收尾做一次: 中间碎块会被递归当成操作数
"""

import random
import uuid
from dataclasses import dataclass

import bmesh
import bpy
from mathutils import Vector

from ...common import measure
from ...common.blender_context import mesh_edit
from ...common.created_data import track
from ...common.errors import require_finite
from ..constants import (
    INTERIOR_VGROUP_NAME,
    RANDOM_SEED_DEFAULT,
    WELD_SCALE_RATIO,
    WELD_TO_JITTER_RATIO,
)
from ..errors import NON_FINITE_MESSAGE, MeshFractureError
from . import boolean
from .sampling import points_from_object, random_unit_vector


@dataclass(frozen=True)
class ShardBatch:
    """一批碎块的落点与身份: 输出集合, 组集合, 命名计划与层组号"""

    # 输出集合; 没有组集合时碎块直接落在这里
    collection: object
    # 本轮的组集合; None 表示不建组集合, 直接落输出集合
    target: object | None
    # 本次切割的命名计划; None 表示独立调用本阶段
    naming: object | None
    # 层号: 0 是首次切割, 大于 0 是递归细分
    level: int
    # 组号: 同一层内的第几个父块; 首次切割传 0
    group: int | None
    # 本次运行的回收账本; 允许为 None
    tracker: object | None

    @property
    def destination(self):
        """本批碎块落进的集合: 有组集合时用组集合, 否则用输出集合"""
        return self.target if self.target is not None else self.collection

    def next_name(self, fallback):
        """切割期的临时名; 没有命名计划时用调用方给的后备名"""
        return self.naming.temp_name() if self.naming is not None else fallback


@dataclass(frozen=True)
class CellStyle:
    """胞体基础网格的外观选项, 只在本阶段使用"""

    # 是否合并近似共面的小面
    clean: bool
    # 基础网格的平滑着色标志
    use_smooth_faces: bool
    # 断面材质槽索引
    material_index: int


def _weld_distances(scale):
    """由物体尺度推出焊接距离与抖动半幅, 抖动取焊接距离的几分之一"""
    # scale 是包围盒对角线: 两个距离都是它的固定比例, 因此与物体绝对大小无关
    distance = scale * WELD_SCALE_RATIO
    return distance, distance / WELD_TO_JITTER_RATIO


def redraw():
    """后台模式下没有窗口, 跳过重绘"""
    if not bpy.app.background:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def remove_generated_object(obj):
    """删除物体, 并回收随之失去用户的网格数据"""
    # 先记下网格引用: 物体删除后 obj.data 不再可读
    mesh = obj.data if obj.type == "MESH" else None
    bpy.data.objects.remove(obj, do_unlink=True)
    # 仅在没有其他使用者时回收网格, 避免误删共享数据
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _seed_points(context, depsgraph, obj, source, source_limit, rng):
    """采样种子点: 取来源, 为空则回退自身顶点, 按上限随机截断, 再精确去重"""
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
    return list({tuple(point): point for point in points}.values())


def _scatter_points(points, scale, source_noise, rng):
    """按噪波强度把种子点扰动成球内均匀分布; 强度为 0 时不消耗随机流"""
    if not source_noise:
        return points
    # 扰动规则点阵, 避免出现过于整齐的切割面
    return [
        point + random_unit_vector(rng) * (source_noise * scale * 0.5 * rng.random())
        for point in points
    ]


def _create_debug_points(points, collection, tracker):
    """建立 DebugPoints 诊断几何, 用于查看实际采样到的种子点位置"""
    mesh = track(tracker, "meshes", bpy.data.meshes.new("DebugPoints"))
    mesh.from_pydata(points, [], [])
    debug_object = track(tracker, "objects", bpy.data.objects.new("DebugPoints", mesh))
    collection.objects.link(debug_object)


def _weld_vertices(bm, weld_dist):
    """把间距小于焊接距离的顶点合并成一个"""
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_dist)


def _dissolve_coplanar(bm, *, with_edges=False):
    """合并近似共面的小面, 让面数更少更规整

    胞体阶段只按顶点判断, 布尔结果还要按边判断, 因此用一个开关表达这处差异
    """
    bm.normal_update()
    options = {"verts": list(bm.verts), "angle_limit": 0.001}
    if with_edges:
        options["edges"] = list(bm.edges)
    bmesh.ops.dissolve_limit(bm, **options)


def _convex_body(bm, weld_dist):
    """焊接重合点并闭合成凸包; 顶点不足或退化时返回 False"""
    _weld_vertices(bm, weld_dist)
    # 少于四个顶点无法构成凸体, 直接丢弃
    if len(bm.verts) < 4:
        return False
    try:
        bmesh.ops.convex_hull(bm, input=list(bm.verts))
    except RuntimeError:
        # 退化输入会让凸包失败, 跳过该胞体而不是中断整次切割
        return False
    return bool(bm.faces)


def _convex_cell_mesh(cell_points, weld_dist, jitter_half, style, rng, batch, fallback_name):
    """把一个胞体的顶点表变成凸包网格; 无法构成凸体时返回 None"""
    with mesh_edit() as bm:
        # 轻微抖动有助焊接重合点, 抖动幅度远小于焊接距离
        for point in cell_points:
            jitter = Vector(tuple(rng.uniform(-jitter_half, jitter_half) for _ in range(3)))
            bm.verts.new(point + jitter)
        if not _convex_body(bm, weld_dist):
            return None
        if style.clean:
            # 合并近似共面的小面, 让胞体面数更少更规整
            _dissolve_coplanar(bm)
        # 写在基础网格上的着色与材质会在布尔求值后带到碎块上
        for face in bm.faces:
            face.smooth = style.use_smooth_faces
            face.material_index = style.material_index
        # 网格和物体属于不同命名空间; 切割期间使用临时名, 收尾统一命名
        name = batch.next_name(fallback_name) + "_cell"
        mesh = track(batch.tracker, "meshes", bpy.data.meshes.new(name))
        bm.to_mesh(mesh)
    return mesh


def _copy_source_layers(mesh, source_mesh):
    """复制材质槽与 UV/颜色层, 让布尔插值能带上外表面数据"""
    for material in source_mesh.materials:
        mesh.materials.append(material)
    for layer in source_mesh.uv_layers:
        mesh.uv_layers.new(name=layer.name)
    for attribute in source_mesh.color_attributes:
        mesh.color_attributes.new(
            name=attribute.name, type=attribute.data_type, domain=attribute.domain
        )


def _place_cell(mesh, center, batch, fallback_name):
    """建物体, 入目标集合, 把原点设到种子点, 并写入身份标记"""
    cell = track(
        batch.tracker, "objects", bpy.data.objects.new(batch.next_name(fallback_name), mesh)
    )
    batch.destination.objects.link(cell)
    # 胞体顶点相对于种子点, 平移到 center 后恢复其世界空间位置
    cell.location = center
    if batch.naming is not None:
        # 独立调用时没有命名计划, 也就没有身份标记
        batch.naming.mark(cell, batch.naming.source_name, batch.level, batch.group)
    return cell


def _sync_slot_links(source_object, cell):
    """让胞体的材质槽链接方式与来源一致; 数量不一致时只尽力同步"""
    # 目标槽位刚由 data.materials 拷贝而来, 两侧长度实际一致, 但这里只是尽力同步,
    # 不该因槽位数量差异中断整次切割, 因此显式声明 strict=False
    for source_slot, target_slot in zip(
        source_object.material_slots, cell.material_slots, strict=False
    ):
        target_slot.link = source_slot.link
        target_slot.material = source_slot.material


def _prepare_geometry(context, depsgraph, obj, snapshot, source, source_limit, source_noise, rng):
    """采样种子点并求出尺度

    返回 (种子点, 顶点, 焊接距离, 抖动半幅); 种子点为空时返回 None
    顶点与尺度取自快照, 本阶段不再对源物体求值一次
    随机流的消费顺序在这里固定: 截断洗牌 -> 噪波
    """
    points = _seed_points(context, depsgraph, obj, source, source_limit, rng)
    if not points:
        return None
    # 快照已保证顶点存在且有限, 这里只查本次采样得到的种子点
    require_finite((points,), MeshFractureError, NON_FINITE_MESSAGE)
    scale = snapshot.diagonal
    # 噪波用尺度换算位移上限, 因此必须排在求尺度之后
    points = _scatter_points(points, scale, source_noise, rng)
    weld_dist, jitter_half = _weld_distances(scale)
    return points, snapshot.vertices, weld_dist, jitter_half


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
    snapshot=None,
):
    """采样种子点并生成凸胞体, 返回胞体物体列表

    naming 为 NamingPlan 时: level 与 group 分别是整数层号和组号,
    target 是这批碎块要落进的组集合 (可能为 None, 表示直接落在输出集合)
    这两项由调用方决定, 因为"这一批属于哪一组"是流程层面的信息, 这里不去猜
    naming 为 None 时使用 "<源物体名>_cell", 允许独立调用本阶段
    rng 由管线在各轮间共享; 单独调用未提供时使用固定默认种子的局部随机流
    snapshot 是流程编排取好的求值源快照; 为 None 时本阶段自己求值一次

    阶段顺序固定为: 采样种子 -> 非有限检查 -> 取尺度 -> 噪波
    -> 建胞体网格 -> 落点; 随机流的消费顺序也按这个次序
    """
    # 延迟导入: cells 只依赖 mathutils, 放模块顶层会把几何依赖提前拉起
    from .cells import points_as_bmesh_cells

    if rng is None:
        # 独立调用本函数时给一个固定种子的局部随机流, 保证结果可复现
        rng = random.Random(RANDOM_SEED_DEFAULT)
    depsgraph = context.evaluated_depsgraph_get()
    if snapshot is None:
        # 独立调用本阶段时自己求值一次源物体
        snapshot = boolean.inspect_source(obj, depsgraph)
    batch = ShardBatch(collection, target, naming, level, group, tracker)
    # 没有命名计划时的后备名, 网格与物体各取一次
    fallback_name = obj.name + "_cell"
    geometry = _prepare_geometry(
        context, depsgraph, obj, snapshot, source, source_limit, source_noise, rng
    )
    if geometry is None:
        return []
    points, verts, weld_dist, jitter_half = geometry
    if use_debug_points:
        # 诊断几何, 跟随碎块一起落点
        _create_debug_points(points, batch.destination, batch.tracker)
    style = CellStyle(clean, use_smooth_faces, material_index)
    # 胞体构造: points 是 Voronoi 站点, 初始包围盒复用快照已经量过的结果
    cells = points_as_bmesh_cells(
        verts, points, cell_scale, margin_cell=margin, bounds=(snapshot.minimum, snapshot.maximum)
    )
    objects = []
    for center, cell_points in cells:
        mesh = _convex_cell_mesh(
            cell_points, weld_dist, jitter_half, style, rng, batch, fallback_name
        )
        if mesh is None:
            continue
        if use_data_match:
            _copy_source_layers(mesh, obj.data)
        cell = _place_cell(mesh, center, batch, fallback_name)
        objects.append(cell)
        if use_data_match:
            _sync_slot_links(obj, cell)
        if use_debug_redraw:
            context.view_layer.update()
            redraw()
    context.view_layer.update()
    return objects


def _object_diagonal(obj):
    """源物体自身包围盒的体对角线长度

    布尔阶段用它派生焊接距离, 与胞体阶段的求值尺度各自独立:
    这里的对象是源物体自身的尺寸, 不受修改器输出大小影响
    """
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return measure.points_diagonal(corners)


def _unhide_source_faces(obj):
    """清掉源物体上的面隐藏标记, 为内部断面判定提供干净起点"""
    obj.data.polygons.foreach_set("hide", [False] * len(obj.data.polygons))
    obj.data.update()


def _add_boolean_modifiers(objects, obj, source, use_interior_hide, use_debug_bool):
    """给每个胞体加布尔求交修改器, 并按需把胞体面标记为隐藏; 返回修改器表"""
    modifiers = {}
    for cell in objects:
        modifier = cell.modifiers.new(name="Boolean", type="BOOLEAN")
        modifier.object = obj
        boolean.configure_intersection(modifier, source)
        # 记下修改器引用, 取完求值结果后逐个移除
        modifiers[cell] = modifier
        if use_interior_hide and not use_debug_bool:
            # 先用隐藏标记区分内外表面, 收尾阶段再据此判定内部断面
            cell.data.polygons.foreach_set("hide", [True] * len(cell.data.polygons))
            cell.data.update()
    return modifiers


def _bake_intersection(cell, modifier, source, depsgraph, tracker):
    """取布尔求值结果替换胞体网格, 返回新网格; 调用方负责判空与清理"""
    # 求值后的网格就是布尔结果, 直接取出来替换胞体网格并移除修改器
    # 名字这里不管: 全部切完后 apply_names() 会把物体与网格一起定名
    # 先验证临时求值结果 - 重试失败不会留下持久化的无用户网格
    with boolean.validated_evaluation(cell, modifier, source, depsgraph) as evaluated:
        mesh = track(
            tracker,
            "meshes",
            bpy.data.meshes.new_from_object(
                evaluated, preserve_all_data_layers=True, depsgraph=depsgraph
            ),
        )
    # 换网格前先记下旧网格, 换完再按使用者数量决定是否回收
    old_mesh = cell.data
    cell.data = mesh
    cell.modifiers.remove(modifier)
    if old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    return mesh


def _validate_pending_cell(cell, modifier, source, depsgraph):
    """调试布尔路径: 保留修改器, 只校验求值结果是否越界"""
    # 保留修改器时检查求值结果; 基础凸胞体超出源轮廓是正常的
    with boolean.validated_evaluation(cell, modifier, source, depsgraph):
        pass


def _flatten_faces(bm, tolerance):
    """把平面偏离超过容差的面三角化, 返回处理的面数

    三角形必然共面, 因此只需要看四边形及以上的面;
    退化面没有法线, 同样需要处理
    """
    targets = []
    for face in bm.faces:
        if len(face.verts) <= 3:
            continue
        normal = face.normal
        if normal.length == 0:
            targets.append(face)
            continue
        origin = face.verts[0].co
        if max(abs((vert.co - origin).dot(normal)) for vert in face.verts) > tolerance:
            targets.append(face)
    if targets:
        # 三角化明确面片平面 - 非平面多边形的表面可能随对角线选择改变
        bmesh.ops.triangulate(bm, faces=targets)
    return len(targets)


def _weld_intersection(mesh, weld_dist):
    """整理求交结果以供递归使用: 焊接顶点, 再处理超出容差的非平面面

    求交会产生大量重合顶点, 不焊接会让后续的递归切割拿到退化输入
    把偏离超过焊接距离的面三角化可缓解部分递归求交失败
    该处理不保证操作数无自交或始终有效 - 求交仍需验证
    """
    with mesh_edit(mesh) as bm:
        _weld_vertices(bm, weld_dist)
        # 焊接会改动拓扑, 重新算法线后才能判断面是否共面
        bm.normal_update()
        _flatten_faces(bm, weld_dist)
        bm.to_mesh(mesh)


def dissolve_final_shards(cells):
    """对最终碎块合并近共面面; 收尾阶段调用一次

    合并面会把多个近共面的面并成一个 n-gon, 而布尔结果里这类 n-gon 并不共面
    不共面的 n-gon 会让精确布尔留下未裁剪的残片, 所以面合并不能作用在
    还会被递归当成布尔操作数的中间碎块上, 只能留到全部切割结束之后再统一做
    """
    for cell in cells:
        with mesh_edit(cell.data) as bm:
            _dissolve_coplanar(bm, with_edges=True)
            bm.to_mesh(cell.data)
        cell.data.update()


def _select_cells(context, cells):
    """只选中本批碎块并把首个设为活动物体, 避免拆分到其它物体"""
    # 视图层里可能残留已失效的空条目 (递归删除上级碎块后尤其明显),
    # 所以每一项都先判空再访问, 否则这里会抛 NoneType 属性错误
    for item in context.view_layer.objects:
        if item is not None and item.select_get():
            item.select_set(False)
    for cell in cells:
        if cell is not None:
            cell.select_set(True)
    context.view_layer.objects.active = cells[0]


def _island_sort_key(obj):
    """孤岛结果的稳定排序键, 只用几何本身, 与对象创建顺序无关

    bpy.ops.mesh.separate 产出对象的顺序不定 (内部按哈希遍历不连通块),
    而递归的停止阈值判断依赖处理顺序, 不排序会让同一输入得到不同的碎块数
    位置优先, 位置相同再比包围盒与顶点数, 保证键互不相同
    """
    mesh = obj.data
    count = len(mesh.vertices)
    if not count:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), 0)
    # foreach_get 一次取出全部坐标, 再按轴切片求极值; 避免逐顶点访问 RNA
    flat = [0.0] * (count * 3)
    mesh.vertices.foreach_get("co", flat)
    axes = (flat[0::3], flat[1::3], flat[2::3])
    return (
        tuple(round(min(axis), 6) for axis in axes),
        tuple(round(max(axis), 6) for axis in axes),
        count,
    )


def _split_islands(context, cells, tracker):
    """按不连通块拆分并认领结果, 返回拆分后的碎块列表"""
    # separate LOOSE 支持物体模式, 因此先把选择集限制在本批碎块上
    _select_cells(context, cells)
    # 物体副本会连自定义属性一起复制, 因此用一次性标识认领本次拆分结果,
    # 这样即使别的插件改动了选择集也不会认错
    token = uuid.uuid4().hex
    for cell in cells:
        cell["_qd_split_owner"] = token
    try:
        with context.temp_override(
            object=cells[0],
            active_object=cells[0],
            selected_objects=cells,
            selected_editable_objects=cells,
        ):
            bpy.ops.mesh.separate(type="LOOSE")
    finally:
        # separate 抛错时也要完成认领与清理, 异常随后继续上抛
        claimed = [cell for cell in bpy.data.objects if cell.get("_qd_split_owner") == token]
        for cell in claimed:
            track(tracker, "objects", cell)
            track(tracker, "meshes", cell.data)
            del cell["_qd_split_owner"]
    # 认领顺序来自 separate 的产出顺序, 这里按几何重新排序:
    # 命名与递归的阈值判断都按列表顺序进行, 顺序不定会让结果不可复现
    return sorted(claimed, key=_island_sort_key)


def intersect_cells(
    context,
    obj,
    objects,
    use_debug_bool=False,
    use_island_split=False,
    use_interior_hide=False,
    use_debug_redraw=False,
    level=0,
    tracker=None,
    snapshot=None,
):
    """把每个胞体与源物体求交, 得到贴合表面的碎块, 返回碎块列表

    snapshot 是流程编排取好的求值源快照; 为 None 时本阶段自己求值一次
    本阶段只焊接并压平求交结果, 不合并面: 见 dissolve_final_shards 的说明
    """
    # 焊接距离按源物体包围盒对角线派生, 与胞体阶段保持同一套尺度
    weld_dist = _weld_distances(_object_diagonal(obj))[0]
    if use_interior_hide and level == 0:
        # 只有首次切割需要干净起点: 源物体上原有的隐藏标记会干扰内部判定
        _unhide_source_faces(obj)
    # 求解策略只在布尔边界决定, 每个源/递归父块检查一次, 所有胞体共用
    if snapshot is None:
        snapshot = boolean.inspect_source(obj, context.evaluated_depsgraph_get())
    modifiers = _add_boolean_modifiers(objects, obj, snapshot, use_interior_hide, use_debug_bool)
    # 修改器与隐藏标记都要先刷新视图层, 依赖图里才会包含它们
    context.view_layer.update()
    depsgraph = context.evaluated_depsgraph_get()
    results = []
    for cell in objects:
        if use_debug_bool:
            _validate_pending_cell(cell, modifiers[cell], snapshot, depsgraph)
        else:
            mesh = _bake_intersection(cell, modifiers[cell], snapshot, depsgraph, tracker)
            # 求交结果为空说明胞体落在源物体之外, 丢弃该胞体
            if not mesh.polygons:
                remove_generated_object(cell)
                continue
            _weld_intersection(mesh, weld_dist)
        results.append(cell)
        if use_debug_redraw:
            redraw()
    if results and use_island_split and not use_debug_bool:
        results = _split_islands(context, results, tracker)
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
