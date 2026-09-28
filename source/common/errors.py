"""通用异常 - 领域错误基类, 参数校验异常与非有限坐标检查"""

import math

# 参数校验失败时的消息, 参数名在报告时按参数规格的上下文翻译
INVALID_PARAMETER_MESSAGE = "Invalid parameter: {name}"


class DomainError(Exception):
    """领域错误基类 - 只保存英文消息键与插值参数, 翻译交给界面层"""

    # 消息所属的翻译上下文, 由各工具的子类给出; None 表示按框架上下文翻译
    context = None

    def __init__(self, message, **values):
        """记录消息键与插值参数"""
        super().__init__(message)
        # 英文消息键, 界面层先按 context 取译文再填入 values
        self.message = message
        # 插值参数
        self.values = values
        # 需要先翻译再填入消息的插值参数: 参数名 -> 翻译上下文
        self.terms = {}


class InvalidParameterError(DomainError):
    """参数值不合法或字段未知 - 记录字段名, 参数名在报告时按参数规格的上下文翻译"""

    def __init__(self, field, label, label_context):
        """记录出错字段, 它的英文参数名与参数名所属的翻译上下文"""
        super().__init__(INVALID_PARAMETER_MESSAGE, name=label)
        # 规格里的字段名
        self.field = field
        self.terms = {"name": label_context}


def require_finite(point_groups, error_type, message):
    """任一组坐标出现非有限值时抛出 error_type(message)"""
    if any(
        not math.isfinite(value) for group in point_groups for point in group for value in point
    ):
        raise error_type(message)
