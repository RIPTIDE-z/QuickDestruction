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


def test_vortex_swirls_around_the_axis():
    """旋涡沿绕轴的切向; 向内收拢按比例混入朝轴的分量; 抬升沿轴加速度"""
    swirl = driver(role=drivers.VORTEX, falloff="CONSTANT")
    event = impulse.driver_event(swirl, shard("a/1", (2.0, 0.0, 0.0)), TIMING)
    assert close(event.velocity, (0.0, 8.0, 0.0))
    pulled = impulse.driver_event(
        driver(role=drivers.VORTEX, falloff="CONSTANT", inward=0.5, rise=3.0),
        shard("a/1", (2.0, 0.0, 0.0)),
        TIMING,
    )
    norm = math.sqrt(1.25)
    assert close(pulled.velocity, (-0.5 / norm * 8.0, 1.0 / norm * 8.0, 3.0))


def test_vortex_on_the_axis_still_moves_in_a_plane():
    """碎块恰在轴上时没有径向, 取垂直于轴的方向, 速度大小不变且没有轴向分量"""
    swirl = driver(role=drivers.VORTEX, falloff="CONSTANT")
    event = impulse.driver_event(swirl, shard("a/1", (0.0, 0.0, 1.0)), TIMING)
    assert impulse.length(event.velocity) == pytest.approx(8.0)
    assert event.velocity[2] == pytest.approx(0.0)


def test_mass_response_makes_light_shards_faster():
    """质量响应: 速度乘 (参考质量 / 质量)^k, 参考质量是资产碎块质量的中位数; k 为 0 时速度相同"""
    light = impulse.ShardInfo("a/1", (1.0, 0.0, 0.0), 0.0, mass=1.0)
    heavy = impulse.ShardInfo("a/2", (0.0, 1.0, 0.0), 0.0, mass=4.0)
    assert impulse.reference_mass([light, heavy]) == pytest.approx(2.5)
    plan = impulse.compile_plan(
        [light, heavy], [driver(falloff="CONSTANT", mass_response=1.0)], FROZEN_RULES, TIMING
    )
    assert impulse.length(plan.shards["a/1"].events[0].velocity) == pytest.approx(20.0)
    assert impulse.length(plan.shards["a/2"].events[0].velocity) == pytest.approx(5.0)
    flat = impulse.compile_plan([light, heavy], [driver(falloff="CONSTANT")], FROZEN_RULES, TIMING)
    assert impulse.length(flat.shards["a/1"].events[0].velocity) == pytest.approx(8.0)
    assert impulse.length(flat.shards["a/2"].events[0].velocity) == pytest.approx(8.0)


def test_mass_factor_is_clamped():
    """极轻或极重的碎块的系数限制在 0.1 到 10 之间, 质量未知时为 1"""
    assert impulse.mass_factor(2.0, 0.001, 1.0) == impulse.MASS_FACTOR_MAX
    assert impulse.mass_factor(2.0, 1000.0, 1.0) == impulse.MASS_FACTOR_MIN
    assert impulse.mass_factor(1.0, 0.0, 1.0) == 1.0


def zone(action, name="Zone", box=True, radius=2.0, center=(0.0, 0.0, 0.0)):
    """一个区域驱动: 默认是半边长 2 的立方体"""
    return driver(
        name=name,
        role=drivers.ZONE,
        zone_action=action,
        box=box,
        center=center,
        inverse=sphere_inverse(center, radius),
    )


def mode_of(zones, center=(1.0, 1.0, 0.0), rules=FROZEN_RULES, lowest=0.0):
    """一块碎块在这些区域下的激活方式"""
    plan = impulse.compile_plan([shard("a/1", center, lowest)], zones, rules, TIMING)
    return plan.shards["a/1"].mode


def test_zone_shape_box_or_ellipsoid():
    """立方体区域按各轴最大值判断, 其余是椭球: 角上的点只在立方体里"""
    corner = (1.9, 1.9, 0.0)
    assert mode_of([zone("EXCLUDE", box=True)], corner) == impulse.EXCLUDED
    assert mode_of([zone("EXCLUDE", box=False)], corner) == impulse.FROZEN


def test_zone_priority():
    """区域优先级: 排除 > 锚定 > 覆盖 > 默认; 覆盖冲突时取名字靠后的; 区域外不受影响"""
    assert mode_of([zone("EXCLUDE"), zone("ANCHOR", "B")]) == impulse.EXCLUDED
    assert mode_of([zone("FREE"), zone("ANCHOR", "B")]) == impulse.ANCHORED
    assert mode_of([zone("FREE", "A"), zone("SLEEPING", "B")]) == impulse.SLEEPING
    assert mode_of([zone("SLEEPING", "B"), zone("FREE", "A")]) == impulse.SLEEPING
    assert mode_of([zone("FREE")], (5.0, 0.0, 0.0)) == impulse.FROZEN
    bottom = impulse.Rules(impulse.FREE, True, 0.1)
    assert mode_of([zone("SLEEPING")], rules=bottom) == impulse.ANCHORED


def test_zone_gives_no_speed_and_excluded_shards_have_no_events():
    """区域不产生速度; 被排除的碎块没有事件; 区域里被覆盖为休眠的碎块仍受其他驱动推动"""
    shards = [shard("a/1", (1.0, 0.0, 0.0))]
    plan = impulse.compile_plan(shards, [zone("EXCLUDE")], FROZEN_RULES, TIMING)
    assert plan.shards["a/1"] == impulse.ShardPlan(impulse.EXCLUDED, ())
    plan = impulse.compile_plan(shards, [zone("SLEEPING"), driver()], FROZEN_RULES, TIMING)
    assert plan.shards["a/1"].mode == impulse.SLEEPING
    assert plan.shards["a/1"].events


