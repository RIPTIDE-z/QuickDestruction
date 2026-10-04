"""冲量模型 - 把驱动快照与碎块信息编译成每块碎块的激活方式与起动事件; 纯计算, 不依赖 Blender

坐标都是世界坐标的三元组, 速度单位是每模拟秒; 时间以帧计, dt 是每推进一帧经过的模拟秒
预览与模拟编译共用这里, 所以预览看到的初始条件就是模拟实际使用的初始条件
随机量由驱动的随机种子与碎块标识共同决定, 与碎块的处理顺序无关
"""

import math
import random
import statistics
from dataclasses import dataclass
from itertools import pairwise

# 激活方式: 冻结直到被起动, 休眠, 自由, 锚定, 以及不参与模拟
FROZEN = "FROZEN"
SLEEPING = "SLEEPING"
FREE = "FREE"
ANCHORED = "ANCHORED"
EXCLUDED = "EXCLUDED"

# 驱动角色, 与 common.drivers 的取值一致
EXPLOSION = "EXPLOSION"
PUSH = "PUSH"
VORTEX = "VORTEX"
ZONE = "ZONE"
CURVE_FLOW = "CURVE_FLOW"
PROJECTILE = "PROJECTILE"

# 区域的处理方式, 与 common.drivers 的取值一致
ZONE_ANCHOR = "ANCHOR"
ZONE_EXCLUDE = "EXCLUDE"
# 区域处理方式到激活方式的对应: 只有这三种会覆盖资产默认的激活方式
ZONE_OVERRIDES = {"FROZEN": FROZEN, "SLEEPING": SLEEPING, "FREE": FREE}

# 弹道撞击事件的种类: 入口坑, 通道, 出口坑
STRIKE_ENTRY = "ENTRY"
STRIKE_CHANNEL = "CHANNEL"
STRIKE_EXIT = "EXIT"

# 视为零向量的长度
TINY = 1e-12
# 反平方衰减在全速核心为 0 时使用的最小核心比例, 避免中心处无穷大
INVERSE_SQUARE_CORE = 0.05
# 冲击波延迟取整前减去的余量, 避免浮点误差把整数帧算成下一帧
CEIL_SLACK = 1e-9
# 质量响应系数的取值范围, 极轻或极重的碎块不会被放大成荒谬的速度
MASS_FACTOR_MIN = 0.1
MASS_FACTOR_MAX = 10.0


@dataclass(frozen=True)
class ShardInfo:
    """编译需要的碎块数值"""

    # 碎块标识, 资产内唯一, 也参与随机种子
    key: str
    # 质心的世界坐标
    center: tuple
    # 最低点的世界 Z 坐标, 用于底部锚定
    lowest: float
    # 质量, kg; 0 表示未知, 此时质量响应不起作用
    mass: float = 0.0
    # 碎块最薄的方向上的尺寸, 用于子步建议
    thickness: float = 0.0


@dataclass(frozen=True)
class Rules:
    """资产级的激活规则"""

    # 默认激活方式: FROZEN, SLEEPING 或 FREE
    activation: str
    # 是否锚定底部
    anchor_bottom: bool
    # 锚定高度, 比资产最低点高出这个距离以内的碎块被锚定
    anchor_height: float
    # 相对穿透阻力, 弹道的穿透预算按它折算
    resistance: float = 1.0


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
class Strike:
    """弹道的一次撞击作用: 入口坑与出口坑是球形范围, 通道是沿弹道的圆柱范围"""

    # ENTRY, CHANNEL 或 EXIT
    kind: str
    # 入口点或出口点; 通道时是起点
    start: tuple
    # 通道的终点; 入口与出口时等于 start
    end: tuple
    # start 处沿弹道的累计长度
    arc: float
    # 碎块被推动的轴向: 入口是反射方向, 通道与出口是弹道方向
    direction: tuple
    # 作用半径
    radius: float
    # 碎块得到的速度
    speed: float
    # 方向随机锥角
    spread: float
    # 发出这发子弹的驱动快照, 取起动帧, 弹速, 衰减与随机量
    driver: object


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


