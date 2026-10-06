"""预设地面 - 在资产最低点下方放一块有厚度的静态刚体盒子, 作为模拟的地面

单面平面没有厚度, 碎块在两个子步之间就能越过它; 盒子有厚度, 同样的速度下不容易穿过
地面顶面与资产静止姿态的最低点齐平, 水平方向以资产为中心向外延伸
一个场景只有一块, 再次添加时重新对齐
地面是环境里的被动刚体, 模拟烘焙会自动让它参与碰撞与受阻判定; 它的变化会让依赖它的模拟成果过期
"""

from dataclasses import dataclass

import bpy
from mathutils import Vector

from ....common import asset, rigidbody
from ....common.created_data import CreatedData, WriteLog, run_all
from ..constants import GROUND_EXTENT_RATIO, GROUND_MIN_HALF, GROUND_NAME, GROUND_THICKNESS
from ..errors import SimulationError

# 没有碎块可对齐时的错误
NO_SHARDS_MESSAGE = "The selected assets have no shards"

# 盒子八个顶点的符号 (每个分量取 -1 或 1), 与下面六个面的顶点序号配套
CORNERS = tuple((x, y, z) for z in (-1, 1) for y in (-1, 1) for x in (-1, 1))
# 盒子六个面的顶点序号, 绕序保证法线朝外
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
    """全部碎块在静止姿态下的世界顶点; 还没有记录静止姿态时取当前世界矩阵

    roots 资产根集合序列
    返回 世界坐标的点列表
    """
    points = []
    for root in roots:
        for shard in asset.shards_of(root):
            # 应用物理之前还没有静止姿态记录, 退回当前姿态
            matrix = asset.rest_matrix(shard) or shard.matrix_world
            points.extend(matrix @ vertex.co for vertex in shard.data.vertices)
    return points


def _layout(points, unit):
    """求地面的位置与尺寸

    points 资产碎块的世界顶点
    unit 每个 Blender 单位对应的米数, 用来把米制常量换成场景单位
    返回 (中心 x, 中心 y, 顶面高度, 半边长, 厚度)
    """
    low = Vector(tuple(min(point[axis] for point in points) for axis in range(3)))
    high = Vector(tuple(max(point[axis] for point in points) for axis in range(3)))
    # 水平范围取资产最大水平尺寸的若干倍, 且不小于下限: 碎块飞出去也有地方落
    extent = max(high[0] - low[0], high[1] - low[1])
    half = max(GROUND_MIN_HALF / unit, GROUND_EXTENT_RATIO * extent)
    return (low[0] + high[0]) / 2.0, (low[1] + high[1]) / 2.0, low[2], half, GROUND_THICKNESS / unit


def _box_mesh(tracker, half, thickness):
    """生成原点在盒子中心的网格, 顶点是真实尺寸, 物体缩放保持 1

    tracker 创建账本
    half 水平半边长
    thickness 厚度
    返回 网格数据
    """
    mesh = tracker.track("meshes", bpy.data.meshes.new(GROUND_NAME))
    scale = (half, half, thickness / 2.0)
    vertices = [
        tuple(sign * size for sign, size in zip(corner, scale, strict=True)) for corner in CORNERS
    ]
    mesh.from_pydata(vertices, [], FACES)
    mesh.update()
    return mesh


def _new_ground(context, world, tracker, mesh):
    """新建地面物体, 放进场景集合与刚体世界, 设为盒体形状的被动刚体

    context Blender 上下文
    world 刚体世界
    tracker 创建账本
    mesh 地面网格
    返回 地面物体
    """
    obj = tracker.track("objects", bpy.data.objects.new(GROUND_NAME, mesh))
    # 链接进场景集合让它可见, 链接进刚体世界集合让 Blender 自动建立刚体
    context.scene.collection.objects.link(obj)
    rigidbody.link(world, [obj])
    body = obj.rigid_body
    body.type = "PASSIVE"
    body.collision_shape = "BOX"
    rigidbody.mark_ground(obj)
    return obj


def add_ground(context, roots):
    """在资产下方添加预设地面, 已有时重新对齐; 失败时撤回本次全部改动

    context Blender 上下文
    roots 资产根集合序列
    返回 GroundResult
    """
    asset.validate(roots)
    rigidbody.check_cache(context.scene)
    context.view_layer.update()
    points = _rest_points(roots)
    # 资产没有碎块时没有高度可对齐
    if not points:
        raise SimulationError(NO_SHARDS_MESSAGE)
    center_x, center_y, top, half, thickness = _layout(points, rigidbody.unit_scale(context.scene))
    # 盒子中心在顶面之下半个厚度处, 顶面刚好与资产最低点齐平
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
        # 没有地面就新建; 已有则换上新网格重新对齐, 保留用户在刚体上调过的其他设置
        if created:
            ground = _new_ground(context, world, tracker, mesh)
        else:
            old_mesh = ground.data
            log.set(ground, "data", mesh)
            # 地面可能被用户移出过刚体世界, 重新对齐时一并放回
            rigidbody.link(world, [ground])
        log.set(ground, "location", location)
        log.set(ground, "rotation_euler", (0.0, 0.0, 0.0))
        log.set(ground, "scale", (1.0, 1.0, 1.0))
    except Exception:
        # 撤回本次改动并回收新建的数据
        run_all((log.undo, tracker.rollback))
        raise
    # 旧网格没有使用者了才删, 提交之后再做, 失败撤回时它还在
    if old_mesh is not None and old_mesh.users == 0:
        bpy.data.meshes.remove(old_mesh)
    return GroundResult(ground, created, top, 2.0 * half, thickness)
