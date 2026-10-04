"""计划编译的输入 - 由资产读出碎块数值, 对弹道驱动求源物体的交点, 再交给冲量模型编译

模拟烘焙与驱动预览共用这里, 所以预览看到的初始条件就是模拟实际使用的初始条件
"""

import math
from dataclasses import dataclass

from mathutils import Vector
from mathutils.bvhtree import BVHTree

from . import asset, ballistics, drivers, impulse, shards
from .blender_context import vertex_coordinates
from .errors import DomainError

# 弹道需要源物体求交, 而源物体已被删除时的消息
SOURCE_MISSING_MESSAGE = (
    'The source object of "{name}" is gone; a projectile needs it to find where it hits'
)
# 沿一段路径最多求交的次数, 防止网格异常时死循环
MAX_CROSSINGS = 64
# 求交后沿路径前进的距离占该段路径长度的比例, 越过刚命中的表面再继续
RESTART_RATIO = 1e-6
# 相邻的穿越点沿路径的间隔小于它视为同一点, 路径折点恰在表面上时不会重复计入
MERGE_DISTANCE = 1e-5


class PlanError(DomainError):
    """计划输入不满足时的错误 - 消息按框架上下文翻译"""


@dataclass
class AssetPlan:
    """一个资产编译出的计划与它的输入"""

    # 资产的碎块, 与 rests, infos 同序
    shards: list
    # 碎块的静止姿态
    rests: list
    # 碎块的编译信息
    infos: list
    # 冲量模型编译出的计划
    plan: impulse.Plan
    # 弹道驱动名 -> 对这个资产的飞行计划
    flights: dict


def rules_of(root):
    """读出资产物理生效记录里的激活规则与穿透阻力

    root 资产根集合, 物理必须已经应用
    返回 impulse.Rules
    """
    record = asset.physics_record(root)
    # 旧记录没有 resistance 字段时按 1 处理, 即与混凝土相同
    return impulse.Rules(
        activation=record["activation"],
        anchor_bottom=bool(record["use_anchor_bottom"]),
        anchor_height=float(record["anchor_height"]),
        resistance=float(record.get("resistance", 1.0)),
    )


def shard_info(shard, rest, asset_id):
    """碎块在静止姿态下的编译信息: 质心是原点的位置, 最低点与厚度由局部包围盒按静止姿态换算

    shard 碎块物体
    rest 碎块的静止世界矩阵
    asset_id 资产标识, 用来拼出碎块的稳定键
    返回 impulse.ShardInfo
    """
    flat = vertex_coordinates(shard.data)
    rows = rest.row
    xs, ys, zs = flat[0::3], flat[1::3], flat[2::3]
    # 最低点: 每个顶点经静止姿态变换后的世界 Z 里取最小, 即矩阵第三行与顶点的点积
    lowest = min(
        rows[2][0] * x + rows[2][1] * y + rows[2][2] * z for x, y, z in zip(xs, ys, zs, strict=True)
    )
    # 厚度: 局部三个轴向的包围盒边长乘各轴缩放, 取最小的一个
    scale = rest.to_scale()
    extents = [
        (max(values) - min(values)) * abs(factor)
        for values, factor in zip((xs, ys, zs), scale, strict=True)
    ]
    # 稳定键用资产标识加路径, 不依赖名字, 改名与复制都不影响
    key = asset_id + "/" + "/".join(str(index) for index in shards.path_of(shard))
    body = shard.rigid_body
    return impulse.ShardInfo(
        key=key,
        center=tuple(rest.translation),
        lowest=lowest + rest[2][3],
        mass=body.mass if body is not None else 0.0,
        thickness=min(extents),
    )


def _source_tree(root, depsgraph):
    """源物体世界网格的求交树

    root 资产根集合
    depsgraph 求值用的依赖图
    返回 BVHTree; 源物体已被删除时抛 PlanError
    """
    source = asset.source_of(root)
    # 源物体被删除后弹道无从求交
    if source is None:
        raise PlanError(SOURCE_MISSING_MESSAGE, name=root.name)
    try:
        return BVHTree.FromObject(source, depsgraph)
    except (RuntimeError, ReferenceError, ValueError):
        # 源物体不在依赖图里 (例如整体停用了视口显示): 退回原始网格
        world = source.matrix_world
        points = [world @ vertex.co for vertex in source.data.vertices]
        return BVHTree.FromPolygons(points, [tuple(face.vertices) for face in source.data.polygons])


def _merge(crossings):
    """合并沿路径相邻且法线朝向相同的穿越点, 折点恰在表面上时不重复

    crossings 已按累计长度排序的穿越点
    返回 合并后的穿越点列表
    """
    merged = []
    for crossing in crossings:
        # 间隔极小且朝向相同: 同一个表面点被两段路径各命中一次
        if merged and abs(crossing.arc - merged[-1].arc) < MERGE_DISTANCE:
            same = impulse.dot(crossing.normal, merged[-1].normal) > 0.0
            if same:
                continue
        merged.append(crossing)
    return merged


