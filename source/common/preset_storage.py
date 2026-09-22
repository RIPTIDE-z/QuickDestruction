"""目录由调用者提供的 JSON 预设存储"""
import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata

def normalize_name(name):
    """把用户输入整理成安全的文件名, 拒绝空名/Windows 保留名与超长名"""
    name = unicodedata.normalize('NFC', name.strip())
    # 路径分隔符与控制字符一律替换成下划线
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip('. ')
    if not name:
        raise ValueError("Preset name is empty")
    stem = name.split('.')[0]
    if re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', stem):
        raise ValueError("This preset name is reserved by Windows")
    if len(name) > 120:
        raise ValueError("Preset name is too long")
    return name

def preset_filepath(directory, name):
    """定位预设文件, 并确认它没有经由符号链接跑到目录之外"""
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
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.qd-', suffix='.tmp', delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temp_path, path)
        else:
            # 用硬链接发布: 目标已存在时会失败, 因此不会覆盖别人的同名预设
            os.link(temp_path, path)
        return path.stem
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)

def list_presets(directory):
    """列出普通 JSON 文件 - 符号链接不作为预设展示"""
    return sorted((path.stem for path in Path(directory).glob("*.json")
                   if path.is_file() and not path.is_symlink()), key=str.casefold)


def read_preset(path):
    """按原大小上限解析 JSON - 业务字段由调用者验证"""
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Preset file is too large")
    with path.open(encoding='utf-8') as handle:
        return json.load(handle)
