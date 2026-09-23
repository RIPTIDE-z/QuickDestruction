"""种子采样 - 从顶点, 粒子系统与注解层收集世界空间种子点, 并提供局部随机流采样"""

from math import cos, sin, sqrt, tau

from mathutils import Vector


def points_from_object(context, depsgraph, obj, source):
    """按来源选项收集种子点, 结果统一为世界空间坐标"""
    points = []

    def from_verts(item):
        # 网格直接取原始顶点; 曲线/曲面/文字/融球取求值后的几何
        """收集原始网格顶点或可转换物体的求值顶点"""
        if item.type == "MESH":
            points.extend(item.matrix_world @ v.co for v in item.data.vertices)
        elif item.type in {"CURVE", "SURFACE", "FONT", "META"}:
            # 这些类型没有可直接读取的顶点, 必须先求值成临时网格;
            # to_mesh 的结果由 to_mesh_clear 释放, 因此放在 try/finally 里
            evaluated = item.evaluated_get(depsgraph)
            try:
                mesh = evaluated.to_mesh()
                if mesh is not None:
                    points.extend(item.matrix_world @ v.co for v in mesh.vertices)
            finally:
                evaluated.to_mesh_clear()

    def from_particles(item):
        """从求值粒子系统读取粒子的世界空间位置"""
        # 粒子位置已经是世界空间坐标, 不再乘矩阵
        evaluated = item.evaluated_get(depsgraph)
        for system in getattr(evaluated, "particle_systems", ()):
            points.extend(p.location.copy() for p in system.particles)

    # 五个来源互相独立, 勾选多个时结果取并集
    if "VERT_OWN" in source:
        from_verts(obj)
    if "VERT_CHILD" in source:
        # 只取直接子物体, 不递归到孙物体
        for child in obj.children:
            from_verts(child)
    if "PARTICLE_OWN" in source:
        from_particles(obj)
    if "PARTICLE_CHILD" in source:
        for child in obj.children:
            from_particles(child)
    if "PENCIL" in source:
        # 注解入口改过名: 4.2 及更早是 grease_pencil, 4.3 起是 annotation
        annotation = getattr(context.scene, "grease_pencil", None)
        if annotation is None:
            annotation = getattr(context.scene, "annotation", None)
        if annotation is not None:
            # 只取活动层, 层级属性在不同版本下名字不同, 因此两种都试
            layer = getattr(annotation.layers, "active", None)
            if layer is None:
                active_name = getattr(annotation.layers, "active_note", None)
                layer = next(
                    (
                        item
                        for item in annotation.layers
                        if getattr(item, "info", None) == active_name
                    ),
                    None,
                )
            frame = getattr(layer, "active_frame", None)
            if frame is not None:
                points.extend(
                    point.co.copy()
                    for stroke in getattr(frame, "strokes", ())
                    for point in stroke.points
                )
    return points


def random_unit_vector(rng):
    """用调用方的随机流在单位球面均匀采样, 避免 mathutils 的独立全局 RNG"""
    # 先在 [-1, 1] 上取 z, 再在圆周上取角度: 这样得到的是球面上的均匀分布,
    # 而不是把三个均匀分量拼起来(那会在立方体角点处堆积)
    z = rng.uniform(-1.0, 1.0)
    angle = rng.uniform(0.0, tau)
    # max 保证浮点误差下开方不出现负数
    radius = sqrt(max(0.0, 1.0 - z * z))
    return Vector((radius * cos(angle), radius * sin(angle), z))