def crossings_on_path(tree, path, lengths):
    """求路径与求交树的全部穿越点

    tree 源物体的求交树
    path 弹道折线的各顶点
    lengths 折线各顶点的累计长度
    返回 按沿路径累计长度排序的穿越点列表
    """
    found = []
    for index in range(len(path) - 1):
        start, end = Vector(path[index]), Vector(path[index + 1])
        edge = end - start
        size = edge.length
        # 零长度的线段没有方向, 跳过
        if size <= impulse.TINY:
            continue
        direction = edge / size
        restart = max(size * RESTART_RATIO, MERGE_DISTANCE)
        origin, remaining = start, size
        # 射线只返回第一个命中点, 命中后越过表面再投一次, 直到穿出线段
        for _ in range(MAX_CROSSINGS):
            location, normal, _face, _distance = tree.ray_cast(origin, direction, remaining)
            if location is None:
                break
            found.append(
                ballistics.Crossing(
                    lengths[index] + (location - start).length,
                    tuple(location),
                    tuple(direction),
                    tuple(normal.normalized()),
                )
            )
            # 越过刚命中的表面一点点, 避免下一次又命中同一个面
            origin = location + direction * restart
            remaining = size - (origin - start).length
            if remaining <= 0.0:
                break
    found.sort(key=lambda crossing: crossing.arc)
    return _merge(found)


def asset_strikes(root, snapshots, resistance, depsgraph):
    """求资产对全部弹道驱动的撞击作用与飞行计划

    root 资产根集合
    snapshots 驱动快照列表
    resistance 资产的相对穿透阻力
    depsgraph 求值用的依赖图
    返回 (撞击作用列表, {驱动名: Flight})
    """
    # 只有至少两个点的弹道才有路径可言
    projectiles = [
        item for item in snapshots if item.role == drivers.PROJECTILE and len(item.path) > 1
    ]
    # 没有弹道时不需要构建求交树, 省掉一次网格读取
    if not projectiles:
        return [], {}
    tree = _source_tree(root, depsgraph)
    strikes, flights = [], {}
    for driver in projectiles:
        lengths = ballistics.path_lengths(driver.path)
        crossings = crossings_on_path(tree, driver.path, lengths)
        flight = ballistics.plan_flight(driver, crossings, resistance, driver.path)
        strikes += flight.strikes
        flights[driver.name] = flight
    return strikes, flights


def compile_asset(root, snapshots, timing, depsgraph):
    """读出资产的碎块与规则, 编译这些驱动对它的计划; 资产的物理必须已经应用

    root 资产根集合
    snapshots 驱动快照列表
    timing 时间换算, 提供起始帧与每帧模拟秒
    depsgraph 求值用的依赖图
    返回 AssetPlan
    """
    items = asset.shards_of(root)
    asset_id = shards.asset_of(root)
    rests = [asset.rest_matrix(shard) for shard in items]
    infos = [shard_info(shard, rest, asset_id) for shard, rest in zip(items, rests, strict=True)]
    rules = rules_of(root)
    strikes, flights = asset_strikes(root, snapshots, rules.resistance, depsgraph)
    plan = impulse.compile_plan(infos, snapshots, rules, timing, strikes)
    return AssetPlan(items, rests, infos, plan, flights)


def suggested_substeps(plans, timing, limit):
    """子步建议值 (启发式): ceil(2 x 计划最大速度 x 每帧模拟秒 / 碎块厚度的 5% 分位数), 不超过 limit

    局限: 忽略比 5% 分位更薄的碎块, 没有计入相对速度, 重力加速与旋转扫掠, 不构成防穿透保证
    plans AssetPlan 列表
    timing 时间换算, 提供每帧模拟秒
    limit 子步上限
    返回 (建议值, 是否被 limit 截断); 没有起动事件或没有厚度信息时返回 (0, False)
    """
    speeds = [
        impulse.length(event.velocity)
        for item in plans
        for shard_plan in item.plan.shards.values()
        for event in shard_plan.events
    ]
    thicknesses = sorted(
        info.thickness for item in plans for info in item.infos if info.thickness > 0.0
    )
    # 没有速度或没有厚度时没有依据, 不给建议
    if not speeds or not thicknesses:
        return 0, False
    # 取 5% 分位数而不是最小值: 个别极薄的碎渣不应把子步推到上限
    quantile = thicknesses[min(len(thicknesses) - 1, int(len(thicknesses) * 0.05))]
    # 目标: 一个子步内最快的碎块移动不超过半个薄碎块的厚度
    wanted = max(1, math.ceil(2.0 * max(speeds) * timing.dt / quantile))
    return min(wanted, limit), wanted > limit
