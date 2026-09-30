"""规格引擎单元测试

测试目的:
    确认 Schema 的校验规则, 形态转换与写回回滚符合约定, 这是预设载入与阶段 API 的共同入口
测试过程:
    用一份覆盖全部属性类型的小规格逐条检查接受与拒绝; 用 SimpleNamespace 充当参数组,
    检查读取, 写回与失败回滚; 最后确认 MeshFracture 的默认值能整体通过校验
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_schema.py
"""

import logging
from types import SimpleNamespace

import pytest

from source.common.errors import InvalidParameterError
from source.common.schema import Schema
from source.tools.MeshFracture.settings import SCHEMA

LOG = logging.getLogger(__name__)

# 覆盖全部属性类型的小规格
SPECS = {
    "flag": ("BoolProperty", {"name": "Flag", "default": False}),
    "count": ("IntProperty", {"name": "Count", "default": 1, "min": 0, "max": 10}),
    "index": ("IntProperty", {"name": "Index", "default": 0, "min": 0}),
    "ratio": ("FloatProperty", {"name": "Ratio", "default": 0.5, "min": 0.0, "max": 1.0}),
    "label": ("StringProperty", {"name": "Label", "default": ""}),
    "mode": (
        "EnumProperty",
        {"name": "Mode", "items": (("A", "A", ""), ("B", "B", "")), "default": "A"},
    ),
    "sources": (
        "EnumProperty",
        {
            "name": "Sources",
            "items": (("X", "X", ""), ("Y", "Y", "")),
            "options": {"ENUM_FLAG"},
            "default": {"X"},
        },
    ),
    "scale": (
        "FloatVectorProperty",
        {"name": "Scale", "size": 3, "min": 0.0, "max": 1.0, "default": (1.0, 1.0, 1.0)},
    ),
}
SCHEMA_UNDER_TEST = Schema(SPECS, "test")

ACCEPTED = [
    ("flag", True),
    ("count", 10),
    ("index", 2**31 - 1),
    ("ratio", 1),
    ("label", "中文"),
    ("mode", "B"),
    ("sources", ["Y", "X"]),
    ("sources", ("X",)),
    ("scale", [0.0, 0.5, 1.0]),
]
REJECTED = [
    ("flag", 1),
    ("count", 1.0),
    ("count", True),
    ("count", 11),
    ("index", 2**31),
    ("index", 10**400),
    ("ratio", float("nan")),
    ("ratio", 1.5),
    ("label", "a\x00b"),
    ("mode", "C"),
    ("sources", ["Z"]),
    ("sources", "X"),
    ("scale", [0.5, 0.5]),
    ("scale", [0.5, 0.5, 2.0]),
    ("unknown", 1),
]


@pytest.mark.parametrize(("key", "value"), ACCEPTED)
def test_accepts_valid_values(key, value):
    """合法值通过校验"""
    LOG.info("accept %s=%r", key, value)
    assert key in SCHEMA_UNDER_TEST.validate({key: value})


@pytest.mark.parametrize(("key", "value"), REJECTED)
def test_rejects_invalid_values(key, value):
    """非法值与未知字段抛出带字段名的 InvalidParameterError, 超大整数也不会抛 OverflowError"""
    LOG.info("reject %s=%r", key, value)
    with pytest.raises(InvalidParameterError) as caught:
        SCHEMA_UNDER_TEST.validate({key: value})
    assert caught.value.field == key


def test_value_forms():
    """validate 返回 RNA 形态, read 返回可 JSON 化的数据形态"""
    values = SCHEMA_UNDER_TEST.validate({"sources": ["Y", "X"], "scale": [0.1, 0.2, 0.3]})
    assert values == {"sources": {"X", "Y"}, "scale": (0.1, 0.2, 0.3)}
    owner = SimpleNamespace(**SCHEMA_UNDER_TEST.validate(SCHEMA_UNDER_TEST.defaults()))
    data = SCHEMA_UNDER_TEST.read(owner)
    LOG.info("read %r", data)
    assert data["sources"] == ["X"]
    assert data["scale"] == [1.0, 1.0, 1.0]


def test_missing_fields_are_skipped():
    """缺失字段不出现在结果里, 写回时保留原值"""
    assert SCHEMA_UNDER_TEST.validate({"count": 3}) == {"count": 3}


def test_write_roundtrip_and_rollback():
    """写回返回字段数; 任一字段赋值失败时恢复已写的字段"""
    owner = SimpleNamespace(**SCHEMA_UNDER_TEST.validate(SCHEMA_UNDER_TEST.defaults()))
    assert SCHEMA_UNDER_TEST.write(owner, {"count": 4, "ratio": 0.25}) == 2
    assert (owner.count, owner.ratio) == (4, 0.25)

    class Failing(SimpleNamespace):
        """写 label 时抛错的参数组, 模拟 RNA 赋值失败"""

        def __setattr__(self, name, value):
            """写 label 为 boom 时抛错, 其余赋值照常进行"""
            if name == "label" and value == "boom":
                raise ValueError("rejected")
            super().__setattr__(name, value)

    failing = Failing(**SCHEMA_UNDER_TEST.validate(SCHEMA_UNDER_TEST.defaults()))
    with pytest.raises(ValueError, match="rejected"):
        SCHEMA_UNDER_TEST.write(failing, {"count": 7, "label": "boom"})
    LOG.info("after rollback count=%s", failing.count)
    assert failing.count == 1


