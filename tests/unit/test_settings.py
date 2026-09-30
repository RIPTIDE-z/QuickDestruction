"""MeshFracture 参数语义单元测试

测试目的:
    参数的生效条件, 首次切割的种子上限与取值范围决定了流水线与界面的行为, 都是纯逻辑,
    需要在不启动 Blender 的情况下逐条确认, 界面置灰与流水线读的是同一批函数
测试过程:
    用 SimpleNamespace 充当参数值, 枚举全部布尔组合检查生效条件; 列表检查首次切割种子数;
    用规格校验检查关键参数的边界值
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_settings.py
"""

import itertools
import logging
from types import SimpleNamespace

import pytest

from source.common.errors import InvalidParameterError
from source.tools.MeshFracture import settings
from source.tools.MeshFracture.constants import MAX_ROUNDS, SHARD_LIMIT

LOG = logging.getLogger(__name__)


def values(**overrides):
    """带默认值的参数值, 只覆盖需要的字段"""
    return SimpleNamespace(**{**settings.SCHEMA.defaults(), **overrides})


def test_active_rules_only_name_real_fields():
    """生效条件登记的字段都在规格里, 拼错字段名会立刻发现"""
    assert set(settings.ACTIVE_WHEN) <= set(settings.SCHEMA.specs)


@pytest.mark.parametrize(("debug", "rounds"), list(itertools.product([False, True], [0, 1, 3])))
def test_active_rules_truth_table(debug, rounds):
    """保留布尔修改器时递归, 孤岛拆分, 锐边与断面顶点组都不生效; 递归相关参数还要轮数大于 0"""
    owner = values(use_debug_bool=debug, recursion=rounds)
    active = {key: rule(owner) for key, rule in settings.ACTIVE_WHEN.items()}
    LOG.info("debug=%s rounds=%s -> %s", debug, rounds, active)
    for key in ("recursion", "use_sharp_edges", "use_interior_vgroup", "use_island_split"):
        assert active[key] == (not debug)
    for key in (
        "sub_cut_pieces",
        "recursion_chance",
        "recursion_chance_select",
        "use_keep_parents",
    ):
        assert active[key] == (rounds > 0 and not debug)


def test_shard_limit_is_always_active():
    """碎块总数上限约束首次切割, 与递归轮数无关, 所以没有登记生效条件"""
    assert "recursion_clamp" not in settings.ACTIVE_WHEN


@pytest.mark.parametrize(("debug", "rounds"), [(False, 0), (False, 4), (True, 4)])
def test_recursion_rounds(debug, rounds):
    """保留布尔修改器时不递归"""
    owner = values(use_debug_bool=debug, recursion=rounds)
    assert settings.recursion_rounds(owner) == (0 if debug else rounds)


@pytest.mark.parametrize(
    ("limit", "maximum", "expected"),
    [(0, 0, 0), (100, 0, 100), (0, 250, 250), (100, 250, 100), (250, 250, 250), (30, 250, 30)],
)
def test_first_cut_seed_limit(limit, maximum, expected):
    """首次切割种子数为 0 时跟随碎块总数上限, 都为 0 表示不限"""
    owner = values(first_cut_seeds=limit, recursion_clamp=maximum)
    assert settings.first_cut_seed_limit(owner) == expected


def test_defaults_respect_the_order_constraint():
    """默认的首次切割种子数不超过默认的碎块总数上限"""
    defaults = settings.SCHEMA.defaults()
    assert defaults["first_cut_seeds"] <= defaults["recursion_clamp"]


@pytest.mark.parametrize(
    ("debug", "split", "vgroup", "sharp"), list(itertools.product([False, True], repeat=4))
)
def test_pipeline_predicates(debug, split, vgroup, sharp):
    """流水线读的判断函数与参数组合的含义一致"""
    owner = values(
        use_debug_bool=debug,
        use_island_split=split,
        use_interior_vgroup=vgroup,
        use_sharp_edges=sharp,
    )
    assert settings.bakes_booleans(owner) == (not debug)
    assert settings.uses_interior(owner) == (vgroup or sharp)
    assert settings.finishes_interior(owner) == ((not debug) and (vgroup or sharp))
    assert settings.splits_islands(owner) == ((not debug) and split)


@pytest.mark.parametrize(
    ("key", "value", "accepted"),
    [
        ("sub_cut_pieces", 0, False),
        ("sub_cut_pieces", 1, False),
        ("sub_cut_pieces", 2, True),
        ("sub_cut_pieces", 5000, True),
        ("sub_cut_pieces", 5001, False),
        ("recursion", MAX_ROUNDS, True),
        ("recursion", MAX_ROUNDS + 1, False),
        ("recursion_clamp", 0, True),
        ("recursion_clamp", SHARD_LIMIT, True),
        ("recursion_clamp", SHARD_LIMIT + 1, False),
        ("first_cut_seeds", 0, True),
        ("first_cut_seeds", 5001, False),
        ("recursion_chance", 1.5, False),
        ("margin", -0.1, False),
        ("shard_index_padding", 9, False),
    ],
)
def test_parameter_boundaries(key, value, accepted):
    """关键参数的边界值: 每块切割上限至少为 2, 总数上限不超过硬上限"""
    if accepted:
        assert settings.SCHEMA.validate({key: value})[key] == value
    else:
        with pytest.raises(InvalidParameterError):
            settings.SCHEMA.validate({key: value})
