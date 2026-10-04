"""驱动契约 - 驱动物体的参数规格与读取; 破坏驱动工具负责编辑, 模拟烘焙经这里读取, 两者互不导入

驱动物体就是普通空物体, 参数挂在 Object.qd_driver 上; 作用范围取自物体本身:
空物体的显示尺寸乘以物体变换, 视口里看到的球或箭头就是真实范围, 非等比缩放时是椭球
本模块顶层不导入 bpy 与 mathutils, 参数规格与快照可以在 Blender 之外做单元测试
"""

import hashlib
import math
from dataclasses import astuple, dataclass
from itertools import pairwise

from .schema import Schema

# 驱动参数的翻译上下文, 与破坏驱动工具 tool_context("drivers") 的结果相同
# common 不能导入 framework, 所以这里直接写出
CONTEXT = "QuickDestruction.drivers"
# 驱动参数挂在物体上的属性名
ATTRIBUTE = "qd_driver"

# 驱动角色
NONE = "NONE"
EXPLOSION = "EXPLOSION"
PUSH = "PUSH"
VORTEX = "VORTEX"
ZONE = "ZONE"
CURVE_FLOW = "CURVE_FLOW"
PROJECTILE = "PROJECTILE"

# 以曲线或箭头射线为路径的角色
PATH_ROLES = (CURVE_FLOW, PROJECTILE)
# 区域对区域内碎块的处理方式
ZONE_ANCHOR = "ANCHOR"
ZONE_FROZEN = "FROZEN"
ZONE_SLEEPING = "SLEEPING"
ZONE_FREE = "FREE"
ZONE_EXCLUDE = "EXCLUDE"
# 箭头射线驱动的射线长度, 世界单位; 弹道求交与飞行动画都只看这一段
RAY_LENGTH = 100.0
# 曲线没有倒角, 管半径也没有设置时的作用管半径, 世界单位
DEFAULT_TUBE_RADIUS = 0.5

# 每种角色在界面上显示的字段, 按用途分组, 组与组之间留空; 与角色无关的字段不显示
# 分组依次是: 时间, 强度与衰减, 方向, 自转与随机; 弹道与区域各有自己的分组
ROLE_FIELDS = {
    EXPLOSION: (
        ("frame", "wave_speed"),
        ("speed", "falloff", "core_ratio", "mass_response"),
        ("spread", "lift"),
        ("max_spin", "seed"),
    ),
    PUSH: (
        ("frame", "wave_speed"),
        ("speed", "falloff", "core_ratio", "mass_response"),
        ("spread", "front_only"),
        ("max_spin", "seed"),
    ),
    VORTEX: (
        ("frame", "wave_speed"),
        ("speed", "falloff", "core_ratio", "mass_response"),
        ("inward", "rise", "spread"),
        ("max_spin", "seed"),
    ),
    CURVE_FLOW: (
        ("frame", "wave_speed"),
        ("speed", "falloff", "core_ratio", "mass_response"),
        ("tube_radius", "attraction", "spread"),
        ("max_spin", "seed"),
    ),
    PROJECTILE: (
        ("frame", "bullet_speed", "penetration"),
        ("entry_radius", "entry_speed"),
        ("channel_radius", "channel_speed"),
        ("exit_radius", "exit_speed", "exit_spread"),
        ("spread", "falloff", "core_ratio", "mass_response"),
        ("max_spin", "seed"),
    ),
    ZONE: (("zone_action",),),
}