def add(a, b):
    """向量相加

    a 三元组
    b 三元组
    返回 a + b
    """
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    """向量相减

    a 三元组
    b 三元组
    返回 a - b
    """
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a, factor):
    """向量数乘

    a 三元组
    factor 标量
    返回 a * factor
    """
    return (a[0] * factor, a[1] * factor, a[2] * factor)


def dot(a, b):
    """点积

    a 三元组
    b 三元组
    返回 a . b
    """
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    """叉积

    a 三元组
    b 三元组
    返回 a x b
    """
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def length(a):
    """向量长度

    a 三元组
    返回 欧氏长度
    """
    return math.sqrt(dot(a, a))


def normalize(a):
    """单位化向量

    a 三元组
    返回 单位向量; 长度接近零时返回 None
    """
    size = length(a)
    return scale(a, 1.0 / size) if size > TINY else None


def transform_point(matrix, point):
    """按行展开的 4x4 矩阵变换一个点, 只用前三行 (仿射变换)

    matrix 按行展开的 16 个数
    point 三元组
    返回 变换后的点
    """
    x, y, z = point
    rows = (matrix[0:4], matrix[4:8], matrix[8:12])
    return tuple(row[0] * x + row[1] * y + row[2] * z + row[3] for row in rows)


def falloff_factor(u, core, kind):
    """归一化距离 u 处的强度系数: 核心以内为 1, 之外按衰减方式降到范围边缘

    u 到作用中心的归一化距离, 范围边缘为 1
    core 全速核心占范围的比例
    kind 衰减方式: CONSTANT, LINEAR, SMOOTH 或 INVERSE_SQUARE
    返回 强度系数, 范围外为 0
    """
    # 范围之外没有作用
    if u > 1.0:
        return 0.0
    # 核心以内全速; 核心占满整个范围时没有衰减区间
    if u <= core or core >= 1.0:
        return 1.0
    # s 是衰减区间内的归一化位置: 核心边缘为 0, 范围边缘为 1
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


def mass_factor(response, mass, reference):
    """质量响应系数: (参考质量 / 质量) 的 response 次方, 限制在合理范围内

    response 质量响应指数, 0 表示所有碎块速度相同
    mass 碎块质量
    reference 参考质量, 资产碎块质量的中位数
    返回 速度的倍数; 不响应或质量未知时为 1
    """
    if response <= 0.0 or mass <= 0.0 or reference <= 0.0:
        return 1.0
    return min(MASS_FACTOR_MAX, max(MASS_FACTOR_MIN, (reference / mass) ** response))


def random_unit(rng):
    """球面上均匀分布的单位向量

    rng random.Random 实例
    返回 单位向量
    """
    # 在球面上, Z 坐标与方位角各自均匀分布就是面积均匀分布
    z = rng.uniform(-1.0, 1.0)
    angle = rng.uniform(0.0, 2.0 * math.pi)
    radius = math.sqrt(max(0.0, 1.0 - z * z))
    return (radius * math.cos(angle), radius * math.sin(angle), z)


def _basis(direction):
    """与方向垂直的两条单位向量

    direction 单位方向
    返回 (第一条, 第二条), 与 direction 两两垂直
    """
    # 选一个与方向不太平行的辅助轴, 避免叉积接近零
    helper = (1.0, 0.0, 0.0) if abs(direction[0]) < 0.9 else (0.0, 1.0, 0.0)
    first = normalize(cross(direction, helper))
    return first, cross(direction, first)


def cone(direction, angle, rng):
    """以 direction 为轴, 半顶角 angle 的锥内均匀取一个方向

    direction 锥的轴, 单位向量
    angle 半顶角, 弧度
    rng random.Random 实例
    返回 单位方向; angle 为 0 时原样返回 direction
    """
    if angle <= 0.0:
        return direction
    # 锥内立体角均匀: 偏转角的余弦在 [cos(angle), 1] 上均匀分布
    cos_tilt = 1.0 - rng.random() * (1.0 - math.cos(angle))
    sin_tilt = math.sqrt(max(0.0, 1.0 - cos_tilt * cos_tilt))
    # 绕轴的方位角均匀分布, 垂直于轴的偏转方向由两条基向量合成
    turn = rng.uniform(0.0, 2.0 * math.pi)
    first, second = _basis(direction)
    side = add(scale(first, math.cos(turn)), scale(second, math.sin(turn)))
    return add(scale(direction, cos_tilt), scale(side, sin_tilt))


