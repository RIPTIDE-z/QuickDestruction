"""预设信封 - 封装 format_version 与 tool_id, 读取时拒绝未知字段和其他工具的预设"""

from ..constants import TOOL_ID
from ..errors import MeshFractureError
from ..settings import PROPERTY_SPECS, validated_values

# 当前信封版本; 结构变化时递增, 旧版本文件会在 decode 里被明确拒绝
FORMAT_VERSION = 1
# 信封必须恰好是这三个键: 少一个键说明不是当前格式, 多一个键说明文件不属于本工具
ENVELOPE_KEYS = frozenset({"format_version", "tool_id", "values"})


def _checked_values(data):
    """验证本工具参数, 拒绝静默忽略未知字段"""
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    # 先查未知字段: 缺字段允许(保留界面现值), 未知字段一律拒绝;
    # 否则别的工具的参数表会被静默地部分应用
    unknown = set(data) - set(PROPERTY_SPECS)
    if unknown:
        raise MeshFractureError("Unknown preset parameter: {name}", name=min(unknown))
    # 再按规格校验类型, 有限性与范围
    validated_values(data)
    return data


def encode(data):
    """以工具 ID 和格式版本封装参数"""
    # 写入前先自检, 避免把非法参数写进文件
    return {
        "format_version": FORMAT_VERSION,
        "tool_id": TOOL_ID,
        "values": _checked_values(data),
    }


def decode(data):
    """先核对信封身份, 再返回通过校验的参数"""
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    # 键集合必须完全相等: 裸参数字典与多出键的文件都不是当前格式
    if set(data) != ENVELOPE_KEYS:
        raise MeshFractureError("Preset format is invalid")
    # 用 type 而不是 isinstance: 布尔 True 也不能被当成版本 1
    if type(data["format_version"]) is not int or data["format_version"] != FORMAT_VERSION:
        raise MeshFractureError("Unsupported preset format version")
    # 工具身份不符时给专门的消息, 便于用户判断是不是拿错了预设
    if data["tool_id"] != TOOL_ID:
        raise MeshFractureError("Preset belongs to another tool")
    return _checked_values(data["values"])
