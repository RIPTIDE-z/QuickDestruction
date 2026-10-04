"""运行锁 - 同一时间只允许一个模拟任务推进帧; 会改动刚体与资产记录的入口都先检查它

模拟烘焙推进期间场景里的刚体, 动画与记录都处于中间状态, 这时应用物理, 移除物理或重设静止姿态
都会把它们改乱, 所以这些入口在有任务运行时一律拒绝
锁放在 common 里, 物理设置与模拟烘焙两个工具共用, 工具之间不必互相导入
"""

from contextlib import contextmanager

from .errors import DomainError

# 有任务正在运行时的消息
BUSY_MESSAGE = "Another task is running"

# 正在运行的任务, 最多一个; 元素是任务的取消函数, 载入文件与停用插件时靠它取消任务
RUNNING = []


class BusyError(DomainError):
    """有模拟任务正在运行 - 消息按框架上下文翻译"""


def busy():
    """是否有任务正在运行

    返回 有任务正在运行时为真
    """
    return bool(RUNNING)


def ensure_idle():
    """要求当前没有任务在运行

    有任务正在运行时抛出 BusyError
    """
    if RUNNING:
        raise BusyError(BUSY_MESSAGE)


def claim(abort):
    """登记一个任务, 已有任务时抛出 BusyError

    abort 取消这个任务的无参函数, 不会被调用除非载入文件或停用插件
    """
    # 先检查再登记: 同一时间只允许一个任务, 后来者直接被拒绝
    ensure_idle()
    RUNNING.append(abort)


def release(abort):
    """任务结束时解除登记, 没有登记过的任务直接忽略

    abort 登记时给出的取消函数
    """
    # 任务可能已被 abort_all 清掉, 重复解除不报错
    if abort in RUNNING:
        RUNNING.remove(abort)


def abort_all():
    """取消全部正在运行的任务, 用于载入文件与停用插件"""
    # 遍历副本: 取消函数内部会调用 release 修改 RUNNING
    for abort in list(RUNNING):
        abort()
    RUNNING.clear()


@contextmanager
def holding():
    """同步执行期间持有运行锁, 退出时无论成败都释放

    已有任务正在运行时进入前抛出 BusyError
    """
    # 同步任务没有取消函数, 用本函数自己占位
    claim(holding)
    try:
        yield
    finally:
        release(holding)
