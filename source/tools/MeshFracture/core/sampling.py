"""种子采样 - 从顶点, 粒子系统与注解层收集世界空间种子点, 按上限随机截取, 并给种子加扰动"""

from math import cos, sin, sqrt, tau

from mathutils import Vector

from ....common.blender_context import evaluated_mesh
from ....common.errors import require_finite
from ..errors import NON_FINITE_MESSAGE, MeshFractureError


def _vertex_points(item, depsgraph):
    """一个物体的世界空间顶点: 网格取原始顶点, 曲线等可转换类型取求值几何"""
    if item.type == "MESH":
        return [item.matrix_world @ v.co for v in item.data.vertices]
    if item.type not in {"CURVE", "SURFACE", "FONT", "META"}:
        return []
    # 这些类型没有可直接读取的顶点, 先求值成临时网格, 用完立即释放
    with evaluated_mesh(item, depsgraph) as (_evaluated, mesh):
        return [] if mesh is None else [item.matrix_world @ v.co for v in mesh.vertices]


def _particle_points(item, depsgraph):
    """一个物体全部粒子系统的粒子位置, 已经是世界空间坐标"""
    evaluated = item.evaluated_get(depsgraph)
    return [
        particle.location.copy()
        for system in getattr(evaluated, "particle_systems", ())
        for particle in system.particles
    ]


def _annotation_points(scene):
    """场景活动注解层在活动帧上的笔画点"""
    # 注解入口改过名: 4.2 及更早是 grease_pencil, 4.3 起是 annotation
    annotation = getattr(scene, "grease_pencil", None) or getattr(scene, "annotation", None)
    if annotation is None:
        return []
    # 活动层的访问方式在不同版本下不同, 两种都试
    layer = getattr(annotation.layers, "active", None)
    if layer is None:
        active_name = getattr(annotation.layers, "active_note", None)
        layer = next(
            (item for item in annotation.layers if getattr(item, "info", None) == active_name),
            None,
        )
    frame = getattr(layer, "active_frame", None)
    if frame is None:
        return []
    return [point.co.copy() for stroke in getattr(frame, "strokes", ()) for point in stroke.points]


def points_from_object(context, depsgraph, obj, source):
    """按来源选项收集世界空间种子点; 勾选多个来源时取并集, 顺序固定"""
    points = []
    if "VERT_OWN" in source:
        points.extend(_vertex_points(obj, depsgraph))
    if "VERT_CHILD" in source:
        # 只取直接子物体, 不递归到孙物体
        for child in obj.children:
            points.extend(_vertex_points(child, depsgraph))
    if "PARTICLE_OWN" in source:
        points.extend(_particle_points(obj, depsgraph))
    if "PARTICLE_CHILD" in source:
        for child in obj.children:
            points.extend(_particle_points(child, depsgraph))
    if "PENCIL" in source:
        points.extend(_annotation_points(context.scene))
    return points


def random_unit_vector(rng):
    """用调用方的随机流在单位球面均匀采样, 不使用 mathutils 的全局随机状态"""
    # 先在 [-1, 1] 上取 z, 再在圆周上取角度, 得到球面上的均匀分布
    z = rng.uniform(-1.0, 1.0)
    angle = rng.uniform(0.0, tau)
    # max 保证浮点误差下开方不出现负数
    radius = sqrt(max(0.0, 1.0 - z * z))
    return Vector((radius * cos(angle), radius * sin(angle), z))


def sample_points(context, obj, settings):
    """取来源的全部候选点, 为空则回退自身顶点; 不消耗随机流, 规划递归时也用它估算块数"""
    depsgraph = context.evaluated_depsgraph_get()
    points = points_from_object(context, depsgraph, obj, settings.source)
    if not points:
        points = points_from_object(context, depsgraph, obj, {"VERT_OWN"})
    return points


def seed_points(context, obj, settings, limit, rng):
    """采样种子点: 取候选点, 超过 limit 时随机截断, 精确去重并检查有限性; limit 为 0 表示全部保留"""
    points = sample_points(context, obj, settings)
    if limit and limit < len(points):
        # 先打乱再截断, 得到随机子集而不是前 N 个
        rng.shuffle(points)
        points = points[:limit]
    # 精确去重: 按小数位取整会丢掉小尺寸物体的真实点
    points = list({tuple(point): point for point in points}.values())
    # 快照只检查源物体的顶点, 这里检查粒子, 子物体与注解等来源的种子点
    require_finite((points,), MeshFractureError, NON_FINITE_MESSAGE)
    return points


def scatter_points(points, scale, source_noise, rng):
    """种子扰动: 每个种子点沿随机方向位移, 半径在 0 到上限之间均匀取值; 强度为 0 时不消耗随机流"""
    if not source_noise:
        return points
    return [
        point + random_unit_vector(rng) * (source_noise * scale * 0.5 * rng.random())
        for point in points
    ]
