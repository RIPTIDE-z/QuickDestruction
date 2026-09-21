"""种子点采样、凸胞体生成, 以及布尔求交后的网格收尾。"""

from contextlib import contextmanager
from math import isfinite
import random
import uuid

import bpy
import bmesh
from mathutils import Vector

# 焊接距离与抖动幅度按物体尺度派生, 不使用绝对量, 否则小物体会被焊掉真实几何
WELD_SCALE_RATIO = 1.0e-4
WELD_TO_JITTER_RATIO = 5.0
# 内部顶点组名是对外接口, 下游按名字取用, 因此保持中文且不参与翻译
INTERIOR_VGROUP_NAME = "内部"
INTERIOR_VGROUP_NAME_LEGACY = ("Interior",)


class CreatedData:
    """只记录本次显式创建的数据, 便于失败时精确回收。"""

    def __init__(self):
        self.ids = {'objects': [], 'meshes': [], 'collections': []}

    def track(self, kind, item):
        self.ids[kind].append(item)
        return item

    def rollback(self):
        """删除本次创建的数据; 已被替换或因递归移除的项直接跳过。"""
        for kind, items in self.ids.items():
            data = getattr(bpy.data, kind)
            for item in items:
                try:
                    # 名字被新数据占用说明该项已不是原来的对象, 不再处理
                    if data.get(item.name) != item:
                        continue
                    if kind == 'objects':
                        data.remove(item, do_unlink=True)
                    elif kind == 'meshes' and item.users == 0:
                        data.remove(item)
                    elif kind == 'collections' and not item.objects and not item.children:
                        data.remove(item)
                except ReferenceError:
                    continue


def track(tracker, kind, item):
    """tracker 允许为空, 便于单独调用底层函数做测试。"""
    return tracker.track(kind, item) if tracker is not None else item


@contextmanager
def mesh_edit(mesh=None):
    """BMesh 编辑上下文, 保证任何路径下都会 free。"""
    bm = bmesh.new()
    try:
        if mesh is not None:
            bm.from_mesh(mesh)
        yield bm
    finally:
        bm.free()


def _weld_distances(scale):
    """由物体尺度推出焊接距离与抖动半幅, 抖动取焊接距离的几分之一。"""
    distance = scale * WELD_SCALE_RATIO
    return distance, distance / WELD_TO_JITTER_RATIO


# 碎块与集合上的标记属性, 仅用于开发期自检与下游筛选, 不是对外接口, 因此不翻译
_SOURCE_MARK = '_qd_source'
_LEVEL_MARK = '_qd_level'
_GROUP_MARK = '_qd_group'
# 切割期的临时名前缀, 全部切完后会被 apply_names() 换成最终名字
TEMP_NAME_PREFIX = "QDTMP_"


