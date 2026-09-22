"""错误呈现适配 - 将字段标识转换为界面标签"""
from ..common.errors import InvalidParameterError
from .errors import FractureError
from .settings import field_label


def describe_error(error):
    """将字段异常转换为标签 - 领域异常保留消息键和参数"""
    if isinstance(error, InvalidParameterError):
        return "Invalid parameter: {name}", {"name": field_label(error.field)}
    if isinstance(error, FractureError):
        return error.message, dict(error.values)
    return str(error), {}
