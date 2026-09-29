"""碎块契约单元测试

测试目的:
    其他工具靠碎块上的标记识别碎块与父碎块, 标记的写入与读取要一致
测试过程:
    用字典充当物体 (支持下标读写与 get), 写入标记后检查层级, 路径, 父路径与筛选结果
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_shards.py
"""

from source.common import shards


def test_mark_records_source_level_and_path():
    """层级等于路径长度, 路径以列表保存"""
    shard = {}
    shards.mark(shard, "Cube", (3, 2))
    assert shard[shards.SOURCE_MARK] == "Cube"
    assert shard[shards.LEVEL_MARK] == 2
    assert shard[shards.PATH_MARK] == [3, 2]


def test_parent_path_drops_the_last_index():
    """去掉最后一项就是父碎块的路径; 首次切割的碎块父级是源物体, 路径为空"""
    first, deeper = {}, {}
    shards.mark(first, "Cube", (5,))
    shards.mark(deeper, "Cube", (5, 2, 7))
    assert shards.parent_path(first) == ()
    assert shards.parent_path(deeper) == (5, 2)


def test_shards_of_filters_by_source_and_skips_parents():
    """只留下指定源物体的碎块, 保留下来的父碎块不算碎块"""
    kept, other, parent = {}, {}, {}
    shards.mark(kept, "Cube", (1,))
    shards.mark(other, "Sphere", (1,))
    shards.mark(parent, "Cube", (2,))
    shards.mark_parent(parent)
    assert shards.shards_of([kept, other, parent, {}], "Cube") == [kept]