class NamingPlan:
    """一次切割操作内的层级命名与集合安排。

    顶层没有层号: 第 0 层就是"还没被递归切开"的状态, 它的落点由输出集合决定 ——
    不分组时就是输出集合本身, 分组时是其中的第 1 组 (L1_G1)。

    命名规则:
      组集合   L<层>_G<组>            从 L1 开始, 例如 L1_G1, L1_G2; 不带用户前缀
      碎块     <前缀>_<组标识>_<序号>  例如 cell_L1G1_001; 序号在组内从 001 重计
      不分组   <前缀>_<序号>          例如 cell_001; 序号在整批里连续

    层号与组号之间加分隔符 (_G / G), 是为了消除纯数字拼接的歧义:
    拼接成 L210 时无法判断它是"第 2 层第 10 组"还是"第 21 层第 1 组"。

    组的含义: 每个被递归切开的碎块各占一组, 组号在层内递增;
    第 1 层的组装的是"一级碎块被切开后的子块", 所以每一层的组集合都对应
    上一层的某个碎块 —— 递归再深, 名字与所属集合始终一一对应。

    建立一次后逐层向下传递, 原因:
      1. 源物体名只读一次, 递归层名字被改过也不会污染命名
         (旧实现用 obj.name + "_cell", 递归时父碎块已叫 X_cell, 于是变成
          X_cell.001_cell, 每递归一层就再追加一次)
      2. 每一组的序号各自独立, 需要把"当前组"作为状态带到下一层
    """

    def __init__(self, source_object, collection, use_collections=True, prefix='cell',
                 padding=3, tracker=None):
        self.source_name = source_object.name
        self.collection = collection
        # 碎块名前缀, 只用于碎块名, 不进集合名
        self.prefix = prefix or ''
        self.padding = max(0, int(padding))
        self.use_collections = use_collections
        self.tracker = tracker
        # 层号从 1 开始, 每进入一次递归就加一层
        self.tier = 0
        # 每层已经用到第几组, 用于给新组编号
        self.group_counts = {}
        # 切割期的临时名计数器; 最终名字由 apply_names() 统一分配
        self.temp_counter = 0
        self.temp_padding = 6
        # 本次操作建过的全部组集合, 供收尾时清理空壳
        self.created_collections = []

    @staticmethod
    def group_id(tier, group):
        """集合与碎块名共用的层组标识。层组之间加分隔符, 避免纯数字拼接的歧义。"""
        return "L%d_G%d" % (tier, group)

    def next_group(self, tier):
        """取该层下一个组号, 并把该层的组数推进一位。"""
        group = self.group_counts.get(tier, 0) + 1
        self.group_counts[tier] = group
        return group

    def next_collection(self, tier, group):
        """建立一个组集合; 集合名只用层组号, 不加用户前缀。"""
        if not self.use_collections:
            return None
        coll = track(self.tracker, 'collections',
                     bpy.data.collections.new(self.group_id(tier, group)))
        self.collection.children.link(coll)
        self.created_collections.append(coll)
        self.group_counts[tier] = group
        return coll

    def drop_collection(self, coll, tier):
        """放弃刚建立、最终没有内容的组集合, 并把该层的组号退回去。

        必须同时把引用从 created_collections 里摘掉: 否则收尾清理时会访问
        已经释放的集合, 抛 "StructRNA of type Collection has been removed"。
        """
        if coll is None:
            return
        if coll in self.created_collections:
            self.created_collections.remove(coll)
        self.group_counts[tier] = max(0, self.group_counts.get(tier, 1) - 1)
        if not coll.objects and not coll.children:
            bpy.data.collections.remove(coll)

    def prune_empty_collections(self):
        """收回被递归替换后腾空的组集合, 避免大纲里留下空壳。"""
        for coll in self.created_collections:
            if coll is not None and not coll.objects and not coll.children:
                bpy.data.collections.remove(coll)

    def temp_name(self):
        """切割过程中使用的临时名, 保证同一批里互不重名。

        最终名字一律由 apply_names() 在全部切割结束后统一分配, 原因是:
        切割中途会有胞体被判空而丢弃, 甚至父碎块被递归替换后删除,
        若边切边编号, 这些被丢弃的中间结果会占掉序号, 让最终名字出现断号。
        """
        self.temp_counter += 1
        return "%s%0*d" % (TEMP_NAME_PREFIX, self.temp_padding, self.temp_counter)

    def apply_names(self, cells):
        """全部切割结束后统一命名, 序号只按保留下来的碎块连续编。

        分组时按 (层, 组) 重排, 让每组从 001 起; 不分组时整批连续编号。
        cells 的顺序就是创建顺序, 因此重排是稳定的。

        首次切割的碎块层号是 0 —— 它们不属于任何组 (只是暂时留在顶层集合),
        因此这类碎块一律按整批连续编号, 不会出现 L0G0 这种层组标识。
        """
        if self.use_collections:
            ordered = sorted(range(len(cells)),
                             key=lambda i: (cells[i][_LEVEL_MARK], cells[i][_GROUP_MARK], i))
        else:
            ordered = list(range(len(cells)))
        counters = {}
        for index in ordered:
            cell = cells[index]
            tier, group = cell[_LEVEL_MARK], cell[_GROUP_MARK]
            if self.use_collections and tier > 0:
                key = (tier, group)
                counters[key] = counters.get(key, 0) + 1
                name = "%s_L%dG%d_%0*d" % (self.prefix, tier, group,
                                           self.padding, counters[key])
            else:
                counters[None] = counters.get(None, 0) + 1
                name = "%s_%0*d" % (self.prefix, self.padding, counters[None])
            cell.name = name
            # 网格数据跟随物体名, 便于在大纲与属性面板里对应
            if cell.data is not None:
                cell.data.name = name + "_mesh"

    @staticmethod
    def mark(shard, source_name, tier, group):
        """给碎块打上来源、层号与组号标记, 便于按属性筛选而不依赖名字。"""
        shard[_SOURCE_MARK] = source_name
        shard[_LEVEL_MARK] = tier
        shard[_GROUP_MARK] = group


def redraw():
    """后台模式下没有窗口, 跳过重绘。"""
    if not bpy.app.background:
        bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)


def interior_vgroup_of(obj):
    """按新名优先、旧名兜底的顺序取内部顶点组, 供下游脚本使用。"""
    for name in (INTERIOR_VGROUP_NAME,) + INTERIOR_VGROUP_NAME_LEGACY:
        group = obj.vertex_groups.get(name)
        if group is not None:
            return group
    return None


