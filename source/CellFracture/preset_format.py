"""CellFracture 预设的数据格式和工具身份校验。"""

from .constants import TOOL_ID
from .errors import FractureError
from .settings import PROPERTY_SPECS, validated_values

FORMAT_VERSION = 1
ENVELOPE_KEYS = frozenset({"format_version", "tool_id", "values"})


def _checked_values(data):
    """允许旧文件缺字段, 但拒绝不属于本工具的字段。"""
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    unknown = set(data) - set(PROPERTY_SPECS)
    if unknown:
        raise FractureError("Unknown preset parameter: {name}", name=min(unknown))
    validated_values(data)
    return data


def encode(data):
    """将本工具参数写入带版本与工具 ID 的预设信封。"""
    return {
        "format_version": FORMAT_VERSION,
        "tool_id": TOOL_ID,
        "values": _checked_values(data),
    }


def decode(data, *, legacy=False):
    """检查文件身份; 无信封格式仅允许从旧目录读取。"""
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    if set(data) == ENVELOPE_KEYS:
        if type(data["format_version"]) is not int or data["format_version"] != FORMAT_VERSION:
            raise FractureError("Unsupported preset format version")
        if data["tool_id"] != TOOL_ID:
            raise FractureError("Preset belongs to another tool")
        return _checked_values(data["values"])
    if not legacy or set(data) & ENVELOPE_KEYS:
        raise FractureError("Preset format is invalid")
    return _checked_values(data)
