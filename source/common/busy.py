"""运行锁 - 同一时间只允许一个模拟任务推进帧; 会改动刚体与资产记录的入口都先检查它

模拟烘焙推进期间场景里的刚体, 动画与记录都处于中间状态, 这时应用物理, 移除物理或重设静止姿态
都会把它们改乱, 所以这些入口在有任务运行时一律拒绝
锁放在 common 里, 物理设置与模拟烘焙两个工具共用, 工具之间不必互相导入
"""

from contextlib import contextmanager

from .errors import DomainError

# 有任务正在运行时的消息
BUSY_MESSAGE = "Another simulation is running"

# 正在运行的任务, 最多一个; 元素是任务的取消函数
RUNNING = []


class BusyError(DomainError):
    """有模拟任务正在运行 - 消息按框架上下文翻译"""


def busy():
    """是否有任务正在运行"""
    return bool(RUNNING)


def ensure_idle():
    """有任务正在运行时报错"""
    if RUNNING:
        raise BusyError(BUSY_MESSAGE)


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
