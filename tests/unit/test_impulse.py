"""冲量模型与驱动契约单元测试 - 编译出的初始条件决定模拟结果, 纯计算部分在这里逐条核对

测试目的:
    1. 衰减: 核心以内为 1, 各衰减方式在范围边缘的取值, 范围外为 0
    2. 作用范围在驱动的局部空间里判定, 非等比缩放时是椭球
    3. 方向: 爆炸沿径向, 上抛先归一化径向再加竖直分量; 推力沿箭头, 只推前方时跳过后方
    4. 时间: 冲击波起动帧按模拟秒换算后向上取整; 起动帧早于起始帧之后的第一帧时推迟并计数
    5. 激活方式: 底部锚定优先, 同一帧的事件合并, 结果与碎块的处理顺序无关
    6. 驱动契约: 翻译上下文与驱动工具一致, 各角色显示的字段都在规格里, 默认值能通过校验
测试过程:
    手工构造驱动快照与碎块信息, 直接调用 common.impulse 的函数, 不需要 Blender
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_impulse.py
"""

import logging
import math

import pytest

from source.common import drivers, impulse
from source.framework.contract import tool_context

LOG = logging.getLogger(__name__)
# 24 帧每秒, 时间缩放为 1 时每帧的模拟秒
DT = 1.0 / 24.0
TIMING = impulse.Timing(frame_start=1, dt=DT)
FROZEN_RULES = impulse.Rules(activation=impulse.FROZEN, anchor_bottom=False, anchor_height=0.0)


def sphere_inverse(center, radius):
    """中心在 center, 半径 radius 的球形范围, 从世界坐标到作用空间的矩阵, 按行展开"""
    cx, cy, cz = center
    scale = 1.0 / radius
    return (
        scale, 0.0, 0.0, -cx * scale,
        0.0, scale, 0.0, -cy * scale,
        0.0, 0.0, scale, -cz * scale,
        0.0, 0.0, 0.0, 1.0,
    )  # fmt: skip


def driver(**overrides):
    """球心在原点, 半径 4 的爆炸驱动; 关闭随机锥角与自转, 便于核对方向"""
    values = {
        "name": "Explosion",
        "role": drivers.EXPLOSION,
        "center": (0.0, 0.0, 0.0),
        "axis": (0.0, 0.0, 1.0),
        "inverse": sphere_inverse((0.0, 0.0, 0.0), 4.0),
        "frame": 10,
        "speed": 8.0,
        "falloff": "LINEAR",
        "core_ratio": 0.0,
        "spread": 0.0,
        "lift": 0.0,
        "max_spin": 0.0,
        "wave_speed": 0.0,
        "front_only": False,
        "seed": 0,
    }
    values.update(overrides)
    return drivers.DriverSnapshot(**values)


def shard(key, center, lowest=0.0):
    """一块碎块的编译信息"""
    return impulse.ShardInfo(key=key, center=center, lowest=lowest)


def close(a, b, tolerance=1e-9):
    """两个向量逐分量相近"""
    return all(abs(x - y) <= tolerance for x, y in zip(a, b, strict=True))


@pytest.mark.parametrize(
    ("kind", "edge"),
    [("CONSTANT", 1.0), ("LINEAR", 0.0), ("SMOOTH", 0.0), ("INVERSE_SQUARE", 0.04)],
)
def test_falloff_edges(kind, edge):
    """核心以内为 1, 范围边缘按衰减方式取值, 范围外为 0"""
    core = 0.2
    LOG.info("%s at core %.3f edge %.3f", kind, impulse.falloff_factor(0.1, core, kind), edge)
    assert impulse.falloff_factor(0.1, core, kind) == 1.0
    assert impulse.falloff_factor(1.0, core, kind) == pytest.approx(edge)
    assert impulse.falloff_factor(1.01, core, kind) == 0.0


def test_smooth_falloff_is_halfway_in_the_middle():
    """平滑衰减在核心与边缘正中间恰好为一半"""
    assert impulse.falloff_factor(0.6, 0.2, "SMOOTH") == pytest.approx(0.5)


def test_explosion_throws_along_the_radius():
    """爆炸沿径向, 线性衰减下速度按距离递减"""
    event = impulse.driver_event(driver(), shard("a/1", (2.0, 0.0, 0.0)), TIMING)
    LOG.info("event %s", event)
    assert event.frame == 10
    assert close(event.velocity, (4.0, 0.0, 0.0))


def test_lift_tilts_the_unit_radius():
    """上抛先把径向归一化再加竖直分量, 结果与碎块到中心的距离无关"""
    near = impulse.driver_event(
        driver(lift=1.0, falloff="CONSTANT"), shard("a/1", (1.0, 0.0, 0.0)), TIMING
    )
    far = impulse.driver_event(
        driver(lift=1.0, falloff="CONSTANT"), shard("a/2", (3.0, 0.0, 0.0)), TIMING
    )
    expected = (8.0 / math.sqrt(2.0), 0.0, 8.0 / math.sqrt(2.0))
    assert close(near.velocity, expected)
    assert close(far.velocity, expected)


def test_out_of_range_has_no_event():
    """作用范围外的碎块没有事件"""
    assert impulse.driver_event(driver(), shard("a/1", (4.5, 0.0, 0.0)), TIMING) is None