def remove_generated_object(obj):
    """删除物体, 并回收随之失去用户的网格数据。"""
    mesh = obj.data if obj.type == 'MESH' else None
    bpy.data.objects.remove(obj, do_unlink=True)
    if mesh is not None and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def _points_from_object(context, depsgraph, obj, source):
    """按来源选项收集种子点, 结果统一为世界空间坐标。"""
    points = []

    def from_verts(item):
        # 网格直接取原始顶点; 曲线/曲面/文字/融球取求值后的几何
        if item.type == 'MESH':
            points.extend(item.matrix_world @ v.co for v in item.data.vertices)
        elif item.type in {'CURVE', 'SURFACE', 'FONT', 'META'}:
            evaluated = item.evaluated_get(depsgraph)
            try:
                mesh = evaluated.to_mesh()
                if mesh is not None:
                    points.extend(item.matrix_world @ v.co for v in mesh.vertices)
            finally:
                evaluated.to_mesh_clear()

    def from_particles(item):
        evaluated = item.evaluated_get(depsgraph)
        for system in getattr(evaluated, 'particle_systems', ()):
            points.extend(p.location.copy() for p in system.particles)

    if 'VERT_OWN' in source:
        from_verts(obj)
    if 'VERT_CHILD' in source:
        for child in obj.children:
            from_verts(child)
    if 'PARTICLE_OWN' in source:
        from_particles(obj)
    if 'PARTICLE_CHILD' in source:
        for child in obj.children:
            from_particles(child)
    if 'PENCIL' in source:
        # 注解入口改过名: 4.2 及更早是 grease_pencil, 4.3 起是 annotation
        annotation = getattr(context.scene, 'grease_pencil', None)
        if annotation is None:
            annotation = getattr(context.scene, 'annotation', None)
        if annotation is not None:
            # 只取活动层, 层级属性在不同版本下名字不同, 因此两种都试
            layer = getattr(annotation.layers, 'active', None)
            if layer is None:
                active_name = getattr(annotation.layers, 'active_note', None)
                layer = next((item for item in annotation.layers
                              if getattr(item, 'info', None) == active_name), None)
            frame = getattr(layer, 'active_frame', None)
            if frame is not None:
                points.extend(point.co.copy() for stroke in getattr(frame, 'strokes', ())
                              for point in stroke.points)
    return points


def cell_fracture_objects(context, collection, obj, source=frozenset({'PARTICLE_OWN'}),
                          source_limit=0, source_noise=0.0, clean=True,
                          use_smooth_faces=False, use_data_match=False,
                          use_debug_points=False, margin=0.0, material_index=0,
                          use_debug_redraw=False, cell_scale=(1.0, 1.0, 1.0),
                          tracker=None, naming=None, level=0, group=None, target=None):
    """采样种子点并生成凸胞体, 返回胞体物体列表。

    naming 为 NamingPlan 时: group 是组标识 (层, 组) 或 None (表示不分组),
    target 是这批碎块要落进的组集合 (可能为 None, 表示直接落在输出集合)。
    这两项由调用方决定, 因为"这一批属于哪一组"是流程层面的信息, 这里不去猜。
    naming 为 None 时回落到旧的 "<源物体名>_cell" 命名, 便于单独调用本函数做测试。
    """
    from .qd_fracture_calc import points_as_bmesh_cells

    def shard_name():
        # 切割期只用临时名; 最终名字在全部切完后由 apply_names() 统一分配,
        # 这样被丢弃的中间结果不会占掉序号, 也不会出现断号
        return naming.temp_name() if naming is not None else obj.name + "_cell"

    def shard_collection():
        return target if target is not None else collection

    depsgraph = context.evaluated_depsgraph_get()
    points = _points_from_object(context, depsgraph, obj, source)
    # 选定来源一个点都没有时回退到自身顶点, 避免白跑一趟
    if not points:
        points = _points_from_object(context, depsgraph, obj, {'VERT_OWN'})
    if not points:
        return []
    if source_limit and source_limit < len(points):
        # 先打乱再截断, 得到的是随机子集而不是前 N 个
        random.shuffle(points)
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
        raise ValueError("Source geometry contains non-finite coordinates")
    # 用包围盒对角线作为整体尺度, 焊接与抖动都按它派生
    lo = [min(v[i] for v in verts) for i in range(3)]
    hi = [max(v[i] for v in verts) for i in range(3)]
    scale = Vector(tuple(hi[i] - lo[i] for i in range(3))).length
    weld_dist, jitter_half = _weld_distances(scale)
    if source_noise:
        # 扰动规则点阵, 避免出现过于整齐的切割面
        from mathutils.noise import random_unit_vector
        points = [p + random_unit_vector() * (source_noise * scale * 0.5 * random.random())
                  for p in points]
    if use_debug_points:
        # 诊断几何, 用于查看实际采样到的种子点位置; 跟随碎块一起落点
        mesh = track(tracker, 'meshes', bpy.data.meshes.new("DebugPoints"))
        mesh.from_pydata(points, [], [])
        debug_object = track(tracker, 'objects', bpy.data.objects.new("DebugPoints", mesh))
        shard_collection().objects.link(debug_object)
    cells = points_as_bmesh_cells(verts, points, cell_scale, margin_cell=margin)
    objects = []
    for center, cell_points in cells:
        with mesh_edit() as bm:
            # 轻微抖动有助焊接重合点, 抖动幅度远小于焊接距离
            for point in cell_points:
                point = point + Vector(tuple(random.uniform(-jitter_half, jitter_half) for _ in range(3)))
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
            # 中间网格名与物体名同源但不同名, 避免 Blender 因为名字被物体占用而改成
            # ".001"; 反正最终名字由 apply_names() 统一分配, 这里只需要互不重名
            mesh = track(tracker, 'meshes', bpy.data.meshes.new(shard_name() + "_cell"))
            bm.to_mesh(mesh)
        if use_data_match:
            # 复制材质槽与 UV/颜色层, 让布尔插值能带上外表面数据
            for material in obj.data.materials:
                mesh.materials.append(material)
            for layer in obj.data.uv_layers:
                mesh.uv_layers.new(name=layer.name)
            for attribute in obj.data.color_attributes:
                mesh.color_attributes.new(name=attribute.name, type=attribute.data_type,
                                          domain=attribute.domain)
        cell = track(tracker, 'objects', bpy.data.objects.new(shard_name(), mesh))
        shard_collection().objects.link(cell)
        # 胞体顶点是世界空间坐标, 所以把原点放到种子点位置即可对齐
        cell.location = center
        if naming is not None:
            naming.mark(cell, naming.source_name, level, group)
        objects.append(cell)
        if use_data_match:
            # 保持材质槽的链接方式与来源一致
            for source_slot, target_slot in zip(obj.material_slots, cell.material_slots):
                target_slot.link = source_slot.link
                target_slot.material = source_slot.material
        if use_debug_redraw:
            context.view_layer.update()
            redraw()
    context.view_layer.update()
    return objects


