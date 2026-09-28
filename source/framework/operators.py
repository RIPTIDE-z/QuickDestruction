"""算子约定 - 工具算子的公共执行流程, 把结果与异常转成可翻译的报告"""

import traceback

from ..common.errors import DomainError
from ..common.translate import tr_report
from .constants import CONTEXT


def failure_text(operator, error):
    """把异常换成报告文本: 领域错误按自己的上下文翻译, 其余异常原样展示"""
    if isinstance(error, DomainError):
        # 参数名这类界面术语先按各自的上下文翻译, 再填入消息
        values = {
            key: tr_report(error.terms[key], value) if key in error.terms else value
            for key, value in error.values.items()
        }
        detail = tr_report(error.context or CONTEXT, error.message, **values)
    else:
        detail = str(error)
    operation = tr_report(operator.bl_translation_context, operator.bl_label)
    return tr_report(CONTEXT, "{operation} failed: {detail}", operation=operation, detail=detail)


class ToolOperator:
    """工具算子的公共执行流程 - 子类实现 run, 这里把结果与异常转成可翻译的报告"""

    def execute(self, context):
        """执行 run 并报告结果, 失败时取消本次操作"""
        try:
            message = self.run(context)
        except Exception as error:
            # 领域错误是可预期的, 其余异常打印堆栈便于排查
            if not isinstance(error, DomainError):
                traceback.print_exc()
            self.report({"ERROR"}, failure_text(self, error))
            return {"CANCELLED"}
        if message:
            self.report({"INFO"}, message)
        return {"FINISHED"}