def segment_nearest(start, end, point):
    """点到线段的最近点

    start 线段起点
    end 线段终点
    point 要查询的点
    返回 (距离, 最近点, 最近点距起点的长度, 线段单位方向); 线段退化时方向为 None
    """
    edge = sub(end, start)
    size = length(edge)
    # 线段退化成一个点: 最近点就是起点, 没有方向
    if size <= TINY:
        return length(sub(point, start)), start, 0.0, None
    direction = scale(edge, 1.0 / size)
    # 投影到线段所在直线, 再夹到线段范围内
    along = min(size, max(0.0, dot(sub(point, start), direction)))
    closest = add(start, scale(direction, along))
    return length(sub(point, closest)), closest, along, direction


def nearest_on_path(path, point):
    """点到折线的最近点

    path 折线的各顶点
    point 要查询的点
    返回 (距离, 最近点, 该处切线, 最近点的累计长度); 折线没有有效线段时返回 None
    """
    best = None
    walked = 0.0
    for start, end in pairwise(path):
        distance, closest, along, direction = segment_nearest(start, end, point)
        # 退化线段没有方向, 不能作为曲线方向的切线
        if direction is not None and (best is None or distance < best[0]):
            best = (distance, closest, direction, walked + along)
        walked += length(sub(end, start))
    return best


def _vortex_direction(driver, offset, rng):
    """旋涡的方向: 绕轴的切向, 按 inward 混入朝轴的分量

    driver 旋涡驱动快照, 提供轴向, 收拢系数与抬升速度
    offset 碎块相对驱动中心的位置
    rng random.Random 实例, 碎块恰在轴上时用来取随机方向
    返回 (方向, 沿轴的速度向量)
    """
    along = dot(offset, driver.axis)
    radial = normalize(sub(offset, scale(driver.axis, along)))
    if radial is None:
        # 恰在轴上时径向没有意义, 在垂直于轴的平面里随机取一个方向
        first, second = _basis(driver.axis)
        turn = rng.uniform(0.0, 2.0 * math.pi)
        radial = add(scale(first, math.cos(turn)), scale(second, math.sin(turn)))
    tangent = normalize(cross(driver.axis, radial))
    # inward 为正时切向混入朝轴的分量, 为负时向外; 混合后退化为零向量则只取切向
    direction = normalize(sub(tangent, scale(radial, driver.inward))) or tangent
    return direction, scale(driver.axis, driver.rise)


def _base_direction(driver, offset, rng):
    """去掉随机锥角之前的方向与不随锥角偏转的附加速度

    推力沿箭头, 爆炸沿径向再加向上偏置, 旋涡绕轴
    driver 驱动快照
    offset 碎块相对驱动中心的位置
    rng random.Random 实例
    返回 (方向, 附加速度向量)
    """
    if driver.role == PUSH:
        return driver.axis, (0.0, 0.0, 0.0)
    if driver.role == VORTEX:
        return _vortex_direction(driver, offset, rng)
    # 爆炸: 碎块恰在中心时径向没有意义, 随机取一个方向; 向上偏置抵消后退回世界 Z
    radial = normalize(offset) or random_unit(rng)
    return normalize(add(radial, (0.0, 0.0, driver.lift))) or (0.0, 0.0, 1.0), (0.0, 0.0, 0.0)


def _delay(distance, speed, timing):
    """冲击波走过 distance 需要的帧数

    distance 冲击波走过的距离
    speed 冲击波速度, 0 表示同时起动
    timing 时间换算
    返回 延迟的帧数, 向上取整
    """
    if speed <= 0.0:
        return 0
    return max(0, math.ceil(distance / speed / timing.dt - CEIL_SLACK))


