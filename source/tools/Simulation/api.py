"""阶段入口 - 对显式给出的资产根集合烘焙或复位模拟, 添加预设地面; 配置缺失字段取默认值

bake 同步执行到底; bake_steps 是生成器, 每推进一帧产出一次进度, 供可取消的界面任务使用
这些入口都先检查运行锁: 同一时间只允许一个模拟任务
"""

from .core import task
from .core.bake import Progress, SimulationResult
from .core.bake import bake_steps as _bake_steps
from .core.ground import GroundResult
from .core.ground import add_ground as _add_ground
from .core.reset import reset as _reset
from .settings import SCHEMA

__all__ = (
    "GroundResult",
    "Progress",
    "SimulationResult",
    "add_ground",
    "bake",
    "bake_steps",
    "reset",
)


def bake_steps(context, roots, config):
    """烘焙的生成器: 每推进一帧产出 Progress; 调用方负责持有运行锁

    context Blender 上下文
    roots 资产根集合序列
    config 配置字典, 缺失字段取默认值
    返回 生成器, 返回值是 SimulationResult
    """
    return _bake_steps(context, list(roots), SCHEMA.resolve(config))


def bake(context, roots, config):
    """同步烘焙到底; 失败时场景回到烘焙之前

    context Blender 上下文
    roots 资产根集合序列
    config 配置字典, 缺失字段取默认值
    返回 SimulationResult
    """
    with task.holding():
        steps = bake_steps(context, roots, config)
        # 逐帧推进到底, 生成器的返回值在 StopIteration 里
        while True:
            try:
                next(steps)
            except StopIteration as stop:
                return stop.value


def reset(context, roots):
    """删除资产的模拟成果, 碎块回到静止姿态

    context Blender 上下文
    roots 资产根集合序列
    返回 处理的碎块数
    """
    task.ensure_idle()
    return _reset(context, list(roots))


def add_ground(context, roots):
    """在资产最低点下方添加有厚度的静态刚体地面, 已有时重新对齐

    context Blender 上下文
    roots 资产根集合序列
    返回 GroundResult
    """
    task.ensure_idle()
    return _add_ground(context, list(roots))