# 驱动参数规格: 字段名 -> (bpy.props 的类型名, 该类型的关键字参数)
DRIVER_SPECS = {
    # 驱动角色, NONE 表示这个物体不是驱动
    "role": (
        "EnumProperty",
        {
            "name": "Type",
            "items": (
                (NONE, "None", "Not used as a driver."),
                (EXPLOSION, "Explosion", "Throw shards outward from the center of the sphere."),
                (PUSH, "Push", "Push shards along the arrow of the empty."),
                (
                    VORTEX,
                    "Vortex",
                    "Swirl shards around the axis of the empty, optionally pulling them in "
                    "or lifting them.",
                ),
                (
                    ZONE,
                    "Zone",
                    "Change how the shards inside behave before the simulation starts; it "
                    "gives them no speed.",
                ),
                (
                    CURVE_FLOW,
                    "Curve Flow",
                    "Carry shards near a curve along it, in the direction of the curve.",
                ),
                (
                    PROJECTILE,
                    "Projectile",
                    "A bullet along the arrow or curve that pierces the source solid and "
                    "throws shards at the entry, along the channel and at the exit.",
                ),
            ),
            "default": NONE,
            "description": "What the driver does to the shards in its range.",
        },
    ),
    # 是否参与模拟, 关闭后驱动留在场景里但不起作用
    "enabled": (
        "BoolProperty",
        {
            "name": "Enabled",
            "description": "Use this driver when simulating; turn it off to keep it in the scene "
            "without effect.",
            "default": True,
        },
    ),
    # 起动帧: 范围内的碎块这一帧保持原位, 从下一帧开始运动
    "frame": (
        "IntProperty",
        {
            "name": "Start Frame",
            "description": "Shards in range still hold their pose on this frame and start "
            "moving from the next one.",
            "min": 0,
            "default": 10,
        },
    ),
    # 全强度速度, 每模拟秒
    "speed": (
        "FloatProperty",
        {
            "name": "Speed",
            "description": "Speed given to the shards at full strength, per second of "
            "simulated time.",
            "min": 0.0,
            "soft_max": 50.0,
            "default": 8.0,
            "unit": "VELOCITY",
        },
    ),
    # 强度随距离衰减的方式
    "falloff": (
        "EnumProperty",
        {
            "name": "Falloff",
            "items": (
                ("CONSTANT", "Constant", "The same speed everywhere in range."),
                ("LINEAR", "Linear", "Speed drops evenly to zero at the edge."),
                ("SMOOTH", "Smooth", "Speed eases out to zero at the edge."),
                (
                    "INVERSE_SQUARE",
                    "Inverse Square",
                    "Speed drops quickly with distance, like a real blast.",
                ),
            ),
            "default": "SMOOTH",
            "description": "How the speed drops from the core to the edge of the range.",
        },
    ),
    # 全速核心占范围的比例, 0 到 1
    "core_ratio": (
        "FloatProperty",
        {
            "name": "Full Speed Core",
            "description": "Part of the range around the center where shards get the full "
            "speed; the falloff starts outside it.",
            "min": 0.0,
            "max": 1.0,
            "default": 0.2,
            "subtype": "FACTOR",
        },
    ),
    # 方向随机锥角, 弧度, 默认 15 度
    "spread": (
        "FloatProperty",
        {
            "name": "Spread",
            "description": "Random deviation of each shard's direction, as a cone angle.",
            "min": 0.0,
            "max": math.pi,
            "default": math.radians(15.0),
            "subtype": "ANGLE",
        },
    ),
    # 爆炸的向上偏置, 0 为纯径向
    "lift": (
        "FloatProperty",
        {
            "name": "Lift",
            "description": "Tilt the blast upward; 0 throws shards straight away from the "
            "center, 1 adds as much upward motion as outward motion.",
            "min": 0.0,
            "max": 2.0,
            "default": 0.2,
        },
    ),
    # 全强度时每秒的最大转角, 弧度, 默认 360 度每秒
    "max_spin": (
        "FloatProperty",
        {
            "name": "Max Spin",
            "description": "Largest turn per second given to a shard at full strength, around "
            "a random axis.",
            "min": 0.0,
            "max": 20.0 * math.pi,
            "soft_max": 4.0 * math.pi,
            "default": 2.0 * math.pi,
            "subtype": "ANGLE",
        },
    ),
    # 冲击波速度, 0 表示范围内同时起动
    "wave_speed": (
        "FloatProperty",
        {
            "name": "Wave Speed",
            "description": "Speed of the shock front; farther shards start later. 0 starts every "
            "shard in range at once.",
            "min": 0.0,
            "soft_max": 100.0,
            "default": 0.0,
            "unit": "VELOCITY",
        },
    ),
    # 质量响应指数, 0 表示全部碎块速度相同
    "mass_response": (
        "FloatProperty",
        {
            "name": "Mass Response",
            "description": "Let lighter shards fly faster and heavier ones slower than the "
            "average shard of the asset; 0 gives every shard the same speed.",
            "min": 0.0,
            "max": 2.0,
            "default": 0.0,
            "subtype": "FACTOR",
        },
    ),
    # 旋涡朝轴收拢的速度比例, 负值向外
    "inward": (
        "FloatProperty",
        {
            "name": "Inward Pull",
            "description": "Part of the speed that points toward the axis; negative values "
            "push shards outward.",
            "min": -1.0,
            "max": 1.0,
            "default": 0.0,
            "subtype": "FACTOR",
        },
    ),
    # 旋涡沿轴的附加速度, 每模拟秒
    "rise": (
        "FloatProperty",
        {
            "name": "Rise",
            "description": "Extra speed along the axis at full strength, per second of "
            "simulated time.",
            "min": -50.0,
            "max": 50.0,
            "soft_min": -20.0,
            "soft_max": 20.0,
            "default": 0.0,
            "unit": "VELOCITY",
        },
    ),
    # 曲线方向的作用管半径, 0 表示取曲线的倒角深度
    "tube_radius": (
        "FloatProperty",
        {
            "name": "Tube Radius",
            "description": "Distance from the curve within which shards are carried; 0 uses "
            "the bevel depth of the curve.",
            "min": 0.0,
            "soft_max": 10.0,
            "default": 0.0,
            "subtype": "DISTANCE",
            "unit": "LENGTH",
        },
    ),
    # 曲线方向把碎块拉向曲线的速度比例
    "attraction": (
        "FloatProperty",
        {
            "name": "Attraction",
            "description": "Part of the speed that pulls shards toward the curve.",
            "min": 0.0,
            "max": 1.0,
            "default": 0.0,
            "subtype": "FACTOR",
        },
    ),
    # 区域内碎块的处理方式
    "zone_action": (
        "EnumProperty",
        {
            "name": "Shards in Zone",
            "items": (
                (ZONE_ANCHOR, "Anchor", "Fix the shards in place for the whole simulation."),
                (ZONE_FROZEN, "Frozen", "Hold the shards still until a driver starts them."),
                (ZONE_SLEEPING, "Sleeping", "Let the shards sleep until something hits them."),
                (ZONE_FREE, "Free", "Let the shards fall from the first frame."),
                (
                    ZONE_EXCLUDE,
                    "Exclude",
                    "Leave the shards out of the simulation; they stay where they are and "
                    "nothing collides with them.",
                ),
            ),
            "default": ZONE_ANCHOR,
            "description": "What happens to the shards whose center is inside the zone.",
        },
    ),
    # 弹道的弹速, 决定路径上各处何时被命中
    "bullet_speed": (
        "FloatProperty",
        {
            "name": "Bullet Speed",
            "description": "How fast the bullet travels, per second of simulated time; it "
            "sets when each part of the path is hit.",
            "min": 0.1,
            "soft_max": 1000.0,
            "default": 400.0,
            "unit": "VELOCITY",
        },
    ),
    # 弹道能穿透的混凝土等效厚度
    "penetration": (
        "FloatProperty",
        {
            "name": "Penetration",
            "description": "Thickness of concrete the bullet can pierce; harder materials "
            "stop it sooner.",
            "min": 0.0,
            "soft_max": 5.0,
            "default": 0.3,
            "subtype": "DISTANCE",
            "unit": "LENGTH",
        },
    ),
    # 入口坑的作用半径
    "entry_radius": (
        "FloatProperty",
        {
            "name": "Entry Radius",
            "description": "Shards within this distance of the entry point are thrown back.",
            "min": 0.0,
            "soft_max": 2.0,
            "default": 0.15,
            "subtype": "DISTANCE",
            "unit": "LENGTH",
        },
    ),
    # 通道的作用半径
    "channel_radius": (
        "FloatProperty",
        {
            "name": "Channel Radius",
            "description": "Shards within this distance of the bullet's path inside the "
            "solid are pushed along it.",
            "min": 0.0,
            "soft_max": 1.0,
            "default": 0.05,
            "subtype": "DISTANCE",
            "unit": "LENGTH",
        },
    ),
    # 出口坑的作用半径
    "exit_radius": (
        "FloatProperty",
        {
            "name": "Exit Radius",
            "description": "Shards within this distance of the exit point are thrown out.",
            "min": 0.0,
            "soft_max": 3.0,
            "default": 0.4,
            "subtype": "DISTANCE",
            "unit": "LENGTH",
        },
    ),
    # 入口坑抛回碎块的速度
    "entry_speed": (
        "FloatProperty",
        {
            "name": "Entry Speed",
            "description": "Speed of the shards thrown back at the entry.",
            "min": 0.0,
            "soft_max": 50.0,
            "default": 3.0,
            "unit": "VELOCITY",
        },
    ),
    # 通道推动碎块的速度
    "channel_speed": (
        "FloatProperty",
        {
            "name": "Channel Speed",
            "description": "Speed of the shards pushed along the bullet's path.",
            "min": 0.0,
            "soft_max": 50.0,
            "default": 10.0,
            "unit": "VELOCITY",
        },
    ),
    # 出口坑抛出碎块的速度
    "exit_speed": (
        "FloatProperty",
        {
            "name": "Exit Speed",
            "description": "Speed of the shards thrown out at the exit.",
            "min": 0.0,
            "soft_max": 50.0,
            "default": 15.0,
            "unit": "VELOCITY",
        },
    ),
    # 出口坑的随机锥角, 弧度, 默认 35 度
    "exit_spread": (
        "FloatProperty",
        {
            "name": "Exit Spread",
            "description": "Cone angle of the shards thrown out at the exit.",
            "min": 0.0,
            "max": math.pi,
            "default": math.radians(35.0),
            "subtype": "ANGLE",
        },
    ),
    # 推力是否只推箭头前方的碎块
    "front_only": (
        "BoolProperty",
        {
            "name": "Front Only",
            "description": "Only push the shards in front of the arrow.",
            "default": False,
        },
    ),
    # 随机种子, 不移动驱动也能换一组随机方向与自转
    "seed": (
        "IntProperty",
        {
            "name": "Random Seed",
            "description": "Change the random directions and spins without moving the driver.",
            "min": 0,
            "default": 0,
        },
    ),
}

