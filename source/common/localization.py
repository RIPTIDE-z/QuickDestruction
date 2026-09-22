"""纯数据翻译目录构建 - 参数规格与 Blender 运行状态相互独立"""

from collections import Counter
from string import Formatter


def _format_fields(message):
    """记录占位符名称及格式 - 翻译可调整顺序但不可改变参数契约"""
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
            if not isinstance(source, str) or not isinstance(translated, str) or not translated:
                raise ValueError(f"Invalid translation entry: {source!r}")
            if _format_fields(source) != _format_fields(translated):
                raise ValueError(f"Translation placeholders differ: {source!r}")
            if source in messages and messages[source] != translated:
                raise ValueError(f"Conflicting translations: {source!r}")
            messages[source] = translated
    return messages


def property_messages(specs, localized):
    """按字段和枚举标识生成消息 - 英文只从参数规格读取"""
    if set(specs) != set(localized):
        missing = sorted(set(specs) - set(localized))
        extra = sorted(set(localized) - set(specs))
        raise ValueError(f"Translation fields differ: missing={missing}, extra={extra}")
    groups = []
    for field, (_kind, options) in specs.items():
        entry = localized[field]
        expected = {key for key in ("name", "description", "items") if key in options}
        if set(entry) != expected:
            raise ValueError(f"Translation attributes differ: {field}")
        for attribute in ("name", "description"):
            if attribute in options:
                groups.append({options[attribute]: entry[attribute]})
        if "items" not in options:
            continue
        # 枚举按稳定标识对照 - 调整显示顺序不会改变译文归属
        items = {item[0]: item for item in options["items"]}
        if set(items) != set(entry["items"]):
            raise ValueError(f"Translation enum items differ: {field}")
        for identifier, (_identifier, name, description, *_) in items.items():
            translated = entry["items"][identifier]
            if set(translated) != {"name", "description"}:
                raise ValueError(f"Translation enum attributes differ: {field}.{identifier}")
            groups.extend(({name: translated["name"]}, {description: translated["description"]}))
    return merge_messages(*groups)


def build_translations(catalogs, *, aliases=None):
    """合并各模块语言目录并展开上下文 - 别名必须指向已有语言"""
    result = {}
    for module, contexts, languages in catalogs:
        for language, messages in languages.items():
            try:
                validated = merge_messages(messages)
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
        if alias in result or language not in result:
            raise ValueError(f"Invalid translation locale alias: {alias} -> {language}")
        result[alias] = dict(result[language])
    return result
