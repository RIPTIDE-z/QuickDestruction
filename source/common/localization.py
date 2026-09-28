"""翻译目录 - 读取各模块的语言文件并合并成 Blender 翻译字典, 坏文件与坏条目只告警跳过, 不依赖 Blender

语言文件格式: {"locale": 语言, "interface": {英文: 译文}, "tooltips": {英文: 译文}}
interface 注册到模块自己的上下文, tooltips 是 Blender 在默认上下文里查找的悬停说明
"""

import json
from collections import Counter
from pathlib import Path
from string import Formatter

# Blender 的默认翻译上下文
DEFAULT_CONTEXT = "*"


def format_fields(message):
    """占位符的名称, 格式与转换符计数 - 译文可以调整顺序但不能增删或改写占位符"""
    return Counter(
        (field, spec, conversion)
        for _text, field, spec, conversion in Formatter().parse(message)
        if field is not None
    )


def read_catalog(path):
    """读取一个语言文件, 返回 (语言, interface, tooltips); 结构不对时抛 ValueError"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("locale"), str):
        raise ValueError("expected an object with a locale")
    sections = [data.get(name, {}) for name in ("interface", "tooltips")]
    if not all(isinstance(section, dict) for section in sections):
        raise ValueError("interface and tooltips must be objects")
    return data["locale"], *sections


def usable(source, translated):
    """条目可用: 原文与译文都是非空字符串且占位符一致"""
    try:
        return (
            isinstance(source, str)
            and isinstance(translated, str)
            and bool(translated)
            and format_fields(source) == format_fields(translated)
        )
    except ValueError:
        # 花括号不成对时 Formatter 抛错, 这类条目同样不可用
        return False


def build_translations(modules):
    """合并各模块的语言文件; modules 是 (上下文, 语言目录) 序列, 返回 {语言: {(上下文, 原文): 译文}}"""
    result = {}
    for context, directory in modules:
        for path in sorted(Path(directory).glob("*.json")):
            try:
                locale, interface, tooltips = read_catalog(path)
            except (OSError, ValueError) as error:
                # 一个坏文件不能拖垮整个扩展, 缺失的译文回退到英文
                print(f"QuickDestruction: skipped translation file {path}: {error}")
                continue
            table = result.setdefault(locale, {})
            for target, section in ((context, interface), (DEFAULT_CONTEXT, tooltips)):
                for source, translated in section.items():
                    if usable(source, translated):
                        table[target, source] = translated
                    else:
                        print(f"QuickDestruction: skipped translation {source!r} in {path}")
    return result
