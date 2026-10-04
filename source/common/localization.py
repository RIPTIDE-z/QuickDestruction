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
    """统计消息里的占位符, 译文可以调整顺序但不能增删或改写占位符

    message 含 {名字} 形式占位符的消息
    返回 Counter, 键是 (占位符名, 格式说明, 转换符), 值是出现次数
    """
    # field 为 None 的片段是纯文本, 不是占位符
    return Counter(
        (field, spec, conversion)
        for _text, field, spec, conversion in Formatter().parse(message)
        if field is not None
    )


def read_catalog(path):
    """读取一个语言文件

    path 语言文件路径
    返回 (语言, interface 字典, tooltips 字典); 结构不对时抛 ValueError
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    # 顶层必须是带 locale 字符串的对象
    if not isinstance(data, dict) or not isinstance(data.get("locale"), str):
        raise ValueError("expected an object with a locale")
    # 两个分组缺失时按空处理, 存在但不是对象才算结构错误
    sections = [data.get(name, {}) for name in ("interface", "tooltips")]
    if not all(isinstance(section, dict) for section in sections):
        raise ValueError("interface and tooltips must be objects")
    return data["locale"], *sections


def usable(source, translated):
    """判断一个条目能否使用: 原文与译文都是非空字符串且占位符一致

    source 英文原文
    translated 译文
    返回 可用为真
    """
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
    """合并各模块的语言文件, 坏文件与坏条目告警后跳过

    modules (上下文, 语言目录) 序列, 目录下每个 json 是一种语言
    返回 {语言: {(上下文, 原文): 译文}}
    """
    result = {}
    for context, directory in modules:
        # 排序后读取: 同一语言有多个文件时结果与文件系统的枚举顺序无关
        for path in sorted(Path(directory).glob("*.json")):
            try:
                locale, interface, tooltips = read_catalog(path)
            except (OSError, ValueError) as error:
                # 一个坏文件不能拖垮整个扩展, 缺失的译文回退到英文
                print(f"QuickDestruction: skipped translation file {path}: {error}")
                continue
            table = result.setdefault(locale, {})
            # interface 注册到本模块的上下文, tooltips 注册到 Blender 查悬停说明的默认上下文
            for target, section in ((context, interface), (DEFAULT_CONTEXT, tooltips)):
                for source, translated in section.items():
                    if usable(source, translated):
                        table[target, source] = translated
                    else:
                        # 占位符对不上的译文会在运行时让 format 抛错, 宁可回退英文
                        print(f"QuickDestruction: skipped translation {source!r} in {path}")
    return result
