"""长任务算子 - 可取消的模态任务的公共执行流程, 模拟烘焙与绑定构建共用

任务由生成器推进: 每产出一次 Progress 就是一个可以取消的检查点, 返回值是任务结果
脚本经 execute 同步执行到底; 界面经 invoke 先推进第一步, 校验错误立即报告, 之后由定时器逐步推进
按 Esc 取消时关闭生成器, 生成器的收尾负责撤回本次全部改动
同一时间只允许一个任务: 运行锁在 common.busy 里, 载入文件与停用插件时经它取消正在运行的任务
"""

from typing import NamedTuple

import bpy
from bpy.app.handlers import persistent

from ..common import busy
from .operators import ToolOperator

# 定时器间隔, 秒; 每次定时器事件推进一步, 单步的耗时远大于它
TIMER_INTERVAL = 0.001

# 任务进行期间仍然放行的事件: 只允许旋转, 平移与缩放视图, 其余输入在任务结束前被拦下
NAVIGATION_EVENTS = frozenset(
    {
        "MIDDLEMOUSE",
        "WHEELUPMOUSE",
        "WHEELDOWNMOUSE",
        "MOUSEMOVE",
        "INBETWEEN_MOUSEMOVE",
        "TRACKPADPAN",
        "TRACKPADZOOM",
        "MOUSEROTATE",
        "NDOF_MOTION",
    }
)


class Progress(NamedTuple):
    """任务的进度: 已完成的步数与总步数"""

    # 已完成的步数
    done: int
    # 总步数
    total: int


@persistent
def _abort_on_load(_scene, _path=None):
    """载入文件前取消正在运行的任务, 它持有的数据即将被替换

    _scene Blender 传入的场景, 不使用
    _path Blender 传入的文件路径, 不使用
    """
    busy.abort_all()


def _remove_load_handler():
    """移除载入回调; 不在列表里时什么也不做"""
    # 重复移除会抛 ValueError, 先判断是否还在列表里
    if _abort_on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_abort_on_load)


