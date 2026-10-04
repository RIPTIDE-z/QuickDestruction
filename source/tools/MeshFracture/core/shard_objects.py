"""生成物体的基本操作 - 实时重绘, 回收被替换的物体, 重设原点"""

import bpy
from mathutils import Matrix, Vector

from ....common.blender_context import vertex_coordinates


def redraw():
    """实时显示进度时立即重绘窗口, 后台模式没有窗口时跳过"""
    if not bpy.app.background:
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def discard_object(tracker, obj):
    """丢弃生成的物体: 取消全部链接, 物体与它的网格登记为待删, 由 tracker.flush 统一批量删除

    tracker 回收账本
    obj 要丢弃的物体
    """
    mesh = obj.data if obj.type == "MESH" else None
    # 先取消全部链接: 登记待删的数据不能还挂在场景里
    for collection in list(obj.users_collection):
        collection.objects.unlink(obj)
    tracker.bury(obj)
    if mesh is not None:
        tracker.bury(mesh)


def recenter(cells):
    """把每个碎块的原点移到顶点平均位置, 即 Blender 重设原点的 MEDIAN 方式, 世界空间几何不动

    cells 碎块物体列表
    """
    for cell in cells:
        mesh = cell.data
        flat = vertex_coordinates(mesh)
        center = Vector((sum(flat[0::3]), sum(flat[1::3]), sum(flat[2::3]))) / len(mesh.vertices)
        # 网格反向平移, 物体矩阵正向平移, 两者抵消后世界坐标不变
        mesh.transform(Matrix.Translation(-center))
        cell.matrix_world = cell.matrix_world @ Matrix.Translation(center)
