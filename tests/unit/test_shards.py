"""碎块契约单元测试

测试目的:
    其他工具靠碎块上的标记识别碎块, 父碎块与所属资产, 标记的写入与读取要一致
测试过程:
    用字典充当物体 (支持下标读写与 get), 写入标记后检查层级, 路径, 资产标识, 父路径,
筛选结果与静止姿态
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_shards.py
"""

from source.common import shards


def test_mark_records_source_level_and_path():
    """层级等于路径长度, 路径以列表保存"""
    shard = {}
    shards.mark(shard, "Cube", (3, 2), "a1")
    assert shard[shards.SOURCE_MARK] == "Cube"
    assert shard[shards.LEVEL_MARK] == 2
    assert shard[shards.PATH_MARK] == [3, 2]
    assert shards.asset_of(shard) == "a1"
    assert shards.path_of(shard) == (3, 2)


def test_parent_path_drops_the_last_index():
    """去掉最后一项就是父碎块的路径; 首次切割的碎块父级是源物体, 路径为空"""
    first, deeper = {}, {}
    shards.mark(first, "Cube", (5,), "a1")
    shards.mark(deeper, "Cube", (5, 2, 7), "a1")
    assert shards.parent_path(first) == ()
    assert shards.parent_path(deeper) == (5, 2)


def test_shards_of_filters_by_source_and_skips_parents():
    """只留下指定源物体的碎块, 保留下来的父碎块不算碎块"""
    kept, other, parent = {}, {}, {}
    shards.mark(kept, "Cube", (1,), "a1")
    shards.mark(other, "Sphere", (1,), "a2")
    shards.mark(parent, "Cube", (2,), "a1")
    shards.mark_parent(parent)
    assert shards.shards_of([kept, other, parent, {}], "Cube") == [kept]


def test_parent_mark_is_detected():
    """父碎块标记写入后能被识别, 普通碎块不是父碎块"""
    shard, parent = {}, {}
    shards.mark(shard, "Cube", (1,), "a1")
    shards.mark(parent, "Cube", (2,), "a1")
    shards.mark_parent(parent)
    assert not shards.is_parent(shard)
    assert shards.is_parent(parent)


def test_rest_pose_round_trip():
    """静止姿态按 16 个浮点数写入并原样读回, 没有记录时为 None"""
    shard = {}
    assert shards.rest_of(shard) is None
    values = [float(index) for index in range(16)]
    shards.set_rest(shard, values)
    assert shards.rest_of(shard) == tuple(values)
