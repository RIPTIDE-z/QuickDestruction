"""阶段入口 - 对显式给出的资产根集合应用, 移除物理或重设静止姿态, 返回只读结果

配置缺失字段取默认值; 这些入口会改动刚体与资产记录, 所以都先检查运行锁
"""

from ...common import busy
from .core import apply as _apply
from .core.apply import PhysicsResult
from .settings import SCHEMA, complete_config

__all__ = ("PhysicsResult", "apply_physics", "remove_physics", "reset_rest")


def apply_physics(context, roots, config):
    """对资产应用物理: 建立刚体, 质心原点, 静止姿态, 按材料写入属性; 失败时撤回本次全部改动

    只给出 material 时, 其余物理量取该材料的值; 显式给出的字段优先
    context Blender 上下文
    roots 资产根集合序列
    config 配置字典, 缺失字段取默认值
    返回 PhysicsResult
    """
    busy.ensure_idle()
    return _apply.apply(context, list(roots), SCHEMA.resolve(complete_config(config)))


def remove_physics(context, roots):
    """删除资产碎块的刚体数据与物理生效记录

    context Blender 上下文
    roots 资产根集合序列
    返回 处理的碎块数
    """
    busy.ensure_idle()
    return _apply.remove(context, list(roots))


def reset_rest(context, roots):
    """把碎块当前的位置记为新的静止姿态; 已有模拟成果时报错

    context Blender 上下文
    roots 资产根集合序列
    返回 处理的碎块数
    """
    busy.ensure_idle()
    return _apply.reset_rest(context, list(roots))
