"""划分与命名 - 骨骼命名, Morton 序, 容量受限的确定性划分; 只有数值运算, 不依赖 Blender

游戏引擎对蒙皮有两类上限: 一段网格引用的骨骼数 (部分平台按网格分段算骨骼调色板), 一个骨架的骨骼总数
划分用 Morton 序把空间上相近的碎块排在一起, 再按容量顺序切分: 容量受限且结果确定, 不用 k 均值,
因为 k 均值不限制每组的人数
"""

from collections import namedtuple

# 骨骼名字节上限: 多数游戏引擎与 FBX 工具链按 63 字节截断
BONE_NAME_BYTES = 63
# 根骨骼名
ROOT_BONE = "root"
# Morton 码每个轴的位数
MORTON_BITS = 10
# 路径每段序号的最小位数
PATH_PADDING = 3
# 退回顺序号命名时的位数
FALLBACK_PADDING = 5

# 一个骨架的划分: segments 是各段网格, 每段是会动的碎块在输入序列里的序号
Armature = namedtuple("Armature", ["segments"])


def bone_name(path, padding=PATH_PADDING):
    """碎块路径对应的骨骼名: b_ 加逐层序号, 例如 b_003_002; 只含 ASCII; 超过字节上限时返回 None"""
    name = "b_" + "_".join(f"{index:0{padding}d}" for index in path)
    return name if len(name.encode("ascii")) <= BONE_NAME_BYTES else None


def fallback_name(number):
    """路径过长时退回的骨骼名: b_ 加 5 位顺序号; 映射由调用方记在骨架上"""
    return f"b_{number:0{FALLBACK_PADDING}d}"


def _spread(low, high, bits):
    """把 [low, high] 映射到整数 0 到 2**bits - 1 的比例系数; 区间退化时为 0"""
    size = high - low
    return ((1 << bits) - 1) / size if size > 0.0 else 0.0


def _interleave(x, y, z, bits):
    """三个整数按位交错成 Morton 码"""
    code = 0
    for bit in range(bits):
        code |= ((x >> bit) & 1) << (3 * bit)
        code |= ((y >> bit) & 1) << (3 * bit + 1)
        code |= ((z >> bit) & 1) << (3 * bit + 2)
    return code


def morton_order(points):
    """点序号按 Morton 码排序的结果, 相同的码按序号排; 点的包围盒决定量化范围"""
    if not points:
        return []
    lows = [min(point[axis] for point in points) for axis in range(3)]
    highs = [max(point[axis] for point in points) for axis in range(3)]
    scales = [_spread(lows[axis], highs[axis], MORTON_BITS) for axis in range(3)]

    def code(index):
        """一个点的 Morton 码"""
        point = points[index]
        cells = [int((point[axis] - lows[axis]) * scales[axis]) for axis in range(3)]
        return _interleave(*cells, MORTON_BITS), index

    return sorted(range(len(points)), key=code)


def _chunks(items, size):
    """按容量顺序切分; size 为 0 表示不限, 整体一块; 没有元素时也返回一个空块, 保证至少有一个骨架与一段网格"""
    if not size or not items:
        return [list(items)]
    return [list(items[start : start + size]) for start in range(0, len(items), size)]


def capacities(max_total, max_per_mesh):
    """每个骨架与每段网格最多容纳的会动碎块数: 上限减去 root 占的一个; 0 表示不限; 上限小于 2 时没有容量, 报 ValueError"""
    result = []
    for limit in (max_total, max_per_mesh):
        if limit and limit < 2:
            raise ValueError("A bone limit needs room for the root and at least one shard")
        result.append(limit - 1 if limit else 0)
    return tuple(result)


def partition(points, max_total, max_per_mesh):
    """会动的碎块的划分: 返回 [Armature, ...], 每个骨架的 segments 是若干段, 每段是 points 的序号列表

    points 是各碎块的静止质心; 先按 Morton 序排好, 按骨架容量顺序切成骨架, 再在每个骨架里按段容量切成段
    没有会动的碎块时仍返回一个只有 root 的骨架, 一段空网格
    """
    total_room, mesh_room = capacities(max_total, max_per_mesh)
    ordered = morton_order(points)
    armatures = []
    for group in _chunks(ordered, total_room):
        armatures.append(Armature(tuple(tuple(segment) for segment in _chunks(group, mesh_room))))
    return armatures