def test_range_is_an_ellipsoid_in_local_space():
    """非等比缩放的范围: x 方向半径 4, z 方向半径 1"""
    inverse = (0.25, 0.0, 0.0, 0.0, 0.0, 0.25, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0)
    squashed = driver(inverse=inverse, falloff="CONSTANT")
    assert impulse.driver_event(squashed, shard("a/1", (3.0, 0.0, 0.0)), TIMING) is not None
    assert impulse.driver_event(squashed, shard("a/2", (0.0, 0.0, 1.5)), TIMING) is None


def test_push_follows_the_arrow_and_skips_the_back():
    """推力沿箭头方向; 只推前方时箭头后方的碎块没有事件"""
    push = driver(role=drivers.PUSH, axis=(0.0, 1.0, 0.0), falloff="CONSTANT", front_only=True)
    ahead = impulse.driver_event(push, shard("a/1", (0.0, 1.0, 0.0)), TIMING)
    behind = impulse.driver_event(push, shard("a/2", (0.0, -1.0, 0.0)), TIMING)
    assert close(ahead.velocity, (0.0, 8.0, 0.0))
    assert behind is None


def test_spread_keeps_the_speed_and_stays_in_the_cone():
    """随机锥角只改方向不改大小, 偏离角不超过锥角"""
    spread = math.radians(20.0)
    for index in range(50):
        event = impulse.driver_event(
            driver(spread=spread, falloff="CONSTANT", seed=index),
            shard("a/1", (2.0, 0.0, 0.0)),
            TIMING,
        )
        assert impulse.length(event.velocity) == pytest.approx(8.0)
        assert math.acos(event.velocity[0] / 8.0) <= spread + 1e-9


def test_wave_delay_rounds_up_in_simulated_time():
    """距离 2, 波速 10, 每帧 1/24 秒: 4.8 帧向上取整为 5 帧; 慢动作下每帧模拟秒减半, 延迟加倍"""
    wave = driver(wave_speed=10.0, falloff="CONSTANT")
    event = impulse.driver_event(wave, shard("a/1", (2.0, 0.0, 0.0)), TIMING)
    slow = impulse.driver_event(wave, shard("a/1", (2.0, 0.0, 0.0)), impulse.Timing(1, DT / 2.0))
    LOG.info("wave frames %d slow %d", event.frame, slow.frame)
    assert event.frame == 15
    assert slow.frame == 20


def test_early_events_are_clamped_and_counted():
    """起动帧早于起始帧之后的第一帧时推迟到那一帧, 并计入推迟数"""
    plan = impulse.compile_plan(
        [shard("a/1", (1.0, 0.0, 0.0))], [driver(frame=0)], FROZEN_RULES, TIMING
    )
    assert plan.shards["a/1"].events[0].frame == 2
    assert plan.clamped == 1


def test_bottom_anchor_wins():
    """开启底部锚定时最低的碎块锚定且没有事件, 高处的碎块按默认方式"""
    rules = impulse.Rules(activation=impulse.FREE, anchor_bottom=True, anchor_height=0.1)
    shards = [shard("a/1", (1.0, 0.0, 0.0), lowest=0.0), shard("a/2", (1.0, 0.0, 1.0), lowest=0.5)]
    plan = impulse.compile_plan(shards, [driver()], rules, TIMING)
    assert plan.shards["a/1"] == impulse.ShardPlan(impulse.ANCHORED, ())
    assert plan.shards["a/2"].mode == impulse.FREE
    assert plan.shards["a/2"].events


def test_same_frame_events_add_up():
    """两个驱动同一帧起动同一块碎块时速度相加, 不同帧时各自保留"""
    first = driver(falloff="CONSTANT")
    second = driver(name="Second", falloff="CONSTANT")
    later = driver(name="Later", falloff="CONSTANT", frame=20)
    plan = impulse.compile_plan(
        [shard("a/1", (1.0, 0.0, 0.0))], [first, second, later], FROZEN_RULES, TIMING
    )
    events = plan.shards["a/1"].events
    assert [event.frame for event in events] == [10, 20]
    assert close(events[0].velocity, (16.0, 0.0, 0.0))


def test_plan_does_not_depend_on_shard_order():
    """随机量只由驱动种子与碎块标识决定, 打乱碎块顺序结果不变"""
    noisy = driver(spread=0.5, max_spin=3.0)
    shards = [shard(f"a/{index}", (index * 0.3, 0.1, 0.0)) for index in range(10)]
    forward = impulse.compile_plan(shards, [noisy], FROZEN_RULES, TIMING)
    backward = impulse.compile_plan(list(reversed(shards)), [noisy], FROZEN_RULES, TIMING)
    assert forward == backward


def test_driver_contract_matches_the_tool():
    """驱动参数的翻译上下文与驱动工具一致, 各角色显示的字段都在规格里, 默认值能通过校验"""
    assert drivers.CONTEXT == tool_context("drivers")
    for groups in drivers.ROLE_FIELDS.values():
        fields = [field for group in groups for field in group]
        assert set(fields) <= set(drivers.DRIVER_SPECS)
        assert len(fields) == len(set(fields))
    assert drivers.DRIVER_SCHEMA.validate(drivers.DRIVER_SCHEMA.defaults())


def test_digest_ignores_float_noise_but_sees_changes():
    """摘要按 6 位有效数字比较: 末位噪声不变, 真正的改动会变"""
    base = drivers.digest([driver()])
    assert drivers.digest([driver(speed=8.0 + 1e-12)]) == base
    assert drivers.digest([driver(speed=9.0)]) != base