# 绑定翻译上下文的驱动参数规格; 驱动工具把它挂到 bpy.types.Object 上
DRIVER_SCHEMA = Schema(DRIVER_SPECS, CONTEXT)


@dataclass(frozen=True)
class DriverSnapshot:
    """一个驱动在编译时刻的只读快照 - 只有数字, 字符串与元组, 纯计算模块直接使用"""

    # 物体名, 只用于报告
    name: str
    # 驱动角色
    role: str
    # 世界坐标的中心
    center: tuple
    # 物体局部 Z 轴在世界坐标里的单位向量, 推力沿它的方向
    axis: tuple
    # 从世界坐标到作用空间的 4x4 矩阵, 按行展开; 作用范围内的点变换后到原点的距离不超过 1
    inverse: tuple
    # 起动帧
    frame: int
    # 全强度时的速度, 每模拟秒
    speed: float
    # 衰减方式
    falloff: str
    # 全速核心占作用范围的比例
    core_ratio: float
    # 方向随机锥角, 弧度
    spread: float
    # 向上偏置
    lift: float
    # 全强度时每模拟秒的最大转角, 弧度
    max_spin: float
    # 冲击波速度, 0 表示同时起动
    wave_speed: float
    # 只推前方
    front_only: bool
    # 随机种子
    seed: int
    # 质量响应指数, 0 表示不响应
    mass_response: float = 0.0
    # 旋涡: 朝轴的速度比例, 负值向外
    inward: float = 0.0
    # 旋涡: 沿轴的速度
    rise: float = 0.0
    # 区域: 区域内碎块的处理方式
    zone_action: str = ZONE_ANCHOR
    # 区域: 立方体区域用各轴取最大值判断, 否则是椭球
    box: bool = False
    # 路径角色的世界坐标折线; 曲线取第一条样条, 箭头射线是两个点
    path: tuple = ()
    # 曲线方向: 作用管半径, 世界单位, 已解析好倒角深度与默认值
    tube_radius: float = DEFAULT_TUBE_RADIUS
    # 曲线方向: 朝曲线的速度比例
    attraction: float = 0.0
    # 弹道: 弹速, 每模拟秒
    bullet_speed: float = 400.0
    # 弹道: 混凝土等效的可穿透厚度
    penetration: float = 0.3
    # 弹道: 入口坑的作用半径
    entry_radius: float = 0.15
    # 弹道: 通道的作用半径
    channel_radius: float = 0.05
    # 弹道: 出口坑的作用半径
    exit_radius: float = 0.4
    # 弹道: 入口坑给碎块的速度
    entry_speed: float = 3.0
    # 弹道: 通道给碎块的速度
    channel_speed: float = 10.0
    # 弹道: 出口坑给碎块的速度
    exit_speed: float = 15.0
    # 弹道: 出口锥角, 弧度
    exit_spread: float = math.radians(35.0)


