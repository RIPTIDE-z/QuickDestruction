"""可翻译的业务错误与参数校验错误，不依赖 Blender 状态。"""

from .qd_properties import field_label


class FractureError(Exception):
    """message 为原文消息键，values 为翻译后再填入的参数。

    与 InvalidParameterError.field 的区别: message 是**消息原文**, 显示前必须
    经翻译函数; field 是**字段标识**, 显示前必须换成界面标签。两者属性名不同
    (message / field), 且都由 describe_error() 统一处理, 不要直接取用。
    """

    def __init__(self, message, **values):
        super().__init__(message)
        self.message = message
        self.values = values


class InvalidParameterError(ValueError):
    """保留 ValueError 兼容性，同时明确指出出错字段。"""

    def __init__(self, field):
        super().__init__(field)
        self.field = field


def describe_error(error):
    """把异常转成可直接交给翻译函数的文本。

    参数校验错误抛的是字段标识，这里换成界面上的参数名再拼上固定前缀；
    其它异常沿用自身消息。调用方只需 translate(describe_error(error))，
    不必各自判断异常类型 —— 这正是 QD-07 那类"把字段标识当消息显示"的根因。
    """
    if isinstance(error, InvalidParameterError):
        return "Invalid parameter: {name}", {"name": field_label(error.field)}
    if isinstance(error, FractureError):
        return error.message, dict(error.values)
    return str(error), {}
