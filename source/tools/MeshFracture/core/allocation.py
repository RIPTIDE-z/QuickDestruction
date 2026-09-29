"""碎块数分配 - 碎块总数上限放不下整轮细分时, 把剩余的块数分给各个候选; 只有数值运算, 不依赖 Blender

candidates 是按优先级排好的候选序列, caps 是每个候选最多切成的块数, budget 是本轮还能新增的块数
返回 [(候选, 种子上限), ...]: 种子上限是这个候选这一轮切成的块数, 净增 种子上限 - 1 块
"""


def fits(caps, budget):
    """全部候选都切满时净增的块数是否不超过预算"""
    return sum(cap - 1 for cap in caps.values()) <= budget


def even_split(candidates, caps, budget):
    """平均细分: 按顺序轮流给每块多分一块, 直到用完预算或各块都到达上限, 余下的先给排在前面的"""
    extra = dict.fromkeys(candidates, 0)
    while budget:
        grown = False
        for candidate in candidates:
            if budget and extra[candidate] < caps[candidate] - 1:
                extra[candidate] += 1
                budget -= 1
                grown = True
        if not grown:
            break
    return [(candidate, extra[candidate] + 1) for candidate in candidates if extra[candidate]]


def in_order(candidates, caps, budget):
    """按优先级依次切满, 预算只剩一部分时最后一块只切到剩余的块数"""
    plan = []
    for candidate in candidates:
        if budget <= 0:
            break
        extra = min(caps[candidate] - 1, budget)
        if extra > 0:
            plan.append((candidate, extra + 1))
            budget -= extra
    return plan
