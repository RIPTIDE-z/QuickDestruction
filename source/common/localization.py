"""翻译目录构建 - 由参数规格生成稳定字段路径, 校验语言 JSON 并展开上下文, 不依赖 Blender"""

import json
from collections import Counter
from pathlib import Path
from string import Formatter


def _format_fields(message):
    """记录占位符名称及格式 - 翻译可调整顺序但不可改变参数契约"""
    # 用 Counter 而不是 set: 同一占位符出现两次, 译文也必须出现两次
    return Counter(
        (field, spec, conversion)
        for _text, field, spec, conversion in Formatter().parse(message)
        if field is not None
    )


def merge_messages(*groups):
    """合并消息并拒绝冲突与无效占位符 - 相同译文可重复声明"""
    messages = {}
    for group in groups:
        for source, translated in group.items():
            # 原文与译文都必须是字符串, 且译文不能为空
            if not isinstance(source, str) or not isinstance(translated, str) or not translated:
                raise ValueError(f"Invalid translation entry: {source!r}")
            # 对比占位符集合: 译文可以换顺序, 但不能增删占位符或改格式规格
            if _format_fields(source) != _format_fields(translated):
                raise ValueError(f"Translation placeholders differ: {source!r}")
            # 同一原文在不同来源给出不同译文时报错, 相同则允许重复声明
            if source in messages and messages[source] != translated:
                raise ValueError(f"Conflicting translations: {source!r}")
            messages[source] = translated
    return messages


def property_sources(specs):
    """从参数规格生成稳定字段路径到英文原文的映射"""
    sources = {}
    for field, (_kind, options) in specs.items():
        # 普通字段用 字段.name 与 字段.description 作为稳定路径
        for attribute in ("name", "description"):
            if attribute in options:
                key = f"{field}.{attribute}"
                if key in sources:
                    raise ValueError(f"Duplicate translation property path: {key}")
                sources[key] = options[attribute]
        # 枚举项再加一层 .items.<标识>, 因此调整枚举顺序不会改变译文归属
        for identifier, name, description, *_rest in options.get("items", ()):
            for attribute, source in (("name", name), ("description", description)):
                key = f"{field}.items.{identifier}.{attribute}"
                if key in sources:
                    raise ValueError(f"Duplicate translation property path: {key}")
                sources[key] = source
    return sources


def property_messages(specs, localized):
    """用稳定字段路径关联译文, 英文消息键仍由参数规格提供"""
    if not isinstance(localized, dict):
        raise TypeError("Translation properties must be an object")
    sources = property_sources(specs)
    # 键集合必须完全一致: 参数漏译与多译都要在加载时暴露
    if set(sources) != set(localized):
        missing = sorted(set(sources) - set(localized))
        extra = sorted(set(localized) - set(sources))
        raise ValueError(f"Translation property keys differ: missing={missing}, extra={extra}")
    # 把 路径到译文 翻转成 英文原文到译文, 再交给 merge_messages 统一校验
    return merge_messages(*({source: localized[key]} for key, source in sources.items()))


def _unique_object(pairs):
    """JSON 默认会静默覆盖重复键; 翻译文件必须拒绝这种输入"""
    result = {}
    for key, value in pairs:
        # object_pairs_hook 能拿到解析顺序, 因此可以识别重复键
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key!r}")
        result[key] = value
    return result


def load_catalogs(directory, modules):
    """从每种语言一个 JSON 文件构建现有的业务模块目录"""
    # 文件名即语言标识, 排序保证加载顺序稳定
    paths = sorted(Path(directory).glob("*.json"))
    if not paths:
        raise ValueError(f"No translation JSON files in {directory}")

    catalogs = {module: {} for module in modules}
    for path in paths:
        try:
            # object_pairs_hook 用于拒绝重复键; 读取失败统一包成 ValueError
            data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
        except (OSError, UnicodeError, ValueError) as error:
            raise ValueError(f"{path}: {error}") from error

        # 顶层结构固定为 版本, 语言, 模块
        if not isinstance(data, dict) or set(data) != {"schema_version", "locale", "modules"}:
            raise ValueError(f"{path}: expected schema_version, locale and modules")
        if type(data["schema_version"]) is not int or data["schema_version"] != 2:
            raise ValueError(f"{path}: unsupported translation schema version")
        # locale 必须与文件名一致, 否则无法判断文件对应哪种语言
        locale = data["locale"]
        if not isinstance(locale, str) or locale != path.stem:
            raise ValueError(f"{path}: locale must match the filename")
        # 模块集合必须与代码登记的一致: 新增工具时每种语言都要补齐分组
        sections = data["modules"]
        if not isinstance(sections, dict) or set(sections) != set(modules):
            raise ValueError(f"{path}: translation modules differ from registered modules")

        for module, (_contexts, specs) in modules.items():
            section = sections[module]
            # 每个模块只允许 properties 与 messages 两个分组
            if not isinstance(section, dict) or set(section) != {"properties", "messages"}:
                raise ValueError(f"{path}/{module}: expected properties and messages")
            try:
                if not isinstance(section["messages"], dict):
                    raise TypeError("messages must be an object")
                # 参数译文按规格逐项校验, 普通消息允许少译; 合并后才是该语言的模块目录
                catalogs[module][locale] = merge_messages(
                    property_messages(specs, section["properties"]), section["messages"]
                )
            except (TypeError, ValueError) as error:
                raise ValueError(f"{path}/{module}: {error}") from error

    # 输出保持模块登记顺序, 上下文元组原样带出供 build_translations 展开
    return tuple(
        (module, contexts, catalogs[module]) for module, (contexts, _specs) in modules.items()
    )


def build_translations(catalogs, *, aliases=None):
    """合并各模块语言目录并展开上下文 - 别名必须指向已有语言"""
    result = {}
    for module, contexts, languages in catalogs:
        for language, messages in languages.items():
            try:
                # 再合并一次: 跨模块的同名原文若译文不同, 在这里暴露
                validated = merge_messages(messages)
                # 同一原文要登记到该工具的每个上下文下, 字典键是 (上下文, 原文)
                target = result.setdefault(language, {})
                for context in contexts:
                    for source, translated in validated.items():
                        key = (context, source)
                        if key in target and target[key] != translated:
                            raise ValueError(f"Conflicting context translation: {key!r}")
                        target[key] = translated
            except ValueError as error:
                raise ValueError(f"{module}/{language}: {error}") from error
    # 语言别名复制目录 - 避免一个语言的后续修改影响另一个
    for alias, language in (aliases or {}).items():
        # 别名不能覆盖已有语言, 且必须指向已经加载的语言
        if alias in result or language not in result:
            raise ValueError(f"Invalid translation locale alias: {alias} -> {language}")
        result[alias] = dict(result[language])
    return result