def curve(**overrides):
    """沿 X 轴从 0 到 10 的曲线方向驱动: 管半径 2, 速度 8, 不衰减"""
    values = {
        "role": drivers.CURVE_FLOW,
        "path": ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
        "tube_radius": 2.0,
        "falloff": "CONSTANT",
    }
    values.update(overrides)
    return driver(**values)


def test_curve_flow_follows_the_tangent():
    """曲线方向: 速度沿最近点的切线; 管半径之外没有事件; 吸附把方向朝曲线偏"""
    near = shard("a/1", (5.0, 1.0, 0.0))
    event = impulse.driver_event(curve(), near, TIMING)
    assert close(event.velocity, (8.0, 0.0, 0.0))
    assert impulse.driver_event(curve(), shard("a/2", (5.0, 3.0, 0.0)), TIMING) is None
    pulled = impulse.driver_event(curve(attraction=1.0), near, TIMING)
    assert close(pulled.velocity, (8.0 / math.sqrt(2.0), -8.0 / math.sqrt(2.0), 0.0))


def test_curve_flow_wave_runs_along_the_curve():
    """冲击波沿曲线推进: 起动延迟按碎块最近点的累计长度算, 5 米 / 12 米每秒 / (1/24 秒每帧) = 10 帧"""
    wave = curve(wave_speed=12.0)
    event = impulse.driver_event(wave, shard("a/1", (5.0, 1.0, 0.0)), TIMING)
    assert event.frame == 20


def test_nearest_on_path_skips_degenerate_segments():
    """折线里长度为零的线段不提供切线; 全部退化时没有最近点"""
    path = ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (4.0, 0.0, 0.0))
    distance, closest, tangent, walked = impulse.nearest_on_path(path, (2.0, 1.0, 0.0))
    assert distance == pytest.approx(1.0)
    assert close(closest, (2.0, 0.0, 0.0))
    assert close(tangent, (1.0, 0.0, 0.0))
    assert walked == pytest.approx(2.0)
    assert impulse.nearest_on_path(((1.0, 1.0, 1.0), (1.0, 1.0, 1.0)), (0.0, 0.0, 0.0)) is None


def projectile():
    """弹道驱动: 起动帧 10, 弹速 24 每秒, 不衰减"""
    return driver(
        name="Bullet", role=drivers.PROJECTILE, frame=10, bullet_speed=24.0, falloff="CONSTANT"
    )


def pit(bullet):
    """原点处朝上的入口坑: 半径 1, 速度 5"""
    return impulse.Strike(
        impulse.STRIKE_ENTRY, (0, 0, 0), (0, 0, 0), 0.0, (0.0, 0.0, 1.0), 1.0, 5.0, 0.0, bullet
    )


def test_strike_pits_and_channel_time_by_arrival():
    """入口坑按撞击点的累计长度定时, 通道按碎块在弹道上的投影定时: 8 米 / 24 米每秒 = 8 帧"""
    bullet = projectile()
    event = impulse.strike_event(pit(bullet), shard("a/1", (0.0, 0.0, 0.5)), TIMING)
    assert event.frame == 10
    assert close(event.velocity, (0.0, 0.0, 5.0))
    channel = impulse.Strike(
        impulse.STRIKE_CHANNEL, (0, 0, 0), (10, 0, 0), 2.0, (1.0, 0.0, 0.0), 0.5, 10.0, 0.0, bullet
    )
    event = impulse.strike_event(channel, shard("a/2", (6.0, 0.2, 0.0)), TIMING)
    assert event.frame == 18
    assert close(event.velocity, (10.0, 0.0, 0.0))
    assert impulse.strike_event(channel, shard("a/3", (6.0, 1.0, 0.0)), TIMING) is None


def test_strikes_join_the_plan_and_merge_with_drivers():
    """撞击作用进入计划; 弹道驱动本身不产生速度; 与普通驱动同一帧时速度相加 (入口坑 5 + 爆炸 8)"""
    bullet = projectile()
    plan = impulse.compile_plan(
        [shard("a/1", (0.0, 0.0, 0.5))],
        [bullet, driver(frame=10, falloff="CONSTANT")],
        FROZEN_RULES,
        TIMING,
        [pit(bullet)],
    )
    events = plan.shards["a/1"].events
    assert len(events) == 1
    assert events[0].velocity[2] == pytest.approx(13.0)


def test_digest_sees_path_changes():
    """路径是快照的一部分: 曲线的点动了, 摘要就变; 同样的快照摘要相同"""
    moved = curve(path=((0.0, 0.0, 0.0), (10.0, 1.0, 0.0)))
    assert drivers.digest([curve()]) != drivers.digest([moved])
    assert drivers.digest([curve()]) == drivers.digest([curve()])


def test_new_roles_show_their_fields():
    """新增的四种角色都有字段分组, 弹道与区域的字段都在规格里"""
    for role in (drivers.VORTEX, drivers.ZONE, drivers.CURVE_FLOW, drivers.PROJECTILE):
        assert role in drivers.ROLE_FIELDS
    assert "zone_action" in drivers.ROLE_FIELDS[drivers.ZONE][0]
    assert "bullet_speed" in drivers.ROLE_FIELDS[drivers.PROJECTILE][0]
