"""种子采样 - 从顶点, 粒子系统与注解层收集世界空间种子点, 并提供局部随机流采样"""

from math import cos, sin, sqrt, tau

from mathutils import Vector


def _vertex_points(item, depsgraph):
    """一个物体的世界空间顶点: 网格取原始顶点, 曲线等可转换类型取求值几何"""
    if item.type == "MESH":
        return [item.matrix_world @ v.co for v in item.data.vertices]
    if item.type not in {"CURVE", "SURFACE", "FONT", "META"}:
        return []
    # 这些类型没有可直接读取的顶点, 先求值成临时网格, 用完立即释放
    evaluated = item.evaluated_get(depsgraph)
    try:
        mesh = evaluated.to_mesh()
        return [] if mesh is None else [item.matrix_world @ v.co for v in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


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
