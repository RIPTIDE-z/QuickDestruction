"""弹道单元测试 - 穿越点配对, 穿透预算与撞击作用的几何关系, 纯计算部分在这里逐条核对

测试目的:
    1. 折线: 累计长度, 按长度取点与方向
    2. 配对: 方向与外法线的点积判断进出, 擦过表面的点被排除, 起点在实体内与终点在实体内都补全
    3. 穿透预算: 厚度乘阻力不超过预算时打穿, 否则停在预算能走到的深度; 恰好打穿算打穿; 停下后不再处理后面的实体
    4. 撞击作用: 入口坑沿镜面反射方向, 通道沿弹道, 出口坑用出口锥角; 到达帧与撞击事件的定时一致
测试过程:
    手工构造穿越点, 直接调用 common.ballistics 的函数, 不需要 Blender
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_ballistics.py
"""

import logging

import pytest

from source.common import ballistics, drivers, impulse
from source.common.ballistics import Crossing

LOG = logging.getLogger(__name__)
DT = 1.0 / 24.0
TIMING = impulse.Timing(frame_start=1, dt=DT)
# 沿 +X 方向的路径, 长 10
PATH = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
FORWARD = (1.0, 0.0, 0.0)
IDENTITY = (1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)


def bullet(**overrides):
    """弹道驱动快照: 起动帧 10, 弹速 24, 可穿透 0.3, 其余取默认"""
    values = {
        "name": "Bullet",
        "role": drivers.PROJECTILE,
        "center": (0.0, 0.0, 0.0),
        "axis": FORWARD,
        "inverse": IDENTITY,
        "frame": 10,
        "speed": 8.0,
        "falloff": "CONSTANT",
        "core_ratio": 0.0,
        "spread": 0.0,
        "lift": 0.0,
        "max_spin": 0.0,
        "wave_speed": 0.0,
        "front_only": False,
        "seed": 0,
        "path": PATH,
        "bullet_speed": 24.0,
        "penetration": 0.3,
    }
    values.update(overrides)
    return drivers.DriverSnapshot(**values)


def enter(arc):
    """路径在 arc 处进入实体: 外法线朝 -X, 与方向相反"""
    return Crossing(arc, (arc, 0.0, 0.0), FORWARD, (-1.0, 0.0, 0.0))


def leave(arc):
    """路径在 arc 处离开实体: 外法线朝 +X"""
    return Crossing(arc, (arc, 0.0, 0.0), FORWARD, (1.0, 0.0, 0.0))


def kinds(flight):
    """撞击作用的种类序列"""
    return [strike.kind for strike in flight.strikes]


def test_path_lengths_and_point_at():
    """累计长度逐段相加; 按长度取点给出该处的方向, 超出范围取端点"""
    path = ((0.0, 0.0, 0.0), (3.0, 0.0, 0.0), (3.0, 4.0, 0.0))
    lengths = ballistics.path_lengths(path)
    assert lengths == pytest.approx([0.0, 3.0, 7.0])
    point, direction = ballistics.point_at(path, lengths, 5.0)
    assert point == pytest.approx((3.0, 2.0, 0.0))
    assert direction == pytest.approx((0.0, 1.0, 0.0))
    assert ballistics.point_at(path, lengths, 99.0)[0] == pytest.approx((3.0, 4.0, 0.0))


def test_pairing_by_facing():
    """方向与外法线点积为负是进入, 为正是离开; 两个实体配成两段"""
    segments = ballistics.solid_segments([enter(1.0), leave(1.2), enter(3.0), leave(3.5)], PATH)
    assert [(a.arc, b.arc) for a, b in segments] == [(1.0, 1.2), (3.0, 3.5)]


def test_grazing_points_are_dropped():
    """路径擦过表面 (方向与法线几乎垂直) 的点不算穿越"""
    graze = Crossing(2.0, (2.0, 0.0, 0.0), FORWARD, (0.0, 1.0, 0.0))
    segments = ballistics.solid_segments([enter(1.0), graze, leave(1.2)], PATH)
    assert [(a.arc, b.arc) for a, b in segments] == [(1.0, 1.2)]


def test_path_starting_inside_gets_an_implicit_entry():
    """路径起点就在实体里: 第一个穿越点是离开, 补一个没有法线的隐含入口, 它不产生入口坑"""
    segments = ballistics.solid_segments([leave(0.4)], PATH)
    entry, out = segments[0]
    assert entry.arc == 0.0
    assert entry.normal is None
    assert out.arc == 0.4
    flight = ballistics.plan_flight(bullet(penetration=1.0), [leave(0.4)], 1.0, PATH)
    assert kinds(flight) == [impulse.STRIKE_CHANNEL, impulse.STRIKE_EXIT]
    # 预算不够打穿这 0.4 的厚度时子弹停在实体里, 只有到停点的通道
    stopped = ballistics.plan_flight(bullet(), [leave(0.4)], 1.0, PATH)
    assert kinds(stopped) == [impulse.STRIKE_CHANNEL]
    assert stopped.stop_arc == pytest.approx(0.3)


