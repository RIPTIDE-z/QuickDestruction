"""递归规划 - 每轮按细分比例挑出候选, 碎块总数上限放不下整轮细分时按细分优先级分配块数

预算够用时全部候选按每块切割上限切满, 顺序与随机流消费都和不设上限时相同
预算不够时整轮一次规划好, 不在轮中途按列表顺序截断, 细分因此不会偏向列表前面的碎块
"""

from mathutils import Vector

from . import allocation, sampling


def _diagonal_squared(obj):
    """世界空间包围盒对角线的平方, 排序时省去开方"""
    corner = obj.matrix_world @ Vector(obj.bound_box[0])
    return (corner - obj.matrix_world @ Vector(obj.bound_box[6])).length_squared


def ordered(objects, mode, cursor, rng):
    """按细分优先级排序; 平均细分按大碎块在前, 余下的块数先给大碎块; 随机会消耗随机流"""
    items = list(objects)
    if mode == "RANDOM":
        rng.shuffle(items)
    elif mode in {"EVEN", "SIZE_MAX", "SIZE_MIN"}:
        items.sort(key=_diagonal_squared, reverse=mode != "SIZE_MIN")
    else:
        # 按物体原点与 3D 游标的距离排序
        items.sort(
            key=lambda obj: (obj.matrix_world.translation - cursor).length_squared,
            reverse=mode == "CURSOR_MAX",
        )
    return items


def candidates(cells, settings, cursor, rng):
    """按每轮细分比例挑出本轮候选, 数量向下取整; 全选时保持原顺序且不消耗随机流"""
    fraction = settings.recursion_chance
    if fraction == 1:
        return list(cells)
    chosen = ordered(cells, settings.recursion_chance_select, cursor, rng)
    return chosen[: int(fraction * len(chosen))]


def _pieces_cap(context, parent, settings):
    """细分 parent 最多得到的块数: 每块切割上限与可用点数取小, 去重与空交集只会更少"""
    count = len(sampling.sample_points(context, parent, settings))
    return min(settings.sub_cut_pieces, count)


def projected_count(context, cells, plan, settings):
    """按计划细分后碎块数的上界: 每个父块最多变成 种子上限 与 可用点数 中较小的块数, 至少仍是 1 块"""
    grown = sum(
        max(1, min(limit, len(sampling.sample_points(context, parent, settings))))
        for parent, limit in plan
    )
    return len(cells) - len(plan) + grown


def plan_round(context, cells, settings, cursor, rng, budget):
    """规划一轮细分, 返回 [(父块, 种子上限), ...]; budget 是本轮还能新增的块数, None 表示不限"""
    chosen = candidates(cells, settings, cursor, rng)
    limit = settings.sub_cut_pieces
    if budget is None:
        return [(parent, limit) for parent in chosen]
    caps = {parent: _pieces_cap(context, parent, settings) for parent in chosen}
    if allocation.fits(caps, budget):
        return [(parent, limit) for parent in chosen]
    mode = settings.recursion_chance_select
    # 比例小于 1 时候选已按优先级排好, 不再重排
    parents = chosen if settings.recursion_chance < 1 else ordered(chosen, mode, cursor, rng)
    split = allocation.even_split if mode == "EVEN" else allocation.in_order
    return split(parents, caps, budget)