def _curve_event(driver, shard, timing, reference):
    """曲线方向对一块碎块的起动事件: 沿最近点的切线, 按距离衰减, 可朝曲线吸附; 冲击波沿曲线推进

    driver 曲线方向驱动快照
    shard 碎块信息
    timing 时间换算
    reference 资产碎块质量的中位数
    返回 Event; 碎块不在管内或强度为零时为 None
    """
    nearest = nearest_on_path(driver.path, shard.center)
    # 曲线没有有效线段时没有方向可言
    if nearest is None:
        return None
    distance, closest, tangent, walked = nearest
    factor = falloff_factor(
        distance / max(driver.tube_radius, TINY), driver.core_ratio, driver.falloff
    )
    if factor <= 0.0:
        return None
    # 吸附: 在切线方向上混入朝曲线的分量, 碎块恰在曲线上时没有朝向可混
    pull = normalize(sub(closest, shard.center))
    base = tangent if pull is None else add(tangent, scale(pull, driver.attraction))
    # 随机流只取决于种子与碎块标识, 与处理顺序无关
    rng = random.Random(f"{driver.seed}:{shard.key}")
    direction = cone(normalize(base) or tangent, driver.spread, rng)
    speed = driver.speed * factor * mass_factor(driver.mass_response, shard.mass, reference)
    spin = scale(random_unit(rng), driver.max_spin * factor)
    return Event(
        driver.frame + _delay(walked, driver.wave_speed, timing), scale(direction, speed), spin
    )


def driver_event(driver, shard, timing, reference=0.0):
    """一个驱动对一块碎块的起动事件

    区域不产生速度; 弹道由 strike_event 处理
    driver 驱动快照
    shard 碎块信息
    timing 时间换算
    reference 资产碎块质量的中位数, 质量响应用
    返回 Event; 碎块不在作用范围内或强度为零时为 None
    """
    if driver.role == CURVE_FLOW:
        return _curve_event(driver, shard, timing, reference)
    # 区域与弹道不产生这种事件
    if driver.role not in (EXPLOSION, PUSH, VORTEX):
        return None
    # 用驱动的逆矩阵把碎块换到驱动的局部空间, 范围就是单位球
    u = length(transform_point(driver.inverse, shard.center))
    factor = falloff_factor(u, driver.core_ratio, driver.falloff)
    if factor <= 0.0:
        return None
    offset = sub(shard.center, driver.center)
    # 推力只推箭头前方时, 后方的碎块不受影响
    if driver.role == PUSH and driver.front_only and dot(offset, driver.axis) < 0.0:
        return None
    rng = random.Random(f"{driver.seed}:{shard.key}")
    base, extra = _base_direction(driver, offset, rng)
    direction = cone(base, driver.spread, rng)
    gain = factor * mass_factor(driver.mass_response, shard.mass, reference)
    velocity = add(scale(direction, driver.speed * gain), scale(extra, gain))
    spin = scale(random_unit(rng), driver.max_spin * factor)
    delay = _delay(length(offset), driver.wave_speed, timing)
    return Event(driver.frame + delay, velocity, spin)


def strike_event(strike, shard, timing, reference=0.0):
    """一次弹道撞击对一块碎块的起动事件

    起动时刻是子弹到达该处的时刻: 入口与出口取撞击点, 通道取碎块在弹道上的投影处
    strike 撞击作用
    shard 碎块信息
    timing 时间换算
    reference 资产碎块质量的中位数, 质量响应用
    返回 Event; 不在作用半径内时为 None
    """
    driver = strike.driver
    # 通道: 距离取到弹道线段的距离, 到达时刻取投影点; 入口与出口: 取到撞击点的距离
    if strike.kind == STRIKE_CHANNEL:
        distance, _closest, along, _direction = segment_nearest(
            strike.start, strike.end, shard.center
        )
        walked = strike.arc + along
    else:
        distance, walked = length(sub(shard.center, strike.start)), strike.arc
    # 半径为零表示这类撞击被关闭
    if strike.radius <= 0.0:
        return None
    factor = falloff_factor(distance / strike.radius, driver.core_ratio, driver.falloff)
    if factor <= 0.0:
        return None
    # 随机种子带上撞击种类与位置: 同一发子弹的三类撞击互不相关
    rng = random.Random(f"{driver.seed}:{strike.kind}:{strike.arc:.4f}:{shard.key}")
    direction = cone(strike.direction, strike.spread, rng)
    speed = strike.speed * factor * mass_factor(driver.mass_response, shard.mass, reference)
    spin = scale(random_unit(rng), driver.max_spin * factor)
    delay = _delay(walked, driver.bullet_speed, timing)
    return Event(driver.frame + delay, scale(direction, speed), spin)