def is_driver(obj):
    """判断物体是否是启用中的驱动

    obj 物体
    返回 是启用中的驱动为真
    """
    params = getattr(obj, ATTRIBUTE, None)
    return params is not None and params.role != NONE and params.enabled


def driver_objects(scene):
    """场景里启用中的驱动物体

    scene 场景
    返回 按名字排序的物体列表, 顺序稳定才能让摘要与随机结果可复现
    """
    return sorted((obj for obj in scene.objects if is_driver(obj)), key=lambda obj: obj.name)


def role_of(obj):
    """物体的驱动角色

    obj 物体, 可以为 None
    返回 角色; 不是驱动时为 NONE
    """
    params = getattr(obj, ATTRIBUTE, None) if obj is not None else None
    return params.role if params is not None else NONE


def all_drivers(scene):
    """场景里设了角色的全部驱动物体, 含停用的

    scene 场景
    返回 按名字排序的物体列表
    """
    return sorted((obj for obj in scene.objects if role_of(obj) != NONE), key=lambda obj: obj.name)


def display_size(obj):
    """作用范围的基准尺寸

    obj 驱动物体
    返回 空物体用显示尺寸, 其他类型按 1
    """
    return obj.empty_display_size if obj.type == "EMPTY" else 1.0


def range_radius(obj):
    """作用范围在世界坐标里的最大半径, 用于界面显示

    obj 驱动物体
    返回 基准尺寸乘最大的缩放分量
    """
    return display_size(obj) * max(abs(value) for value in obj.matrix_world.to_scale())


