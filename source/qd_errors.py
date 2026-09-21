"""可翻译的业务错误与参数校验错误，不依赖 Blender 状态。"""


class FractureError(Exception):
    """message 为原文消息键，values 为翻译后再填入的参数。"""

    def __init__(self, message, **values):
        super().__init__(message)
        self.message = message
        self.values = values


class InvalidParameterError(ValueError):
    """保留 ValueError 兼容性，同时明确指出出错字段。"""

    def __init__(self, field):
        super().__init__(field)
        self.field = field
