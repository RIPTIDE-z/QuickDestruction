"""通用异常 - 领域错误基类, 参数校验异常与非有限坐标检查"""

import math


class DomainError(Exception):
    """领域错误基类 - 只保存英文消息键与插值参数, 翻译交给界面层"""

    # 消息所属的翻译上下文, 由各工具的子类给出
    context = None

    def __init__(self, message, **values):
        """记录消息键与插值参数"""
        super().__init__(message)
        # 英文消息键, 界面层先按 context 取译文再填入 values
        self.message = message
        # 插值参数
        self.values = values


class InvalidParameterError(ValueError):
    """参数值不合法或字段未知 - 只记录字段名, 界面层再换成参数名"""

    def __init__(self, field):
        """记录出错字段名"""
        super().__init__(field)
        # 规格里的字段名
        self.field = field


def require_finite(point_groups, error_type, message):
    """任一组坐标出现非有限值时抛出 error_type(message)"""
    if any(
        not math.isfinite(value) for group in point_groups for point in group for value in point
    ):
        raise error_type(message)