def merge_events(events):
    """同一帧的事件速度与角速度相加, 结果按帧排序

    events Event 序列
    返回 合并后按帧排序的 Event 元组
    """
    merged = {}
    for event in events:
        velocity, spin = merged.get(event.frame, ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0)))
        merged[event.frame] = (add(velocity, event.velocity), add(spin, event.spin))
    return tuple(Event(frame, velocity, spin) for frame, (velocity, spin) in sorted(merged.items()))


def zone_contains(zone, point):
    """判断点是否在区域内: 立方体区域各轴取最大值, 其余是椭球

    zone 区域驱动快照
    point 世界坐标的点
    返回 在区域内为真
    """
    local = transform_point(zone.inverse, point)
    reach = max(abs(value) for value in local) if zone.box else length(local)
    return reach <= 1.0


def resolve_mode(shard, rules, asset_lowest, zones=()):
    """求碎块的激活方式, 优先级从高到低: 区域排除, 区域锚定与底部锚定, 区域的覆盖, 资产默认

    多个区域的覆盖冲突时取名字排序靠后的; 排除与锚定的优先级高于覆盖
    shard 碎块信息
    rules 资产级激活规则
    asset_lowest 资产全部碎块的最低点 Z
    zones 区域驱动快照, 应当已按名字排序
    返回 激活方式
    """
    inside = [zone for zone in zones if zone_contains(zone, shard.center)]
    if any(zone.zone_action == ZONE_EXCLUDE for zone in inside):
        return EXCLUDED
    if any(zone.zone_action == ZONE_ANCHOR for zone in inside):
        return ANCHORED
    # 底部锚定: 碎块最低点离资产最低点不超过锚定高度
    if rules.anchor_bottom and shard.lowest <= asset_lowest + rules.anchor_height:
        return ANCHORED
    overrides = [
        ZONE_OVERRIDES[zone.zone_action] for zone in inside if zone.zone_action in ZONE_OVERRIDES
    ]
    return overrides[-1] if overrides else rules.activation


def reference_mass(shards):
    """资产碎块质量的中位数, 质量响应的参考值

    shards ShardInfo 序列
    返回 中位数; 没有已知质量时为 0
    """
    masses = [shard.mass for shard in shards if shard.mass > 0.0]
    return statistics.median(masses) if masses else 0.0


def compile_plan(shards, drivers, rules, timing, strikes=()):
    """编译一个资产的计划; 起动帧不早于起始帧之后的第一帧, 更早的事件推迟并计数

    shards ShardInfo 序列
    drivers 驱动快照, 其中的区域只参与激活解析
    rules 资产级激活规则
    timing 时间换算
    strikes 弹道对这个资产求出的撞击作用
    返回 Plan
    """
    asset_lowest = min((shard.lowest for shard in shards), default=0.0)
    # 区域按名字排序: 多个覆盖冲突时取靠后的, 结果与驱动的枚举顺序无关
    zones = sorted(
        (driver for driver in drivers if driver.role == ZONE), key=lambda driver: driver.name
    )
    sources = [driver for driver in drivers if driver.role != ZONE]
    reference = reference_mass(shards)
    # 起始帧时碎块处于静止姿态, 事件最早只能在下一帧
    earliest = timing.frame_start + 1
    plans = {}
    clamped = 0
    for shard in shards:
        mode = resolve_mode(shard, rules, asset_lowest, zones)
        # 锚定与排除的碎块不会动, 不必编译事件
        if mode in (ANCHORED, EXCLUDED):
            plans[shard.key] = ShardPlan(mode, ())
            continue
        events = [driver_event(driver, shard, timing, reference) for driver in sources]
        events += [strike_event(strike, shard, timing, reference) for strike in strikes]
        kept = []
        for event in events:
            # None 表示碎块不在这个驱动的范围内
            if event is None:
                continue
            # 太早的事件推迟到最早允许的帧并计数, 报告里提示用户
            if event.frame < earliest:
                clamped += 1
                event = Event(earliest, event.velocity, event.spin)
            kept.append(event)
        plans[shard.key] = ShardPlan(mode, merge_events(kept))
    return Plan(plans, clamped)
