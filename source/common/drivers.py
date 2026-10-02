"""驱动契约 - 驱动物体的参数规格与读取; 破坏驱动工具负责编辑, 模拟烘焙经这里读取, 两者互不导入

驱动物体就是普通空物体, 参数挂在 Object.qd_driver 上; 作用范围取自物体本身:
空物体的显示尺寸乘以物体变换, 视口里看到的球或箭头就是真实范围, 非等比缩放时是椭球
本模块顶层不导入 bpy 与 mathutils, 参数规格与快照可以在 Blender 之外做单元测试
"""

import hashlib
import math
from dataclasses import astuple, dataclass

from .schema import Schema

# 驱动参数的翻译上下文, 与破坏驱动工具 tool_context("drivers") 的结果相同; common 不能导入 framework, 这里直接写出
CONTEXT = "QuickDestruction.drivers"
# 驱动参数挂在物体上的属性名
ATTRIBUTE = "qd_driver"

# 驱动角色
NONE = "NONE"
EXPLOSION = "EXPLOSION"
PUSH = "PUSH"

# 每种角色在界面上显示的字段, 按用途分组, 组与组之间留空; 与角色无关的字段不显示
# 分组依次是: 时间, 强度与衰减, 方向, 自转与随机
ROLE_FIELDS = {
    EXPLOSION: (
        ("frame", "wave_speed"),
        ("speed", "falloff", "core_ratio"),
        ("spread", "lift"),
        ("max_spin", "seed"),
    ),
    PUSH: (
        ("frame", "wave_speed"),
        ("speed", "falloff", "core_ratio"),
        ("spread", "front_only"),
        ("max_spin", "seed"),
    ),
}

# 驱动参数规格: 字段名 -> (bpy.props 的类型名, 该类型的关键字参数)
DRIVER_SPECS = {
    "role": (
        "EnumProperty",
        {
            "name": "Type",
            "items": (
                (NONE, "None", "Not used as a driver."),
                (EXPLOSION, "Explosion", "Throw shards outward from the center of the sphere."),
                (PUSH, "Push", "Push shards along the arrow of the empty."),
            ),
            "default": NONE,
            "description": "What the driver does to the shards in its range.",
        },
    ),
    "enabled": (
        "BoolProperty",
        {
            "name": "Enabled",
            "description": "Use this driver when simulating; turn it off to keep it in the scene "
            "without effect.",
            "default": True,
        },
    ),
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
    "front_only": (
        "BoolProperty",
        {
            "name": "Front Only",
            "description": "Only push the shards in front of the arrow.",
            "default": False,
        },
    ),
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


def is_driver(obj):
    """物体是否是启用中的驱动"""
    params = getattr(obj, ATTRIBUTE, None)
    return params is not None and params.role != NONE and params.enabled


def driver_objects(scene):
    """场景里启用中的驱动物体, 按名字排序"""
    return sorted((obj for obj in scene.objects if is_driver(obj)), key=lambda obj: obj.name)


def role_of(obj):
    """物体的驱动角色, 不是驱动时为 NONE"""
    params = getattr(obj, ATTRIBUTE, None) if obj is not None else None
    return params.role if params is not None else NONE


def all_drivers(scene):
    """场景里设了角色的全部驱动物体, 含停用的, 按名字排序"""
    return sorted((obj for obj in scene.objects if role_of(obj) != NONE), key=lambda obj: obj.name)


def display_size(obj):
    """作用范围的基准尺寸: 空物体用显示尺寸, 其他类型按 1"""
    return obj.empty_display_size if obj.type == "EMPTY" else 1.0


def range_radius(obj):
    """作用范围在世界坐标里的最大半径, 用于界面显示"""
    return display_size(obj) * max(abs(value) for value in obj.matrix_world.to_scale())


def snapshot(obj):
    """读取驱动物体当前的世界变换与参数, 返回 DriverSnapshot"""
    # 延迟导入: 模块顶层要能在 Blender 之外导入
    from mathutils import Matrix, Vector

    params = getattr(obj, ATTRIBUTE)
    world = obj.matrix_world
    inverse = (world @ Matrix.Scale(display_size(obj), 4)).inverted_safe()
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
    )


def quantize(value):
    """签名里的浮点数: 按 1e-6 的绝对精度取整, 负零当作零

    不用有效数字: 本该为零的量会带着 1e-17 这样的残差, 按有效数字格式化时与 0 不同, 会误报变化
    """
    return f"{round(value, 6) + 0.0:.6f}"


def digest(snapshots):
    """一组驱动快照的摘要: 浮点数先量化, 避免末位误差造成误报的变化"""
    parts = []
    for item in snapshots:
        for value in astuple(item):
            if isinstance(value, float):
                parts.append(quantize(value))
            elif isinstance(value, tuple):
                parts.append(",".join(quantize(number) for number in value))
            else:
                parts.append(str(value))
        parts.append("|")
    return hashlib.sha1("/".join(parts).encode("utf-8")).hexdigest()
