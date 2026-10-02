"""阶段入口 - 对显式给出的资产根集合烘焙或复位模拟; 配置缺失字段取默认值

bake 同步执行到底; bake_steps 是生成器, 每推进一帧产出一次进度, 供可取消的界面任务使用
两者都先检查运行锁: 同一时间只允许一个模拟任务
"""

from .core import task
from .core.bake import Progress, SimulationResult
from .core.bake import bake_steps as _bake_steps
from .core.reset import reset as _reset
from .settings import SCHEMA

__all__ = ("Progress", "SimulationResult", "bake", "bake_steps", "reset")


def bake_steps(context, roots, config):
    """烘焙的生成器: 每推进一帧产出 Progress, 返回 SimulationResult; 调用方负责持有运行锁"""
    return _bake_steps(context, list(roots), SCHEMA.resolve(config))


def bake(context, roots, config):
    """同步烘焙到底并返回 SimulationResult; 失败时场景回到烘焙之前"""
    with task.holding():
        steps = bake_steps(context, roots, config)
        while True:
            try:
                next(steps)
            except StopIteration as stop:
                return stop.value


def reset(context, roots):
    """删除资产的模拟成果, 碎块回到静止姿态; 返回处理的碎块数"""
    task.ensure_idle()
    return _reset(context, list(roots))
