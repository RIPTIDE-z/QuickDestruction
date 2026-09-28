"""预设文件单元测试

测试目的:
    确认预设名净化, 原子保存, 覆盖语义, 大小上限与信封校验符合约定
测试过程:
    在 pytest 提供的临时目录里保存, 列出, 读取预设, 检查不残留临时文件;
    再构造各种不合规的信封, 确认都被 PresetError 拒绝
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_preset_files.py
"""

import json
import logging

import pytest

from source.framework import preset_files
from source.framework.preset_files import PresetError

LOG = logging.getLogger(__name__)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  mine ", "mine"), ("a/b\\c", "a_b_c"), ("..hidden.", "hidden"), ("预设", "预设")],
)
def test_normalize_name(raw, expected):
    """去掉首尾空白与点号, 路径分隔符替换成下划线"""
    assert preset_files.normalize_name(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "..", "CON", "com1.json", "x" * 121])
def test_normalize_name_rejects(raw):
    """空名, Windows 保留名与超长名被拒绝"""
    with pytest.raises(PresetError):
        preset_files.normalize_name(raw)


def test_save_list_read_roundtrip(tmp_path):
    """保存后能列出并读回同样的内容, 目录里不留临时文件"""
    data = preset_files.encode("tool", {"count": 3})
    name = preset_files.save_preset(tmp_path, " First ", data)
    preset_files.save_preset(tmp_path, "second", data)
    LOG.info("files %s", sorted(path.name for path in tmp_path.iterdir()))
    assert name == "First"
    assert preset_files.list_presets(tmp_path) == ["First", "second"]
    assert preset_files.read_preset(tmp_path / "First.json") == data
    assert not list(tmp_path.glob("*.tmp"))


def test_overwrite_semantics(tmp_path):
    """同名预设在未勾选覆盖时被拒绝, 勾选后替换内容"""
    preset_files.save_preset(tmp_path, "p", preset_files.encode("tool", {"count": 1}))
    with pytest.raises(PresetError):
        preset_files.save_preset(tmp_path, "p", preset_files.encode("tool", {"count": 2}))
    preset_files.save_preset(tmp_path, "p", preset_files.encode("tool", {"count": 2}), True)
    assert preset_files.read_preset(tmp_path / "p.json")["values"] == {"count": 2}


def test_failed_write_leaves_nothing(tmp_path):
    """写入失败时既不留下预设也不留下临时文件"""
    with pytest.raises(ValueError, match="Out of range"):
        preset_files.save_preset(tmp_path, "bad", {"value": float("nan")})
    assert not list(tmp_path.iterdir())


def test_read_rejects_large_file(tmp_path):
    """超过大小上限的文件在解析前就被拒绝"""
    path = tmp_path / "big.json"
    path.write_text(json.dumps("x" * preset_files.MAX_SIZE), encoding="utf-8")
    with pytest.raises(PresetError):
        preset_files.read_preset(path)


def test_decode_roundtrip():
    """合规信封返回其中的参数"""
    assert preset_files.decode("tool", preset_files.encode("tool", {"a": 1})) == {"a": 1}


@pytest.mark.parametrize(
    "data",
    [
        [],
        {"a": 1},
        {"format_version": 1, "tool_id": "tool"},
        {"format_version": 1, "tool_id": "tool", "values": []},
        {"format_version": True, "tool_id": "tool", "values": {}},
        {"format_version": 2, "tool_id": "tool", "values": {}},
        {"format_version": 1, "tool_id": "other", "values": {}},
        {"format_version": 1, "tool_id": "tool", "values": {}, "extra": 0},
    ],
)
def test_decode_rejects(data):
    """结构不符, 版本不符与其他工具的预设都被拒绝"""
    LOG.info("reject %r", data)
    with pytest.raises(PresetError):
        preset_files.decode("tool", data)
