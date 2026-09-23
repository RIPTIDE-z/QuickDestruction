"""预设存储 - 目录由调用者提供, 负责文件名净化, 大小限制与原子写入"""

import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path


def normalize_name(name):
    """把用户输入整理成安全的文件名, 拒绝空名/Windows 保留名与超长名"""
    # 统一成 NFC, 避免同形不同码点的名字在文件系统上表现不一致
    name = unicodedata.normalize("NFC", name.strip())
    # 路径分隔符与控制字符一律替换成下划线, 顺手去掉首尾的点与空格
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip(". ")
    # 替换之后可能变成空串或只剩点号
    if not name:
        raise ValueError("Preset name is empty")
    # 主名部分不能是 Windows 保留设备名
    stem = name.split(".")[0]
    if re.fullmatch(r"(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])", stem):
        raise ValueError("This preset name is reserved by Windows")
    # 留出 .json 后缀与目录路径的余量
    if len(name) > 120:
        raise ValueError("Preset name is too long")
    return name


def preset_filepath(directory, name):
    """定位预设文件, 并确认它没有经由符号链接跑到目录之外"""
    # 比较 resolve 之后的父目录: 符号链接与 ".." 都会在这里被识别出来
    directory = Path(directory).resolve()
    path = directory / (normalize_name(name) + ".json")
    if path.is_symlink() or path.resolve().parent != directory:
        raise ValueError("Preset path is outside the preset directory")
    return path


def save_preset(directory, name, data, overwrite=False):
    """先写临时文件再原子发布, 避免中途失败留下半个预设"""
    path = preset_filepath(directory, name)
    if path.exists() and not overwrite:
        raise FileExistsError(path.name)
    temp_path = None
    try:
        # 临时文件与目标同目录: 同一文件系统上的覆盖与硬链接才是原子操作
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".qd-", suffix=".tmp", delete=False
        ) as handle:
            temp_path = Path(handle.name)
            # ensure_ascii=False 保留中文; allow_nan=False 拒绝 NaN 与 Infinity 这类非法 JSON
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write("\n")
            handle.flush()
            # 先落盘再发布, 避免断电后留下内容为空的预设
            os.fsync(handle.fileno())
        if overwrite:
            # Path.replace 与 os.replace 等价, 都是原子覆盖
            temp_path.replace(path)
        else:
            # 用硬链接发布: 目标已存在时会失败, 因此不会覆盖别人的同名预设
            os.link(temp_path, path)
        return path.stem
    finally:
        # 发布后临时文件只是硬链接的另一个名字, unlink 仅去掉多余的名字
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def list_presets(directory):
    """列出普通 JSON 文件 - 符号链接不作为预设展示"""
    # casefold 排序让大小写不同的名字有稳定顺序
    return sorted(
        (
            path.stem
            for path in Path(directory).glob("*.json")
            if path.is_file() and not path.is_symlink()
        ),
        key=str.casefold,
    )


def read_preset(path):
    """按原大小上限解析 JSON - 业务字段由调用者验证"""
    # 先看大小再解析, 避免超大文件占满内存
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Preset file is too large")
    # 只当数据解析, 不执行其中任何内容
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)