class LongTaskOperator(ToolOperator):
    """可取消的模态长任务 - 子类实现 begin, report_text, progress_text 与 cancelled_text

    begin 读完全部输入并返回任务的生成器, 输入不满足时抛出领域错误
    运行期间的状态都在 invoke 里建立, 成员如下:
        _steps 任务的生成器, 还没有开始或已释放时为 None
        _timer 推进用的定时器, 没有时为 None
        _manager 窗口管理器, 释放进度条与定时器时用
        _workspace 工作区, 释放状态栏文字时用, 可以为 None
    """

    @classmethod
    def unregister(cls):
        """停用插件时取消正在运行的任务

        Blender 注销算子类时直接释放模态处理器而不调用 cancel
        所以由运行锁的取消函数释放界面资源, 载入回调在这里移除
        """
        busy.abort_all()
        _remove_load_handler()

    def begin(self, context):
        """读取输入, 返回任务的生成器; 子类实现

        context Blender 上下文
        返回 生成器, 每次产出 Progress, 返回值是任务结果
        """
        raise NotImplementedError

    def report_text(self, result):
        """任务完成的报告文字; 子类实现

        result 生成器的返回值
        返回 已翻译的报告文字
        """
        raise NotImplementedError

    def progress_text(self, progress):
        """状态栏里的进度文字; 子类实现

        progress 当前进度
        返回 已翻译的文字
        """
        raise NotImplementedError

    def cancelled_text(self):
        """取消后的报告文字; 子类实现

        返回 已翻译的报告文字
        """
        raise NotImplementedError

    def run(self, context):
        """脚本调用: 同步执行到底

        context Blender 上下文
        返回 报告文字
        """
        steps = self.begin(context)
        # 同步执行也要持有运行锁, 期间其他入口被拒绝
        with busy.holding():
            while True:
                try:
                    next(steps)
                except StopIteration as stop:
                    return self.report_text(stop.value)

    def invoke(self, context, _event):
        """界面调用: 先推进第一步, 校验错误立即报告; 之后由定时器逐步推进

        context Blender 上下文
        _event 触发事件, 不使用
        返回 {"RUNNING_MODAL"} 进入模态, 出错时 {"CANCELLED"}
        """
        self._steps = None
        self._timer = None
        # 取消可能来自载入回调或注销, 那时拿不到算子的上下文, 先记下要释放的界面资源的归属
        self._manager = context.window_manager
        self._workspace = context.workspace
        try:
            # 输入先读完再拿运行锁: 读取失败时没有锁需要释放
            steps = self.begin(context)
            busy.claim(self._abort)
        except Exception as error:
            return self._fail(error)
        self._steps = steps
        # 第一步里完成输入校验: 校验错误在这里立即抛出, 不必进入模态
        try:
            progress = next(self._steps)
        except Exception as error:
            self._release()
            return self._fail(error)
        manager = self._manager
        self._timer = manager.event_timer_add(TIMER_INTERVAL, window=context.window)
        manager.modal_handler_add(self)
        manager.progress_begin(0, progress.total)
        bpy.app.handlers.load_pre.append(_abort_on_load)
        self._show(progress)
        return {"RUNNING_MODAL"}

    def modal(self, _context, event):
        """Esc 取消; 定时器推进一步; 视图导航放行; 其余输入在任务结束前拦下

        _context Blender 上下文, 不使用
        event 当前事件
        返回 算子返回集合
        """
        if event.type == "ESC" and event.value == "PRESS":
            return self._cancel_task()
        if event.type == "TIMER":
            return self._advance()
        # 视图旋转, 平移与缩放放行, 用户仍然可以从不同角度观察推进过程
        if event.type in NAVIGATION_EVENTS:
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    def cancel(self, _context):
        """Blender 从外部取消模态任务时同样撤回, 例如载入文件时移除旧窗口的处理器

        _context Blender 上下文, 不使用
        """
        self._cancel_task()

    def _advance(self):
        """推进一步; 结束时报告结果, 出错时报告原因, 场景已由生成器恢复

        返回 算子返回集合
        """
        try:
            progress = next(self._steps)
        except StopIteration as stop:
            self._release()
            # 正常结束的任务一定有返回值, 没有说明生成器是被外部关闭的
            if stop.value is None:
                # 生成器已被外部关闭, 例如载入文件
                return {"CANCELLED"}
            self.report({"INFO"}, self.report_text(stop.value))
            return {"FINISHED"}
        except Exception as error:
            self._release()
            return self._fail(error)
        self._show(progress)
        return {"RUNNING_MODAL"}

    def _cancel_task(self):
        """关闭生成器触发撤回, 释放资源并报告

        返回 {"CANCELLED"}
        """
        # 关闭生成器会在它内部抛出 GeneratorExit, 生成器的收尾据此撤回本次全部改动
        if self._steps is not None:
            self._steps.close()
        self._release()
        self.report({"WARNING"}, self.cancelled_text())
        return {"CANCELLED"}

    def _abort(self):
        """由运行锁调用的取消, 用于载入文件与停用插件: 关闭生成器并释放计时器, 进度条, 状态栏与运行锁

        载入回调不在这里移除: 载入文件时它正在 load_pre 列表的遍历中执行,
        删除自己会让下一个回调被跳过
        """
        if self._steps is not None:
            self._steps.close()
        self._release_interface()

    def _show(self, progress):
        """更新进度条与状态栏文字

        progress 当前进度
        """
        self._manager.progress_update(progress.done)
        # 工作区可能已不存在 (窗口被关闭), 此时不更新状态栏
        if self._workspace is not None:
            self._workspace.status_text_set(self.progress_text(progress))

    def _release_interface(self):
        """释放定时器, 进度条, 状态栏与运行锁; 可以重复调用"""
        # 只有定时器存在时才有进度条需要结束
        if self._timer is not None:
            self._manager.event_timer_remove(self._timer)
            self._timer = None
            self._manager.progress_end()
        if self._workspace is not None:
            self._workspace.status_text_set(None)
        busy.release(self._abort)

    def _release(self):
        """释放全部资源, 包括载入回调; 可以重复调用"""
        self._release_interface()
        _remove_load_handler()

    def _fail(self, error):
        """报告失败, 与 ToolOperator.execute 的报告格式一致

        error 捕获到的异常
        返回 {"CANCELLED"}
        """
        return self.fail(error)
