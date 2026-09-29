"""碎块数分配单元测试

测试目的:
    碎块总数上限放不下整轮细分时, 剩余的块数怎么分给各个候选是纯数值逻辑, 需要在不启动 Blender 的
    情况下确认: 总数不超预算, 平均细分让每块都分到, 按顺序细分只让靠前的切满
测试过程:
    用整数当作候选, 构造预算够用, 不够, 为零, 上限为 1 等情形, 逐条检查返回的计划
    还复现了 doc/MeshFracture.md 里的例子: 30 块, 每块最多切成 8 块, 预算 50
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_allocation.py
"""

import logging

import pytest

from source.tools.MeshFracture.core import allocation

LOG = logging.getLogger(__name__)


def gain(plan):
    """计划带来的净增块数: 每个候选切成 n 块, 净增 n - 1"""
    return sum(pieces - 1 for _candidate, pieces in plan)


def test_fits():
    """全部切满的净增块数不超过预算时才算放得下"""
    caps = {"a": 4, "b": 3}
    assert allocation.fits(caps, 5)
    assert allocation.fits(caps, 6)
    assert not allocation.fits(caps, 4)
    assert allocation.fits({}, 0)


def test_even_split_documented_example():
    """30 块, 每块最多 8 块, 预算 50: 每块都分到, 块数只有 2 或 3"""
    candidates = list(range(30))
    plan = allocation.even_split(candidates, dict.fromkeys(candidates, 8), 50)
    LOG.info("even split sizes %s", sorted({pieces for _c, pieces in plan}))
    assert len(plan) == 30
    assert gain(plan) == 50
    # 余下的 20 块先给排在前面的候选
    assert [pieces for _c, pieces in plan] == [3] * 20 + [2] * 10


def test_in_order_documented_example():
    """按顺序切满: 前 7 块切成 8 块, 第 8 块只切成 2 块, 其余不动"""
    candidates = list(range(30))
    plan = allocation.in_order(candidates, dict.fromkeys(candidates, 8), 50)
    assert plan == [(index, 8) for index in range(7)] + [(7, 2)]
    assert gain(plan) == 50


@pytest.mark.parametrize("split", [allocation.even_split, allocation.in_order])
def test_never_exceeds_budget(split):
    """不同的预算与上限组合下, 净增块数都不超过预算, 也不超过各自的上限"""
    candidates = list(range(7))
    caps = {0: 2, 1: 5, 2: 1, 3: 8, 4: 3, 5: 2, 6: 6}
    for budget in range(40):
        plan = split(candidates, caps, budget)
        assert gain(plan) <= budget
        assert all(2 <= pieces <= caps[candidate] for candidate, pieces in plan)
        # 上限为 1 的候选没有可增加的块数, 不会出现在计划里
        assert all(candidate != 2 for candidate, _pieces in plan)


@pytest.mark.parametrize("split", [allocation.even_split, allocation.in_order])
def test_zero_budget_and_empty(split):
    """预算为零或没有候选时没有任何计划"""
    assert split([1, 2], {1: 4, 2: 4}, 0) == []
    assert split([], {}, 10) == []


@pytest.mark.parametrize("split", [allocation.even_split, allocation.in_order])
def test_uses_whole_budget_when_possible(split):
    """候选的上限总和大于预算时, 预算被用完"""
    candidates = list(range(5))
    plan = split(candidates, dict.fromkeys(candidates, 6), 12)
    assert gain(plan) == 12


@pytest.mark.parametrize("split", [allocation.even_split, allocation.in_order])
def test_plan_follows_candidate_order(split):
    """计划里的候选顺序与传入的优先级顺序一致"""
    candidates = [9, 3, 7, 1]
    caps = dict.fromkeys(candidates, 4)
    planned = [candidate for candidate, _pieces in split(candidates, caps, 5)]
    assert planned == [c for c in candidates if c in planned]