def curve_path(obj):
    """曲线物体第一条样条的世界坐标折线

    贝塞尔按样条的分辨率逐段求值, 多段线取控制点, NURBS 取控制点连线作近似
    求值后的网格在有倒角时是一根管子, 不能当折线用
    obj 曲线物体
    返回 点的元组, 每个点是世界坐标三元组; 没有样条时为空元组
    """
    # 延迟导入: 模块顶层要能在 Blender 之外导入
    from mathutils import Vector
    from mathutils.geometry import interpolate_bezier

    splines = obj.data.splines
    if not splines:
        return ()
    # 只取第一条样条: 一个驱动只有一条路径
    spline = splines[0]
    world = obj.matrix_world
    if spline.type == "BEZIER":
        knots = list(spline.bezier_points)
        # 闭合曲线补上回到起点的一段
        if spline.use_cyclic_u:
            knots.append(knots[0])
        points = []
        # 每相邻两个结点之间按分辨率求值; 去掉每段的终点, 避免与下一段起点重复
        for first, second in pairwise(knots):
            points += interpolate_bezier(
                first.co, first.handle_right, second.handle_left, second.co, spline.resolution_u + 1
            )[:-1]
        points.append(knots[-1].co.copy())
    else:
        # 多段线与 NURBS: 直接取控制点连线
        points = [Vector(point.co[:3]) for point in spline.points]
        if spline.use_cyclic_u and points:
            points.append(points[0])
    return tuple(tuple(world @ point) for point in points)


def _path_of(obj, params, center, axis):
    """路径角色的折线: 曲线物体取样条, 其余沿箭头方向取一段射线

    obj 驱动物体
    params 驱动参数
    center 驱动中心的世界坐标
    axis 驱动的单位轴向
    返回 点的元组; 不是路径角色时为空元组
    """
    if params.role not in PATH_ROLES:
        return ()
    if obj.type == "CURVE":
        return curve_path(obj)
    # 空物体: 从中心沿箭头方向取固定长度的射线
    reach = tuple(center[index] + axis[index] * RAY_LENGTH for index in range(3))
    return (tuple(center), reach)


