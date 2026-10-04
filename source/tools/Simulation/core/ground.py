"""预设地面 - 在资产最低点下方放一块有厚度的静态刚体盒子, 作为模拟的地面

单面平面没有厚度, 碎块在两个子步之间就能越过它; 盒子有厚度, 同样的速度下不容易穿过
地面顶面与资产静止姿态的最低点齐平, 水平方向以资产为中心向外延伸; 一个场景只有一块, 再次添加时重新对齐
地面是环境里的被动刚体, 模拟烘焙会自动让它参与碰撞与受阻判定; 它的变化会让依赖它的模拟成果过期
"""

import traceback
from dataclasses import dataclass

import bpy
from mathutils import Vector

from ....common import asset, rigidbody
from ....common.created_data import CreatedData, WriteLog
from ..constants import GROUND_EXTENT_RATIO, GROUND_MIN_HALF, GROUND_NAME, GROUND_THICKNESS
from ..errors import SimulationError

# 没有碎块可对齐时的错误
NO_SHARDS_MESSAGE = "The selected assets have no shards"

# 盒子八个顶点的符号, 与下面六个面的顶点序号配套
CORNERS = tuple((x, y, z) for z in (-1, 1) for y in (-1, 1) for x in (-1, 1))
FACES = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 2, 6, 4), (1, 5, 7, 3), (0, 4, 5, 1), (2, 3, 7, 6))


@dataclass(frozen=True)
class GroundResult:
    """一次添加地面的结果"""

    # 地面物体
    ground: object
    # 是否本次新建 (否则是重新对齐了已有的地面)
    created: bool
    # 顶面的高度, 世界坐标
    top: float
    # 水平边长
    size: float
    # 厚度
    thickness: float


def _rest_points(roots):
    """全部碎块在静止姿态下的世界顶点; 还没有记录静止姿态时取当前世界矩阵"""
    points = []
    for root in roots:
        for shard in asset.shards_of(root):
            matrix = asset.rest_matrix(shard) or shard.matrix_world
            points.extend(matrix @ vertex.co for vertex in shard.data.vertices)
    return points


def _layout(points, unit):
    """地面的 (中心 x, 中心 y, 顶面高度, 半边长, 厚度)"""
    low = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
    high = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
    extent = max(high[0] - low[0], high[1] - low[1])
    half = max(GROUND_MIN_HALF / unit, GROUND_EXTENT_RATIO * extent)
    return (low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0, low[2], half, GROUND_THICKNESS / unit


def _box_mesh(tracker, half, thickness):
    """原点在盒子中心的网格, 顶点是真实尺寸, 物体缩放保持 1"""
    mesh = tracker.track("meshes", bpy.data.meshes.new(GROUND_NAME))
    scale = (half, half, thickness / 2.0)
    vertices = [
        tuple(sign * size for sign, size in zip(corner, scale, strict=True)) for corner in CORNERS
    ]
    mesh.from_pydata(vertices, [], FACES)
    mesh.update()
    return mesh


def _new_ground(context, world, tracker, mesh):
    """新建地面物体, 放进场景集合与刚体世界, 设为盒体形状的被动刚体"""
    obj = tracker.track("objects", bpy.data.objects.new(GROUND_NAME, mesh))
    context.scene.collection.objects.link(obj)
    rigidbody.link(world, [obj])
    body = obj.rigid_body
    body.type = "PASSIVE"
    body.collision_shape = "BOX"
    rigidbody.mark_ground(obj)
    return obj


def _undo(log, tracker):
    """撤回本次改动并回收新建的数据; 清理失败不覆盖原异常"""
    for step in (log.undo, tracker.rollback):
        try:
            step()
        except Exception:
            traceback.print_exc()


def add_ground(context, roots):
    """在资产下方添加预设地面, 已有时重新对齐; 返回 GroundResult, 失败时撤回本次全部改动"""
    asset.validate(roots)
    rigidbody.check_cache(context.scene)
    context.view_layer.update()
    points = _rest_points(roots)
    if not points:
        raise SimulationError(NO_SHARDS_MESSAGE)
    center_x, center_y, top, half, thickness = _layout(points, rigidbody.unit_scale(context.scene))
    location = (center_x, center_y, top - thickness / 2.0)
    tracker, log = CreatedData(), WriteLog()
    old_mesh = None
    try:
        world, created_world = rigidbody.ensure_world(context, tracker)
        if created_world:
            log.add(lambda: rigidbody.remove_world(context))
        mesh = _box_mesh(tracker, half, thickness)
        ground = rigidbody.ground_of(context.scene)
        created = ground is None
        if created:
            ground = _new_ground(context, world, tracker, mesh)
        else:
            old_mesh = ground.data
            # 不能用 log.set 记网格: 它会把网格当成可复制的值取副本
            log.add(lambda: setattr(ground, "data", old_mesh))
            ground.data = mesh
            rigidbody.link(world, [ground])
        log.set(ground, "location", location)
        log.set(ground, "rotation_euler", (0.0, 0.0, 0.0))
        log.set(ground, "scale", (1.0, 1.0, 1.0))
    except Exception:
        _undo(log, tracker)
        raise
    # 旧网格没有使用者了才删, 提交之后再做, 失败撤回时它还在
    if old_mesh is not None and old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    return GroundResult(ground, created, top, 2.0 * half, thickness)
