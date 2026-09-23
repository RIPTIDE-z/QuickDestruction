"""通用异常与校验 - 领域错误基类, 字段校验异常, 非有限坐标检查与错误描述"""

import math


class DomainError(Exception):
    """领域错误基类 - 只保存消息键与插值参数, 翻译交给界面层"""

    def __init__(self, message, **values):
        """记录消息键与插值参数, 实际文案留给界面层翻译"""
        # message 是英文消息键, 界面层先查译文再把 values 填进去
        super().__init__(message)
        self.message = message
        self.values = values


class PresetError(DomainError):
    """预设信封的格式或归属错误 - 由通用预设层抛出, 全部工具共用"""


class InvalidParameterError(ValueError):
    """保留 ValueError 兼容性,同时明确指出出错字段"""

    def __init__(self, field):
        """记录出错字段名, 同时保留 ValueError 的字符串内容"""
        # 继承 ValueError, 让既有的 except ValueError 分支继续生效
        super().__init__(field)
        # field 是规格里的字段名, 界面层据此取出参数标签再展示
        self.field = field


def require_finite(point_groups, error_type, message):
    """任一组坐标出现非有限值时抛出 error_type(message)

    point_groups 是若干组点, 每组点的元素是数值序列;
    异常类型由调用方给出, 因此检查只有一份而各工具的异常层次保持独立
    """
    if any(
        not math.isfinite(value) for group in point_groups for point in group for value in point
    ):
        raise error_type(message)


def describe_error(error, specs):
    """把异常换成 消息键 + 插值参数, 供界面层取译文

    specs 是该工具的参数规格, 用于把出错字段名换成界面标签
    """
    if isinstance(error, InvalidParameterError):
        # 校验错误只带字段名, 这里换成界面标签再交给消息模板
        label = specs.get(error.field, ("", {"name": error.field}))[1]["name"]
        return "Invalid parameter: {name}", {"name": label}
    if isinstance(error, DomainError):
        # 领域错误已经保存了消息键与插值参数, 直接透传
        return error.message, dict(error.values)
    # 其余异常按原文展示
    return str(error), {}
