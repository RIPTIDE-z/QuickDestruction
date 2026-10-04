"""预设文件 - 文件名净化, 大小限制与原子写入, 以及带工具标识与格式版本的信封, 不依赖 Blender"""

import json
import re
import tempfile
import unicodedata
from pathlib import Path

from ..common.errors import DomainError
from .constants import CONTEXT

# 信封格式版本; 结构变化时递增, 版本不符的文件会被明确拒绝
FORMAT_VERSION = 1
# 信封必须恰好是这三个键
ENVELOPE_KEYS = frozenset({"format_version", "tool_id", "values"})
# 预设文件的大小上限, 先看大小再解析, 避免超大文件占满内存
MAX_SIZE = 1024 * 1024


class PresetError(DomainError):
    """预设文件的名称, 格式或归属错误"""

    # 消息按框架上下文翻译
    context = CONTEXT


def normalize_name(name):
    """把用户输入整理成安全的文件名, 拒绝空名, Windows 保留名与超长名

    name 用户输入的预设名
    返回 净化后的名字; 不合法时抛 PresetError
    """
    # NFC 让同形不同码点的名字在文件系统上表现一致
    name = unicodedata.normalize("NFC", name.strip())
    # 路径分隔符与控制字符替换成下划线, 因此名字不可能越出预设目录
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(". ")
    if not name:
        raise PresetError("Preset name is empty")
    # Windows 的设备名 (含带扩展名的形式) 不能当文件名用
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", name.split(".")[0]):
        raise PresetError("This preset name is reserved by Windows")
    # 留出 .json 后缀与目录路径的余量
    if len(name) > 120:
        raise PresetError("Preset name is too long")
    return name


def preset_filepath(directory, name):
    """预设名对应的文件路径

    directory 预设目录
    name 预设名, 会先被净化
    返回 目录下的 .json 文件路径
    """
    return Path(directory) / (normalize_name(name) + ".json")


def save_preset(directory, name, data, overwrite=False):
    """先写同目录的临时文件再整体替换, 中途失败不会留下半个预设

    directory 预设目录, 必须已经存在
    name 预设名
    data 可 JSON 化的数据, 通常是带信封的参数
    overwrite 为真时允许替换同名预设
    返回 实际的预设名 (净化之后)
    """
    path = preset_filepath(directory, name)
    if path.exists() and not overwrite:
        raise PresetError("Preset already exists; enable Overwrite to replace it")
    temp = None
    try:
        # 临时文件放在目标同一目录: 同一文件系统上的替换才是原子的
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=path.parent, prefix=".qd-", suffix=".tmp", delete=False
        ) as handle:
            temp = Path(handle.name)
            # ensure_ascii=False 保留中文; allow_nan=False 拒绝非法 JSON
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
        temp.replace(path)
    finally:
        # 替换成功后临时文件已经不存在, 失败时在这里清掉
        if temp is not None:
            temp.unlink(missing_ok=True)
    return path.stem


def list_presets(directory):
    """列出目录里的预设名

    directory 预设目录
    返回 按不区分大小写排序的预设名列表
    """
    return sorted(
        (path.stem for path in Path(directory).glob("*.json") if path.is_file()),
        key=str.casefold,
    )


def read_preset(path):
    """按大小上限读取并解析 JSON, 只当数据解析, 字段由调用方校验

    文件缺失或读不出来, 编码或 JSON 不合法都转成 PresetError, 报告不会露出系统与解析器的原始消息
    path 预设文件路径
    返回 解析出的数据
    """
    try:
        # 先看大小再读内容, 超大文件不会占满内存
        if path.stat().st_size > MAX_SIZE:
            raise PresetError("Preset file is too large")
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PresetError("Preset file could not be read") from error
    except ValueError as error:
        # JSON 语法错误与非 UTF-8 编码分别是 ValueError 的子类 JSONDecodeError 与 UnicodeDecodeError
        raise PresetError("Preset format is invalid") from error


def remove_preset(path):
    """删除预设文件; 文件已不存在或无法删除时转成 PresetError

    path 预设文件路径
    """
    try:
        path.unlink()
    except OSError as error:
        raise PresetError("Preset file could not be removed") from error


def encode(tool_id, data):
    """以工具标识与格式版本封装参数

    tool_id 工具标识
    data 数据形态的参数字典
    返回 信封字典
    """
    return {"format_version": FORMAT_VERSION, "tool_id": tool_id, "values": data}


def decode(tool_id, data):
    """核对信封的结构, 版本与工具标识; 字段校验在写回时进行

    tool_id 当前工具的标识, 预设必须属于它
    data 从文件解析出的数据
    返回 信封里的参数字典; 不合法时抛 PresetError
    """
    # 结构: 必须是恰好三个键的字典, 且 values 是字典
    if (
        not isinstance(data, dict)
        or set(data) != ENVELOPE_KEYS
        or not isinstance(data["values"], dict)
    ):
        raise PresetError("Preset format is invalid")
    # 用 type 而不是 isinstance: 布尔 True 不能当成版本 1
    if type(data["format_version"]) is not int or data["format_version"] != FORMAT_VERSION:
        raise PresetError("Unsupported preset format version")
    # 一个工具的预设不能载入到另一个工具
    if data["tool_id"] != tool_id:
        raise PresetError("Preset belongs to another tool")
    return data["values"]