# 带顺序约束的小规格: low 不得超过 high, high 为 0 表示不限
ORDERED = Schema(
    {
        "low": ("IntProperty", {"name": "Low", "default": 0, "min": 0}),
        "high": ("IntProperty", {"name": "High", "default": 0, "min": 0}),
    },
    "test",
    orders=(("low", "high"),),
)


@pytest.mark.parametrize(
    ("low", "high", "accepted"),
    [(3, 5, True), (5, 5, True), (0, 5, True), (9, 0, True), (0, 0, True), (6, 5, False)],
)
def test_order_constraint(low, high, accepted):
    """小的一方不得超过大的一方; 大的一方为 0 表示不限, 小的一方为 0 总是允许"""
    LOG.info("low=%s high=%s accepted=%s", low, high, accepted)
    if accepted:
        assert ORDERED.validate({"low": low, "high": high}) == {"low": low, "high": high}
        return
    with pytest.raises(InvalidParameterError) as caught:
        ORDERED.validate({"low": low, "high": high})
    assert caught.value.field == "low"


def test_order_constraint_uses_base_for_missing_fields():
    """只给一部分字段时, 顺序约束与现有值一起检查, 现有值不进入结果"""
    assert ORDERED.validate({"low": 9}) == {"low": 9}
    with pytest.raises(InvalidParameterError):
        ORDERED.validate({"low": 9}, base={"high": 5})
    assert ORDERED.validate({"high": 20}, base={"low": 9}) == {"high": 20}
    with pytest.raises(InvalidParameterError):
        ORDERED.validate({"high": 4}, base={"low": 9})


def test_write_checks_order_against_current_values():
    """写回只给一个字段时按参数组上的现有值检查; 被拒绝时参数组不变"""
    owner = SimpleNamespace(low=3, high=5)
    with pytest.raises(InvalidParameterError):
        ORDERED.write(owner, {"low": 8})
    assert (owner.low, owner.high) == (3, 5)
    assert ORDERED.write(owner, {"low": 8, "high": 10}) == 2
    assert (owner.low, owner.high) == (8, 10)


def test_order_constraint_must_name_known_fields_once():
    """顺序约束里的字段必须在规格里, 每个字段最多出现一次"""
    specs = ORDERED.specs
    with pytest.raises(ValueError, match="Order constraints"):
        Schema(specs, "test", orders=(("low", "missing"),))
    with pytest.raises(ValueError, match="Order constraints"):
        Schema(specs, "test", orders=(("low", "high"), ("high", "low")))


def test_mesh_fracture_first_cut_cannot_exceed_shard_limit():
    """MeshFracture 的首次切割种子数不得超过碎块总数上限, 总数上限为 0 时不限"""
    with pytest.raises(InvalidParameterError) as caught:
        SCHEMA.resolve({"first_cut_seeds": 300, "recursion_clamp": 250})
    assert caught.value.field == "first_cut_seeds"
    assert SCHEMA.resolve({"first_cut_seeds": 300, "recursion_clamp": 0}).first_cut_seeds == 300
    assert SCHEMA.resolve({"first_cut_seeds": 250, "recursion_clamp": 250}).recursion_clamp == 250


def test_invalid_parameter_carries_label():
    """参数错误带英文参数名与它的翻译上下文, 未知字段的参数名退回字段名"""
    error = SCHEMA_UNDER_TEST.invalid("count")
    assert (error.field, error.values, error.terms) == (
        "count",
        {"name": "Count"},
        {"name": "test"},
    )
    assert SCHEMA_UNDER_TEST.invalid("missing").values == {"name": "missing"}


def test_resolve_fills_defaults_and_is_read_only():
    """缺失字段取默认值, 未知字段被拒绝, 得到的参数值只读, 拼错字段名立刻报错"""
    values = SCHEMA.resolve({"margin": 0.25})
    assert values.margin == 0.25
    assert values.recursion == SCHEMA.defaults()["recursion"]
    with pytest.raises(InvalidParameterError):
        SCHEMA.resolve({"no_such_field": 1})
    with pytest.raises(AttributeError):
        values.margin = 1.0
    with pytest.raises(AttributeError):
        _ = values.no_such_field


def test_mesh_fracture_defaults_are_valid():
    """MeshFracture 的每个字段都声明了默认值, 并且默认值能整体通过校验"""
    values = SCHEMA.validate(SCHEMA.defaults())
    LOG.info("MeshFracture fields %d", len(values))
    assert set(values) == set(SCHEMA.specs)
