"""部件转碎块 - 把模型自带的部件直接当作碎块, 不切割, 输出与切割结果一致的资产

一个网格物体时, 它的每个连通块是一个部件; 选中多个物体时, 每个物体是一个部件
几何原样保留, 不做任何修补: 部件互相重叠时只检测并告警, 由调用方提示用户
碎块与切割产出的碎块同一个样式: 资产清单, 命名与标记, 材质槽与断面材质槽, 断面面属性与断面顶点组
下游的物理, 驱动, 模拟与绑定只认资产清单与碎块标记, 不关心碎块是切出来的还是模型自带的
"""

from dataclasses import dataclass
from time import perf_counter

import bmesh
import bpy

from ....common.created_data import CreatedData, run_all
from ..errors import MeshFractureError
from . import cell_objects, interior, islands, materials, overlap, shard_objects
from .naming import NamingPlan
from .pipeline import check_limit, output_collection

# 只有一个连通块的网格没有部件可言时的错误
SINGLE_PART_MESSAGE = (
    'Mesh "{name}" has only one connected part; use Generate Fragments to cut it, '
    "or select several objects"
)
# 网格里面隐藏标记的内部属性名; 切割产出的碎块没有隐藏的面, 部件转碎块也要清掉
HIDE_ATTRIBUTE = ".hide_poly"
# 重叠检测用的暂存网格名
SCRATCH_NAME = "QDTMP_overlap"


@dataclass
class Part:
    """一个部件: 网格数据在局部坐标里, 世界矩阵单独记"""

    # 网格数据, 局部坐标; 它就是碎块的网格
    mesh: object
    # 世界矩阵
    matrix: object
    # 来源物体, 用来把碎块对回源物体
    owner: object


@dataclass(frozen=True)
class Outcome:
    """一次部件转碎块的结果"""

    # 全部碎块, 源到碎块的映射, 源到资产根集合的映射
    shards: list
    source_to_shards: dict
    source_to_asset: dict
    # 互相重叠的碎块对 [(碎块, 碎块), ...]
    overlaps: tuple
    # 耗时
    seconds: float


def _evaluated_copy(obj, depsgraph, ledger):
    """物体求值后的网格副本, 带修改器的结果, 隐藏的面一律取消隐藏

    直接复制依赖图里已求值的网格, 不再单独取一次求值网格
    obj 源物体
    depsgraph 求值用的依赖图
    ledger 回收账本, 副本登记在里面
    返回 (网格数据, 世界矩阵); 没有面时抛 MeshFractureError
    """
    evaluated = obj.evaluated_get(depsgraph)
    mesh = ledger.track("meshes", bpy.data.meshes.new_from_object(evaluated))
    if not mesh.polygons:
        raise MeshFractureError('Mesh "{name}" has no faces', name=obj.name)
    # 只有存在隐藏标记时才写, 避免给每个碎块凭空多出一个内部属性
    if mesh.attributes.get(HIDE_ATTRIBUTE) is not None:
        mesh.polygons.foreach_set("hide", [False] * len(mesh.polygons))
    return mesh, evaluated.matrix_world.copy()


def _split_loose(whole, matrix, labels, count, owner, batch):
    """把网格按连通块拆成部件, 每个部件只从自己的面建网格

    whole 求值副本, 不改动
    matrix 它的世界矩阵
    labels 每个面所属连通块的序号
    count 连通块数
    owner 来源物体
    batch ShardBatch, 部件网格登记在它的账本里
    返回 Part 列表, 顺序由连通块的最小面下标决定, 可复现
    """
    parts = [None] * count

    def emit(index, bm):
        """把一个连通块的 BMesh 写成部件网格, 材质槽与求值副本一致"""
        # 路径只有一层, 网格直接用最终名字
        mesh = batch.new_mesh(f"{batch.naming.final_name(index + 1)}_mesh")
        bm.to_mesh(mesh)
        for material in whole.materials:
            mesh.materials.append(material)
        parts[index] = Part(mesh, matrix, owner)

    islands.split_by_labels(whole, labels, count, emit)
    return parts


def _merge_slots(mesh, slots, order):
    """把一个部件副本的材质槽并进合成网格的材质列表, 面的材质索引随之改到新的槽号

    mesh 部件网格的副本, 原地修改面的材质索引
    slots 材质 -> 合成网格里的槽号, 原地补充; 空槽用 None 作键
    order 合成网格的材质列表, 原地补充
    """
    mapping = []
    for material in mesh.materials:
        if material not in slots:
            slots[material] = len(order)
            order.append(material)
        mapping.append(slots[material])
    # 槽号没有变化时不必改面的材质索引
    if all(index == slot for index, slot in enumerate(mapping)):
        return
    indices = [0] * len(mesh.polygons)
    mesh.polygons.foreach_get("material_index", indices)
    last = len(mapping) - 1
    mesh.polygons.foreach_set("material_index", [mapping[min(index, last)] for index in indices])


