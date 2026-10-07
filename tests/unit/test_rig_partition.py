"""绑定构建的划分与命名单元测试

测试目的:
    1. 骨骼命名: b_ 加从 1 开始的平铺顺序号, 只含 ASCII, 不超过 63 字节
    2. Morton 序: 空间上相近的点在序里相邻, 相同位置按序号排, 结果确定
    3. 容量: 上限减去 root 占的一个, 0 表示不限, 小于 2 的上限没有容量
    4. 划分: 每个骨架与每段网格都不超过容量, 不重不漏, 没有会动的碎块时仍有一个只有 root 的骨架
测试过程:
    手工构造点列, 直接调用 source.tools.Rig.core.partition, 不需要 Blender
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_rig_partition.py
"""

import logging
import random
from itertools import pairwise

import pytest

from source.tools.Rig.core import partition

LOG = logging.getLogger(__name__)


def test_bone_name_is_flat_and_ascii():
    """骨骼名平铺编号: b_ 加从 1 开始的顺序号, 不足三位补零, 超过三位照写, 始终在字节上限内"""
    assert partition.bone_name(0) == "b_001"
    assert partition.bone_name(41) == "b_042"
    assert partition.bone_name(1233) == "b_1234"
    assert len(partition.bone_name(10**6).encode("ascii")) <= partition.BONE_NAME_BYTES


def test_morton_order_is_deterministic_and_local():
    """相同输入同样的序; 两簇远离的点各自连续排在一起, 不交错"""
    cluster_a = [(0.0 + i * 0.01, 0.0, 0.0) for i in range(8)]
    cluster_b = [(10.0 + i * 0.01, 10.0, 10.0) for i in range(8)]
    points = [*cluster_a, *cluster_b]
    mixed = list(range(16))
    random.Random(3).shuffle(mixed)
    shuffled = [points[i] for i in mixed]
    order = partition.morton_order(shuffled)
    assert order == partition.morton_order(shuffled)
    groups = ["a" if shuffled[i][0] < 5 else "b" for i in order]
    switches = sum(1 for a, b in pairwise(groups) if a != b)
    LOG.info("groups %s", "".join(groups))
    assert switches == 1


def test_identical_points_keep_index_order():
    """位置完全相同的点 Morton 码相同, 按序号排, 退化的包围盒不抛错"""
    assert partition.morton_order([(1.0, 1.0, 1.0)] * 4) == [0, 1, 2, 3]
    assert partition.morton_order([]) == []


def test_capacities():
    """容量是上限减 root; 0 不限; 1 没有容量"""
    assert partition.capacities(0, 0) == (0, 0)
    assert partition.capacities(10, 4) == (9, 3)
    with pytest.raises(ValueError, match="room"):
        partition.capacities(1, 0)
    with pytest.raises(ValueError, match="room"):
        partition.capacities(0, 1)


def test_capacities_with_fixed_bones():
    """原物体切换每个骨架有 3 根固定骨骼 (root, intact, still): 骨架容量减 3, 网格段容量仍减 1"""
    LOG.info("fixed 3: %s", partition.capacities(10, 4, 3))
    assert partition.capacities(10, 4, 3) == (7, 3)
    assert partition.capacities(4, 0, 3) == (1, 0)
    with pytest.raises(ValueError, match="room"):
        partition.capacities(3, 0, 3)
    # 划分后每个骨架的会动碎块数不超过上限减固定骨骼数
    points = [(float(index), 0.0, 0.0) for index in range(10)]
    armatures = partition.partition(points, 5, 0, 3)
    assert all(len(armature.segments[0]) <= 2 for armature in armatures)
    assert sorted(i for a in armatures for s in a.segments for i in s) == list(range(10))


@pytest.mark.parametrize(("total", "per_mesh"), [(0, 0), (0, 5), (7, 0), (7, 4), (31, 8), (2, 2)])
def test_partition_respects_limits_and_covers_everything(total, per_mesh):
    """每个骨架至多 total - 1 块, 每段至多 per_mesh - 1 块, 全部碎块恰好出现一次"""
    rng = random.Random(11)
    points = [(rng.random(), rng.random(), rng.random()) for _ in range(100)]
    armatures = partition.partition(points, total, per_mesh)
    seen = []
    for armature in armatures:
        count = sum(len(segment) for segment in armature.segments)
        if total:
            assert count <= total - 1
        for segment in armature.segments:
            if per_mesh:
                assert len(segment) <= per_mesh - 1
            seen.extend(segment)
    assert sorted(seen) == list(range(100))
    LOG.info("total %d per_mesh %d -> %d armatures", total, per_mesh, len(armatures))


def test_partition_is_deterministic():
    """同样的输入得到同样的划分"""
    rng = random.Random(5)
    points = [(rng.random(), rng.random(), rng.random()) for _ in range(50)]
    assert partition.partition(points, 9, 4) == partition.partition(points, 9, 4)


def test_no_moving_shards_still_has_one_rig():
    """没有会动的碎块: 一个骨架, 一段空网格, 只有 root"""
    armatures = partition.partition([], 10, 4)
    assert len(armatures) == 1
    assert armatures[0].segments == ((),)


def test_neighbours_share_a_segment():
    """分段按 Morton 序: 一个紧凑的簇通常落在同一段, 切分只发生在簇与簇之间"""
    points = [(i * 0.001, 0.0, 0.0) for i in range(6)] + [
        (5.0 + i * 0.001, 5.0, 5.0) for i in range(6)
    ]
    (armature,) = partition.partition(points, 0, 7)
    first = {index < 6 for index in armature.segments[0]}
    assert len(armature.segments) == 2
    assert first == {True}