def cell_fracture_boolean(context, collection, obj, objects, use_debug_bool=False,
                          clean=True, use_island_split=False, use_interior_hide=False,
                          use_debug_redraw=False, level=0, remove_doubles=True, tracker=None):
    """把每个胞体与源物体求交, 得到贴合表面的碎块, 返回碎块列表。"""
    # 焊接距离按源物体包围盒对角线派生, 与胞体阶段保持同一套尺度
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    diagonal = Vector(tuple(max(p[i] for p in corners) - min(p[i] for p in corners)
                            for i in range(3))).length
    weld_dist = _weld_distances(diagonal)[0]
    if use_interior_hide and level == 0:
        obj.data.polygons.foreach_set("hide", [False] * len(obj.data.polygons))
        obj.data.update()
    modifiers = {}
    for cell in objects:
        modifier = cell.modifiers.new(name="Boolean", type='BOOLEAN')
        modifier.object = obj
        modifier.operation = 'INTERSECT'
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
            # 求值后的网格就是布尔结果, 直接取出来替换胞体网格并移除修改器。
            # 名字这里不管: 全部切完后 apply_names() 会把物体与网格一起定名
            evaluated = cell.evaluated_get(depsgraph)
            mesh = track(tracker, 'meshes', bpy.data.meshes.new_from_object(
                evaluated, preserve_all_data_layers=True, depsgraph=depsgraph))
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
                        bmesh.ops.dissolve_limit(bm, verts=list(bm.verts), edges=list(bm.edges),
                                                angle_limit=0.001)
                    if remove_doubles:
                        bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=weld_dist)
                    bm.to_mesh(mesh)
        results.append(cell)
        if use_debug_redraw:
            redraw()
    if results and use_island_split and not use_debug_bool:
        # 借助编辑模式的 separate LOOSE 拆分不连通部分, 因此要先手动管理选择集。
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
            cell['_qd_split_owner'] = token
        try:
            with context.temp_override(object=results[0], active_object=results[0],
                                       selected_objects=results, selected_editable_objects=results):
                bpy.ops.mesh.separate(type='LOOSE')
        finally:
            results = [cell for cell in bpy.data.objects if cell.get('_qd_split_owner') == token]
            for cell in results:
                track(tracker, 'objects', cell)
                track(tracker, 'meshes', cell.data)
                del cell['_qd_split_owner']
    context.view_layer.update()
    return results


def cell_fracture_interior_handle(objects, use_interior_vgroup=False,
                                  use_sharp_edges=False, use_sharp_edges_apply=False):
    """全部切割完成后统一处理内部断面: 顶点组、锐边、以及取消隐藏。"""
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
