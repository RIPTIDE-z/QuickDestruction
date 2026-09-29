"""胞体物体 - 把每个种子点的胞体顶点表变成凸包网格, 再建成带材质槽与数据层的物体"""

import bmesh
import bpy
from mathutils import Vector

from ....common.blender_context import mesh_edit
from ..constants import WELD_TO_JITTER_RATIO
from . import cleanup, sampling, shard_objects
from .cells import points_as_bmesh_cells


def _create_debug_points(points, batch):
    """建立 DebugPoints 诊断几何, 显示扰动后实际用于切割的种子点"""
    tracker = batch.naming.tracker
    mesh = tracker.track("meshes", bpy.data.meshes.new("DebugPoints"))
    mesh.from_pydata(points, [], [])
    debug_object = tracker.track("objects", bpy.data.objects.new("DebugPoints", mesh))
    # 调试几何放在源物体集合里, 不混进碎块集合
    batch.naming.root.objects.link(debug_object)


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
        cleanup.dissolve_coplanar(bm)
        # 写在基础网格上的着色与材质会经布尔求值带到碎块上
        for face in bm.faces:
            face.smooth = settings.use_smooth_faces
            face.material_index = settings.material_index
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
    """由种子点建凸胞体物体; 随机流在采样之后按种子扰动, 顶点抖动的次序消费"""
    if not points:
        return []
    points = sampling.scatter_points(points, snapshot.diagonal, settings.source_noise, rng)
    weld_dist = snapshot.weld_distance
    jitter_half = weld_dist / WELD_TO_JITTER_RATIO
    if settings.use_debug_points:
        _create_debug_points(points, batch)
    cells = points_as_bmesh_cells(points, settings.cell_scale, settings.margin, snapshot)
    created = []
    for center, cell_points in cells:
        mesh = _convex_cell_mesh(cell_points, weld_dist, jitter_half, settings, batch, rng)
        if mesh is None:
            continue
        _copy_source_layers(mesh, obj.data)
        cell = batch.new_object(mesh, center)
        created.append(cell)
        _sync_slot_links(obj, cell)
        if settings.use_debug_redraw:
            context.view_layer.update()
            shard_objects.redraw()
    context.view_layer.update()
    return created
