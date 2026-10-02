"""运行锁 - 同一时间只允许一个模拟任务推进帧; 算子与阶段 API 的入口都先检查它"""

from contextlib import contextmanager

from ..errors import SimulationError

# 正在运行的任务, 最多一个; 元素是任务的取消函数
RUNNING = []


def busy():
    """是否有任务正在运行"""
    return bool(RUNNING)


def ensure_idle():
    """有任务正在运行时报错"""
    if RUNNING:
        raise SimulationError("Another simulation is running")


def claim(abort):
    """登记一个任务, abort 是取消它的函数; 已有任务时报错"""
    ensure_idle()
    RUNNING.append(abort)


def release(abort):
    """任务结束时解除登记"""
    if abort in RUNNING:
        RUNNING.remove(abort)


def abort_all():
    """取消全部正在运行的任务, 用于载入文件与停用插件"""
    for abort in list(RUNNING):
        abort()
    RUNNING.clear()


@contextmanager
def holding():
    """同步执行期间持有运行锁"""
    claim(holding)
    try:
        yield
    finally:
        release(holding)