def _tube_radius(obj, params):
    """作用管半径: 设置值, 否则曲线的倒角深度乘缩放, 都没有时取默认值

    obj 驱动物体
    params 驱动参数
    返回 管半径, 世界单位
    """
    if params.tube_radius > 0.0:
        return params.tube_radius
    # 没有设置时借用曲线的倒角深度, 视口里看到的管就是作用范围
    if obj.type == "CURVE" and obj.data.bevel_depth > 0.0:
        return obj.data.bevel_depth * max(abs(value) for value in obj.matrix_world.to_scale())
    return DEFAULT_TUBE_RADIUS


def snapshot(obj):
    """读取驱动物体当前的世界变换与参数

    obj 驱动物体
    返回 DriverSnapshot
    """
    # 延迟导入: 模块顶层要能在 Blender 之外导入
    from mathutils import Matrix, Vector

    params = getattr(obj, ATTRIBUTE)
    world = obj.matrix_world
    # 作用空间 = 世界变换再乘显示尺寸; 它的逆把世界点换到单位球坐标
    inverse = (world @ Matrix.Scale(display_size(obj), 4)).inverted_safe()
    # 物体局部 Z 轴的世界方向, 推力与旋涡以它为轴
    axis = (world.to_3x3() @ Vector((0.0, 0.0, 1.0))).normalized()
    return DriverSnapshot(
        name=obj.name,
        role=params.role,
        center=tuple(world.translation),
        axis=tuple(axis),
        inverse=tuple(value for row in inverse for value in row),
        frame=params.frame,
        speed=params.speed,
        falloff=params.falloff,
        core_ratio=params.core_ratio,
        spread=params.spread,
        lift=params.lift,
        max_spin=params.max_spin,
        wave_speed=params.wave_speed,
        front_only=params.front_only,
        seed=params.seed,
        mass_response=params.mass_response,
        inward=params.inward,
        rise=params.rise,
        zone_action=params.zone_action,
        box=obj.type == "EMPTY" and obj.empty_display_type == "CUBE",
        path=_path_of(obj, params, tuple(world.translation), tuple(axis)),
        tube_radius=_tube_radius(obj, params),
        attraction=params.attraction,
        bullet_speed=params.bullet_speed,
        penetration=params.penetration,
        entry_radius=params.entry_radius,
        channel_radius=params.channel_radius,
        exit_radius=params.exit_radius,
        entry_speed=params.entry_speed,
        channel_speed=params.channel_speed,
        exit_speed=params.exit_speed,
        exit_spread=params.exit_spread,
    )


def quantize(value):
    """签名里的浮点数: 按 1e-6 的绝对精度取整, 负零当作零

    不用有效数字: 本该为零的量会带着 1e-17 这样的残差, 按有效数字格式化时与 0 不同, 会误报变化
    value 浮点数
    返回 定点格式的字符串
    """
    # 加 0.0 把 -0.0 归一为 0.0, 否则字符串里会出现 "-0.000000"
    return f"{round(value, 6) + 0.0:.6f}"


def _flat(value):
    """展平嵌套的元组, 路径是点的元组

    value 可能嵌套的元组
    产出 其中全部非元组的元素
    """
    for item in value:
        if isinstance(item, tuple):
            yield from _flat(item)
        else:
            yield item


def digest(snapshots):
    """一组驱动快照的摘要: 浮点数先量化, 避免末位误差造成误报的变化

    snapshots DriverSnapshot 序列, 顺序影响结果
    返回 十六进制摘要字符串
    """
    parts = []
    for item in snapshots:
        for value in astuple(item):
            # 浮点数量化; 元组 (中心, 轴向, 矩阵, 路径) 逐个数量化; 其余按字符串
            if isinstance(value, float):
                parts.append(quantize(value))
            elif isinstance(value, tuple):
                parts.append(",".join(quantize(number) for number in _flat(value)))
            else:
                parts.append(str(value))
        # 驱动之间加分隔符, 避免相邻驱动的字段首尾相接后碰撞
        parts.append("|")
    return hashlib.sha1("/".join(parts).encode("utf-8")).hexdigest()
