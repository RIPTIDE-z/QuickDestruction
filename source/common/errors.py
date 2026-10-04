"""通用异常 - 领域错误基类, 参数校验异常与非有限坐标检查"""

import math

# 参数校验失败时的消息, 参数名在报告时按参数规格的上下文翻译
INVALID_PARAMETER_MESSAGE = "Invalid parameter: {name}"


class DomainError(Exception):
    """领域错误基类 - 只保存英文消息键与插值参数, 翻译交给界面层"""

    # 消息所属的翻译上下文, 由各工具的子类给出; None 表示按框架上下文翻译
    context = None

    def __init__(self, message, **values):
        """记录消息键与插值参数

        message 英文消息键, 可含 {名字} 形式的占位符
        values 占位符的取值, 键与占位符名字一一对应
        """
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
        """记录出错字段, 它的英文参数名与参数名所属的翻译上下文

        field 规格里的字段名
        label 字段的英文显示名, 填入消息的 {name}
        label_context 显示名所属的翻译上下文, 报告时据此先翻译显示名
        """
        super().__init__(INVALID_PARAMETER_MESSAGE, name=label)
        # 规格里的字段名
        self.field = field
        # 显示名需要先翻译再填进消息
        self.terms = {"name": label_context}


def require_finite(point_groups, error_type, message):
    """要求全部坐标都是有限值, 出现 NaN 或无穷时抛出 error_type(message)

    point_groups 多组坐标, 每组是若干个点, 每个点是若干个数
    error_type 出错时抛出的异常类型
    message 异常的消息
    """
    # 逐个数值检查: 一个非有限坐标就会让后面的布尔与求交结果不可预期
    if any(
        not math.isfinite(value) for group in point_groups for point in group for value in point
    ):
        raise error_type(message)
