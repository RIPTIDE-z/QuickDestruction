"""冲量模型 - 把驱动快照与碎块信息编译成每块碎块的激活方式与起动事件; 纯计算, 不依赖 Blender

坐标都是世界坐标的三元组, 速度单位是每模拟秒; 时间以帧计, dt 是每推进一帧经过的模拟秒
预览与模拟编译共用这里, 所以预览看到的初始条件就是模拟实际使用的初始条件
随机量由驱动的随机种子与碎块标识共同决定, 与碎块的处理顺序无关
"""

import math
import random
from dataclasses import dataclass

# 激活方式: 冻结直到被起动, 休眠, 自由, 锚定
FROZEN = "FROZEN"
SLEEPING = "SLEEPING"
FREE = "FREE"
ANCHORED = "ANCHORED"

# 驱动角色, 与 common.drivers 的取值一致
EXPLOSION = "EXPLOSION"
PUSH = "PUSH"

# 视为零向量的长度
TINY = 1e-12
# 反平方衰减在全速核心为 0 时使用的最小核心比例, 避免中心处无穷大
INVERSE_SQUARE_CORE = 0.05
# 冲击波延迟取整前减去的余量, 避免浮点误差把整数帧算成下一帧
CEIL_SLACK = 1e-9


@dataclass(frozen=True)
class ShardInfo:
    """编译需要的碎块数值"""

    # 碎块标识, 资产内唯一, 也参与随机种子
    key: str
    # 质心的世界坐标
    center: tuple
    # 最低点的世界 Z 坐标, 用于底部锚定
    lowest: float


@dataclass(frozen=True)
class Rules:
    """资产级的激活规则"""

    # 默认激活方式: FROZEN, SLEEPING 或 FREE
    activation: str
    # 是否锚定底部
    anchor_bottom: bool
    # 锚定高度
    anchor_height: float


@dataclass(frozen=True)
class Timing:
    """时间换算"""

    # 模拟起始帧
    frame_start: int
    # 每推进一帧经过的模拟秒
    dt: float


@dataclass(frozen=True)
class Event:
    """一次起动: 碎块在 frame 帧保持当前位姿, 从 frame 到 frame + 1 获得速度, 之后交给模拟"""

    # 起动帧
    frame: int
    # 线速度, 每模拟秒
    velocity: tuple
    # 角速度, 世界坐标的轴角向量, 弧度每模拟秒
    spin: tuple


@dataclass(frozen=True)
class ShardPlan:
    """一块碎块的计划"""

    # 激活方式
    mode: str
    # 起动事件, 按帧排序, 同一帧的事件已经合并
    events: tuple


@dataclass(frozen=True)
class Plan:
    """一个资产的计划"""

    # 碎块标识 -> ShardPlan
    shards: dict
    # 起动帧早于起始帧之后第一帧而被推迟的事件数
    clamped: int


def _add(a, b):
    """向量相加"""
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    """向量相减"""
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(a, factor):
    """向量数乘"""
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def _dot(a, b):
    """点积"""
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    """叉积"""
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a):
    """向量长度"""
    return math.sqrt(_dot(a, a))


def normalize(a):
    """单位向量; 零向量返回 None"""
    size = length(a)
    return _scale(a, 1.0 / size) if size > TINY else None


def transform_point(matrix, point):
    """按行展开的 4x4 矩阵变换一个点"""
    x, y, z = point
    rows = (matrix[0:4], matrix[4:8], matrix[8:12])
    return tuple(row[0] * x + row[1] * y + row[2] * z + row[3] for row in rows)


def falloff_factor(u, core, kind):
    """归一化距离 u 处的强度系数: 核心以内为 1, 之外按衰减方式降到范围边缘"""
    if u > 1.0:
        return 0.0
    if u <= core or core >= 1.0:
        return 1.0
    s = (u - core) / (1.0 - core)
    if kind == "CONSTANT":
        return 1.0
    if kind == "LINEAR":
        return 1.0 - s
    if kind == "SMOOTH":
        return 1.0 - (3.0 * s * s - 2.0 * s * s * s)
    # 反平方: 在核心边缘为 1, 在范围边缘截断
    inner = max(core, INVERSE_SQUARE_CORE)
    return min(1.0, (inner / u) ** 2)


