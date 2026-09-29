"""生成物体的基本操作 - 实时重绘, 回收被替换的物体, 重设原点"""

import bpy
from mathutils import Matrix, Vector

from ....common.blender_context import vertex_coordinates


def redraw():
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


def recenter(cells):
    """把每个碎块的原点移到顶点平均位置, 即 Blender 重设原点的 MEDIAN 方式, 世界空间几何不动"""
    for cell in cells:
        mesh = cell.data
        flat = vertex_coordinates(mesh)
        center = Vector((sum(flat[0::3]), sum(flat[1::3]), sum(flat[2::3]))) / len(mesh.vertices)
        # 网格反向平移, 物体矩阵正向平移, 两者抵消后世界坐标不变
        mesh.transform(Matrix.Translation(-center))
        cell.matrix_world = cell.matrix_world @ Matrix.Translation(center)
