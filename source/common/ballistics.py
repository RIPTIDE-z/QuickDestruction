"""弹道 - 把路径与源物体求得的穿越点编译成入口坑, 通道与出口坑的撞击作用; 纯计算, 不依赖 Blender

求交由调用方完成, 这里只处理几何关系: 穿越点按沿路径的累计长度排序, 每个点带有路径在该处的方向与表面外法线
穿越点按"进入, 离开"配对成实心段; 每一段按穿透预算判断是否打穿
穿透预算以混凝土的厚度计: 打穿一段需要的预算是它的厚度乘资产的相对穿透阻力, 金属等硬材料消耗得更快
"""

import math
from dataclasses import dataclass
from itertools import pairwise

from . import impulse
from .impulse import STRIKE_CHANNEL, STRIKE_ENTRY, STRIKE_EXIT, Strike

# 路径与表面的夹角过小时视为擦过, 不算穿越: 方向与外法线点积的绝对值小于它
TANGENT_EPSILON = 1e-3
# 预算与厚度比较时的容差, 恰好打穿的情形不被浮点误差判成差一点
BUDGET_EPSILON = 1e-9


@dataclass(frozen=True)
class Crossing:
    """路径穿过源物体表面的一个点"""

    # 沿路径的累计长度
    arc: float
    # 世界坐标
    point: tuple
    # 路径在该处的单位方向
    direction: tuple
    # 表面外法线; 路径起点就在实体内部时的隐含入口没有法线
    normal: tuple | None


@dataclass(frozen=True)
class Flight:
    """一发子弹对一个资产的计划"""

    # 撞击作用, 已按沿路径的顺序排列
    strikes: tuple
    # 子弹停下处的累计长度; 一路飞完时为 None
    stop_arc: float | None
    # 打穿的实心段数
    pierced: int


def path_lengths(path):
    """折线各顶点的累计长度, 第一个顶点为 0"""
    lengths = [0.0]
    for start, end in pairwise(path):
        lengths.append(lengths[-1] + impulse.length(impulse.sub(end, start)))
    return lengths


def point_at(path, lengths, arc):
    """折线上累计长度为 arc 处的点与该处的单位方向; arc 超出范围时取端点"""
    arc = min(max(arc, 0.0), lengths[-1])
    for index in range(len(path) - 1):
        if arc <= lengths[index + 1] or index == len(path) - 2:
            size = lengths[index + 1] - lengths[index]
            along = 0.0 if size <= impulse.TINY else (arc - lengths[index]) / size
            edge = impulse.sub(path[index + 1], path[index])
            point = impulse.add(path[index], impulse.scale(edge, along))
            return point, impulse.normalize(edge) or (0.0, 0.0, 1.0)
    return path[0], (0.0, 0.0, 1.0)


def reflect(direction, normal):
    """方向关于表面的镜面反射: d - 2 (d . n) n"""
    return impulse.sub(direction, impulse.scale(normal, 2.0 * impulse.dot(direction, normal)))


def solid_segments(crossings, path):
    """把穿越点配成实心段, 返回 [(入口, 出口)]; 出口为 None 表示路径在实体内结束

    用方向与外法线的点积判断进出: 小于零是进入, 大于零是离开; 擦过表面的点被排除
    路径起点就在实体内部时, 第一段补一个没有法线的隐含入口
    """
    segments = []
    entry = None
    first = True
    for crossing in crossings:
        facing = impulse.dot(crossing.direction, crossing.normal)
        if abs(facing) < TANGENT_EPSILON:
            continue
        if facing < 0.0:
            # 已经在实体内又遇到进入的点, 说明网格不封闭或自相交, 忽略它
            entry = entry or crossing
        elif entry is not None:
            segments.append((entry, crossing))
            entry = None
        elif first:
            start = Crossing(0.0, path[0], crossing.direction, None)
            segments.append((start, crossing))
        first = False
    if entry is not None:
        segments.append((entry, None))
    return segments


def _strikes(driver, entry, end_point, end_arc, direction, exit_crossing):
    """一个实心段产生的撞击作用: 入口坑, 通道, 以及打穿时的出口坑"""
    strikes = []
    if entry.normal is not None:
        strikes.append(
            Strike(
                STRIKE_ENTRY,
                entry.point,
                entry.point,
                entry.arc,
                reflect(direction, entry.normal),
                driver.entry_radius,
                driver.entry_speed,
                driver.spread,
                driver,
            )
        )
    if end_arc > entry.arc:
        strikes.append(
            Strike(
                STRIKE_CHANNEL,
                entry.point,
                end_point,
                entry.arc,
                direction,
                driver.channel_radius,
                driver.channel_speed,
                driver.spread,
                driver,
            )
        )
    if exit_crossing is not None:
        strikes.append(
            Strike(
                STRIKE_EXIT,
                exit_crossing.point,
                exit_crossing.point,
                exit_crossing.arc,
                direction,
                driver.exit_radius,
                driver.exit_speed,
                driver.exit_spread,
                driver,
            )
        )
    return strikes


def plan_flight(driver, crossings, resistance, path):
    """一发子弹的计划: 逐个实心段按穿透预算判断打穿或停下

    打穿一段消耗的预算是它的厚度乘 resistance; 预算不够时子弹停在预算能走到的深度, 只留入口坑与到那里的通道
    子弹停下后不再处理后面的实心段
    """
    budget = driver.penetration
    total = path_lengths(path)[-1]
    strikes = []
    pierced = 0
    for entry, leave in solid_segments(crossings, path):
        exit_arc = total if leave is None else leave.arc
        thickness = max(0.0, exit_arc - entry.arc)
        need = thickness * resistance
        direction = entry.direction
        if leave is not None and budget + BUDGET_EPSILON >= need:
            strikes += _strikes(driver, entry, leave.point, leave.arc, direction, leave)
            budget -= need
            pierced += 1
            continue
        depth = min(thickness, budget / max(resistance, impulse.TINY))
        stop_arc = entry.arc + depth
        end_point = impulse.add(entry.point, impulse.scale(direction, depth))
        strikes += _strikes(driver, entry, end_point, stop_arc, direction, None)
        return Flight(tuple(strikes), stop_arc, pierced)
    return Flight(tuple(strikes), None, pierced)


def arrival_frame(driver, arc, dt):
    """子弹到达累计长度 arc 处的帧, 与撞击事件的时刻同一个换算"""
    return driver.frame + max(0, math.ceil(arc / driver.bullet_speed / dt - impulse.CEIL_SLACK))
