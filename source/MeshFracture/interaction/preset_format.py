"""MeshFracture 预设格式及改名前文件的读取兼容。"""

from ..constants import TOOL_ID
from ..errors import FractureError
from ..settings import PROPERTY_SPECS, validated_values

FORMAT_VERSION = 1
ENVELOPE_KEYS = frozenset({"format_version", "tool_id", "values"})
PREVIOUS_TOOL_ID = "cell_fracture"
RETIRED_FIELDS = frozenset({"mass_mode", "mass"})


def _checked_values(data, *, previous=False):
    """旧预设只丢弃已移除的占位字段, 其余未知字段仍报错。"""
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    if previous:
        data = {key: value for key, value in data.items() if key not in RETIRED_FIELDS}
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


def decode(data, *, legacy=False, previous=False):
    """检查工具身份, 并读取两种旧目录中的有效参数。"""
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    if set(data) == ENVELOPE_KEYS:
        if type(data["format_version"]) is not int or data["format_version"] != FORMAT_VERSION:
            raise FractureError("Unsupported preset format version")
        if data["tool_id"] == PREVIOUS_TOOL_ID and previous:
            return _checked_values(data["values"], previous=True)
        if data["tool_id"] != TOOL_ID:
            raise FractureError("Preset belongs to another tool")
        return _checked_values(data["values"])
    if not (legacy or previous) or set(data) & ENVELOPE_KEYS:
        raise FractureError("Preset format is invalid")
    return _checked_values(data, previous=True)