def _proxy_source(parts, tracker, temp):
    """选中多个物体时, 把它们合成一个物体作为资产的源物体: 原物体切换与弹道都要一个完整的源

    直接在一个 BMesh 里合并各部件网格的副本, 不用 join 算子: 算子要建全部副本物体, 刷新场景,
    材质一多合并材质槽的代价还会急剧上升
    合成物体沿用第一个部件的世界矩阵, 材质槽按材质合并
    parts 各部件, 合成时的顺序; 部件网格不改动
    tracker 回收账本, 合成的物体与网格登记在里面
    temp 临时数据的回收账本, 部件网格的副本登记为待删
    返回 合成的源物体, 不在任何集合里, 由调用方链接
    """
    inverse = parts[0].matrix.inverted()
    slots, order = {}, []
    bm = bmesh.new()
    try:
        for part in parts:
            mesh = part.mesh.copy()
            temp.bury(mesh)
            # 换到第一个部件的局部坐标
            mesh.transform(inverse @ part.matrix)
            _merge_slots(mesh, slots, order)
            # 读入一个非空的 BMesh 时是追加几何
            bm.from_mesh(mesh)
        name = f"{parts[0].owner.name}_Whole"
        whole = tracker.track("meshes", bpy.data.meshes.new(name))
        bm.to_mesh(whole)
    finally:
        bm.free()
    for material in order:
        whole.materials.append(material)
    source = tracker.track("objects", bpy.data.objects.new(name, whole))
    source.matrix_world = parts[0].matrix
    return source


def _create_shards(parts, naming, settings):
    """由部件建碎块物体: 补断面材质槽, 带上部件的世界矩阵, 重设原点, 命名与编号, 断面收尾

    parts Part 列表, 网格的材质槽与来源物体一致
    naming NamingPlan
    settings 参数值
    返回 碎块物体列表, 与 parts 同序
    """
    batch = naming.first_batch()
    cells = []
    for number, part in enumerate(parts, 1):
        # 路径只有一层, 创建时就能确定最终名字, 不必先用临时名再改名; 名字跟随部件自己的物体
        name = naming.final_name(number, part.owner.name)
        if part.mesh.name != f"{name}_mesh":
            part.mesh.name = f"{name}_mesh"
        # 断面材质索引指向最后一个槽之后时, 碎块多一个断面材质槽, 与切割一致
        materials.append_interior_slot(
            part.mesh, part.owner, settings.material_index, naming.tracker
        )
        cell = batch.new_object(part.mesh, part.matrix.translation, name)
        cell.matrix_world = part.matrix
        cell_objects.sync_slot_links(part.owner, cell)
        cells.append(cell)
    if settings.use_recenter:
        shard_objects.recenter(cells)
    batch.number(cells)
    interior.finish_original(cells, settings)
    return cells


def run(context, sources, settings):
    """把源物体的部件转成碎块并建资产; 失败时回收本次新建的数据

    context Blender 上下文, 物体模式
    sources 源物体序列, 非网格物体被忽略: 一个物体时按连通块拆, 多个物体时每个物体一个部件
    settings 参数值, 用到断面材质, 断面顶点组, 输出集合, 命名与重设原点
    返回 Outcome
    """
    started = perf_counter()
    meshes = sorted((obj for obj in sources if obj.type == "MESH"), key=lambda obj: obj.name_full)
    if context.mode != "OBJECT" or not meshes:
        raise MeshFractureError("Select editable mesh objects in Object Mode")
    for obj in meshes:
        # 材质槽编号 0 总是合法, 其余要么是已有的槽, 要么是最后一个槽之后那个自动新建的断面材质槽
        materials.check_slot(obj, settings.material_index)
    tracker, temp = CreatedData(), CreatedData()
    try:
        depsgraph = context.evaluated_depsgraph_get()
        if len(meshes) == 1:
            # 先数连通块, 不够或超限时在建任何东西之前报错
            whole, matrix = _evaluated_copy(meshes[0], depsgraph, temp)
            labels, count = islands.face_labels(whole)
            if count < 2:
                raise MeshFractureError(SINGLE_PART_MESSAGE, name=meshes[0].name)
            check_limit(count)
            source = meshes[0]
            parts = None
        else:
            check_limit(len(meshes))
            # 每个物体的求值副本直接成为碎块的网格
            parts = [Part(*_evaluated_copy(obj, depsgraph, tracker), obj) for obj in meshes]
        collection = output_collection(context, settings.collection_name, tracker)
        # 源物体: 一个物体时就是它, 多个物体时合成一个放进资产根集合
        if parts is not None:
            source = _proxy_source(parts, tracker, temp)
        naming = NamingPlan(source, collection, settings, tracker, gap=0.0, hollow=False)
        if parts is not None:
            naming.root.objects.link(source)
            source.hide_render = True
            source.hide_set(True)
        else:
            parts = _split_loose(whole, matrix, labels, count, meshes[0], naming.first_batch())
        # 重叠只检测不修复, 在动场景之前算出来
        scratch = temp.track("meshes", bpy.data.meshes.new(SCRATCH_NAME))
        pairs = overlap.find_overlaps([(part.mesh, part.matrix) for part in parts], scratch)
        cells = _create_shards(parts, naming, settings)
        by_owner = {}
        for part, cell in zip(parts, cells, strict=True):
            by_owner.setdefault(part.owner, []).append(cell)
        source_to_shards = {owner: tuple(found) for owner, found in by_owner.items()}
        source_to_asset = dict.fromkeys(source_to_shards, naming.root)
        overlaps = tuple((cells[first], cells[second]) for first, second in pairs)
    except Exception:
        # 回收已经生成的数据
        run_all((tracker.rollback,))
        raise
    finally:
        # 临时物体, 网格与集合无论成败都回收; 待删的先批量删除
        run_all((temp.flush, temp.rollback))
    return Outcome(cells, source_to_shards, source_to_asset, overlaps, perf_counter() - started)