def test_path_ending_inside_keeps_the_open_segment():
    """路径在实体里结束: 最后一段没有出口"""
    segments = ballistics.solid_segments([enter(9.5)], PATH)
    assert segments[0][1] is None


def test_thin_slab_is_pierced():
    """厚 0.2 的板, 预算 0.3, 阻力 1: 打穿, 有入口坑, 通道与出口坑, 子弹没有停下"""
    flight = ballistics.plan_flight(bullet(), [enter(1.0), leave(1.2)], 1.0, PATH)
    LOG.info("kinds %s", kinds(flight))
    assert kinds(flight) == [impulse.STRIKE_ENTRY, impulse.STRIKE_CHANNEL, impulse.STRIKE_EXIT]
    assert flight.stop_arc is None
    assert flight.pierced == 1


def test_thick_slab_stops_the_bullet_at_the_budget_depth():
    """厚 0.5 的板, 预算 0.3: 停在入口之后 0.3 处, 只有入口坑与到那里的通道"""
    flight = ballistics.plan_flight(bullet(), [enter(1.0), leave(1.5)], 1.0, PATH)
    assert kinds(flight) == [impulse.STRIKE_ENTRY, impulse.STRIKE_CHANNEL]
    assert flight.stop_arc == pytest.approx(1.3)
    assert flight.strikes[1].end == pytest.approx((1.3, 0.0, 0.0))
    assert flight.pierced == 0


def test_resistance_spends_the_budget_faster():
    """阻力 2 时同样 0.2 的板要 0.4 预算, 超过 0.3: 停在 0.15 深处; 阻力 0.5 时打穿"""
    stopped = ballistics.plan_flight(bullet(), [enter(1.0), leave(1.2)], 2.0, PATH)
    assert stopped.stop_arc == pytest.approx(1.15)
    pierced = ballistics.plan_flight(bullet(), [enter(1.0), leave(1.2)], 0.5, PATH)
    assert pierced.stop_arc is None


def test_exact_budget_pierces():
    """预算恰好等于需要的量算打穿, 浮点误差不能把它判成差一点"""
    flight = ballistics.plan_flight(bullet(penetration=0.2), [enter(1.0), leave(1.2)], 1.0, PATH)
    assert flight.stop_arc is None
    assert flight.pierced == 1


def test_budget_carries_over_and_later_solids_are_skipped_after_a_stop():
    """第一块打穿后预算减少; 第二块不够就停下, 之后的实体不再处理"""
    crossings = [enter(1.0), leave(1.2), enter(2.0), leave(2.3), enter(4.0), leave(4.1)]
    flight = ballistics.plan_flight(bullet(), crossings, 1.0, PATH)
    assert flight.pierced == 1
    assert flight.stop_arc == pytest.approx(2.1)
    entries = [strike.arc for strike in flight.strikes if strike.kind == impulse.STRIKE_ENTRY]
    assert entries == [1.0, 2.0]


def test_reflect_flips_the_normal_component():
    """镜面反射只翻转法向分量: 斜着向下打在水平面上弹向斜上方"""
    reflected = ballistics.reflect((0.6, 0.0, -0.8), (0.0, 0.0, 1.0))
    assert reflected == pytest.approx((0.6, 0.0, 0.8))


def test_directions_and_cones_of_the_three_strikes():
    """入口坑沿反射方向 (这里被板挡着, 朝 -X 抛回), 通道与出口坑沿弹道, 出口用出口锥角"""
    driver = bullet(exit_spread=0.5, spread=0.1)
    flight = ballistics.plan_flight(driver, [enter(1.0), leave(1.2)], 1.0, PATH)
    entry, channel, exit_ = flight.strikes
    assert entry.direction == pytest.approx((-1.0, 0.0, 0.0))
    assert channel.direction == pytest.approx(FORWARD)
    assert exit_.direction == pytest.approx(FORWARD)
    assert entry.spread == 0.1
    assert exit_.spread == 0.5


def test_arrival_frame_matches_the_strike_timing():
    """子弹到达某处的帧与撞击事件的起动帧是同一个换算: 8 米 / 24 米每秒 / (1/24 秒每帧) = 8 帧"""
    driver = bullet()
    assert ballistics.arrival_frame(driver, 8.0, DT) == 18
    strike = impulse.Strike(
        impulse.STRIKE_ENTRY, (8.0, 0, 0), (8.0, 0, 0), 8.0, FORWARD, 1.0, 3.0, 0.0, driver
    )
    shard = impulse.ShardInfo("a/1", (8.0, 0.0, 0.0), 0.0)
    assert impulse.strike_event(strike, shard, TIMING).frame == 18