def random_unit(rng):
    """球面上均匀分布的单位向量"""
    z = rng.uniform(-1.0, 1.0)
    angle = rng.uniform(0.0, 2.0 * math.pi)
    radius = math.sqrt(max(0.0, 1.0 - z * z))
    return (radius * math.cos(angle), radius * math.sin(angle), z)


def _basis(direction):
    """与方向垂直的两条单位向量"""
    helper = (1.0, 0.0, 0.0) if abs(direction[0]) < 0.9 else (0.0, 1.0, 0.0)
    first = normalize(_cross(direction, helper))
    return first, _cross(direction, first)


def cone(direction, angle, rng):
    """以 direction 为轴, 半顶角 angle 的锥内均匀取一个方向; angle 为 0 时原样返回"""
    if angle <= 0.0:
        return direction
    cos_tilt = 1.0 - rng.random() * (1.0 - math.cos(angle))
    sin_tilt = math.sqrt(max(0.0, 1.0 - cos_tilt * cos_tilt))
    turn = rng.uniform(0.0, 2.0 * math.pi)
    first, second = _basis(direction)
    side = _add(_scale(first, math.cos(turn)), _scale(second, math.sin(turn)))
    return _add(_scale(direction, cos_tilt), _scale(side, sin_tilt))


def _base_direction(driver, offset, rng):
    """去掉随机锥角之前的方向: 推力沿箭头, 爆炸沿径向再加向上偏置"""
    if driver.role == PUSH:
        return driver.axis
    radial = normalize(offset) or random_unit(rng)
    return normalize(_add(radial, (0.0, 0.0, driver.lift))) or (0.0, 0.0, 1.0)


def driver_event(driver, shard, timing):
    """一个驱动对一块碎块的起动事件; 碎块不在作用范围内或强度为零时返回 None"""
    u = length(transform_point(driver.inverse, shard.center))
    factor = falloff_factor(u, driver.core_ratio, driver.falloff)
    if factor <= 0.0:
        return None
    offset = _sub(shard.center, driver.center)
    if driver.role == PUSH and driver.front_only and _dot(offset, driver.axis) < 0.0:
        return None
    rng = random.Random(f"{driver.seed}:{shard.key}")
    direction = cone(_base_direction(driver, offset, rng), driver.spread, rng)
    velocity = _scale(direction, driver.speed * factor)
    spin = _scale(random_unit(rng), driver.max_spin * factor)
    delay = 0
    if driver.wave_speed > 0.0:
        delay = math.ceil(length(offset) / driver.wave_speed / timing.dt - CEIL_SLACK)
    return Event(driver.frame + max(0, delay), velocity, spin)


def merge_events(events):
    """同一帧的事件速度与角速度相加, 结果按帧排序"""
    merged = {}
    for event in events:
        velocity, spin = merged.get(event.frame, ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
        merged[event.frame] = (_add(velocity, event.velocity), _add(spin, event.spin))
    return tuple(Event(frame, velocity, spin) for frame, (velocity, spin) in sorted(merged.items()))


def resolve_mode(shard, rules, asset_lowest):
    """碎块的激活方式: 底部锚定优先, 否则取资产默认"""
    if rules.anchor_bottom and shard.lowest <= asset_lowest + rules.anchor_height:
        return ANCHORED
    return rules.activation


def compile_plan(shards, drivers, rules, timing):
    """编译一个资产的计划; 起动帧不早于起始帧之后的第一帧, 更早的事件推迟并计数"""
    asset_lowest = min((shard.lowest for shard in shards), default=0.0)
    earliest = timing.frame_start + 1
    plans = {}
    clamped = 0
    for shard in shards:
        mode = resolve_mode(shard, rules, asset_lowest)
        if mode == ANCHORED:
            plans[shard.key] = ShardPlan(ANCHORED, ())
            continue
        events = []
        for driver in drivers:
            event = driver_event(driver, shard, timing)
            if event is None:
                continue
            if event.frame < earliest:
                clamped += 1
                event = Event(earliest, event.velocity, event.spin)
            events.append(event)
        plans[shard.key] = ShardPlan(mode, merge_events(events))
    return Plan(plans, clamped)
