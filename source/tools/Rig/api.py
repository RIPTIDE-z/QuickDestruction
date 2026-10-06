"""阶段入口 - 对显式给出的资产根集合构建绑定并检查; 配置缺失字段取默认值

build_rig 同步执行到底; build_rig_steps 是生成器, 每个阶段产出一次进度, 供可取消的界面任务使用
构建先检查运行锁: 同一时间只允许一个长任务
"""

from ...common import asset, busy
from .core import check as _check
from .core.build import Progress, RigResult
from .core.build import build_steps as _build_steps
from .settings import SCHEMA

__all__ = ("Progress", "RigResult", "build_rig", "build_rig_steps", "check")


def build_rig_steps(context, roots, config):
    """构建的生成器: 每个阶段产出一次 Progress; 调用方负责持有运行锁

    context Blender 上下文
    roots 资产根集合序列
    config 配置字典, 缺失字段取默认值
    返回 生成器, 返回值是 [RigResult, ...]
    """
    return _build_steps(context, list(roots), SCHEMA.resolve(config))


def build_rig(context, roots, config):
    """同步构建到底; 失败时场景回到构建之前, 旧绑定保留

    context Blender 上下文
    roots 资产根集合序列
    config 配置字典, 缺失字段取默认值
    返回 [RigResult, ...]
    """
    with busy.holding():
        steps = build_rig_steps(context, roots, config)
        # 逐步推进到底, 生成器的返回值在 StopIteration 里
        while True:
            try:
                next(steps)
            except StopIteration as stop:
                return stop.value


def check(context, root, config, sample_frames=3):
    """检查资产的绑定

    context Blender 上下文
    root 资产根集合
    config 配置字典, 缺失字段取默认值
    sample_frames 逐帧蒙皮核对抽取的帧数, 0 表示不核对
    返回 Issue 元组; 没有构建过时抛 RigError
    """
    busy.ensure_idle()
    asset.validate([root])
    return _check.check_rig(context, root, SCHEMA.resolve(config), sample_frames)
