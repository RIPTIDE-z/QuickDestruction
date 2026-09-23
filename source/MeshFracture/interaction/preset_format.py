"""预设信封 - 封装 format_version 与 tool_id, 读取时拒绝未知字段和其他工具的预设"""

from ..constants import TOOL_ID
from ..errors import MeshFractureError
from ..settings import PROPERTY_SPECS, validated_values

FORMAT_VERSION = 1
ENVELOPE_KEYS = frozenset({"format_version", "tool_id", "values"})


def _checked_values(data):
    """验证本工具参数, 拒绝静默忽略未知字段"""
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    unknown = set(data) - set(PROPERTY_SPECS)
    if unknown:
        raise MeshFractureError("Unknown preset parameter: {name}", name=min(unknown))
    validated_values(data)
    return data


def encode(data):
    """以工具 ID 和格式版本封装参数"""
    return {
        "format_version": FORMAT_VERSION,
        "tool_id": TOOL_ID,
        "values": _checked_values(data),
    }


def decode(data):
    """先核对信封身份, 再返回通过校验的参数"""
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    if set(data) != ENVELOPE_KEYS:
        raise MeshFractureError("Preset format is invalid")
    if type(data["format_version"]) is not int or data["format_version"] != FORMAT_VERSION:
        raise MeshFractureError("Unsupported preset format version")
    if data["tool_id"] != TOOL_ID:
        raise MeshFractureError("Preset belongs to another tool")
    return _checked_values(data["values"])
