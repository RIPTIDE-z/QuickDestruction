"""根翻译域与 Blender 界面及报告适配"""

from pathlib import Path

import bpy

from .CellFracture.constants import CONTEXT
from .CellFracture.settings import PROPERTY_SPECS
from .common.localization import build_translations, load_catalogs

TRANSLATIONS_DOMAIN = __package__

# 模块规格与上下文由代码维护；每种独立语言在 locales 中维护一个 JSON 文件。
MODULES = {"CellFracture": ((CONTEXT, "*"), PROPERTY_SPECS)}
CATALOGS = load_catalogs(Path(__file__).resolve().parent / "locales", MODULES)
LOCALE_ALIASES = {"zh_CN": "zh_HANS"}
translations_dict = build_translations(CATALOGS, aliases=LOCALE_ALIASES)


def tr(message, *, context=CONTEXT, **values):
    """按自定义上下文取译文, 并填入格式化参数"""
    translated = bpy.app.translations.pgettext_iface(message, context)
    return translated.format(**values) if values else translated


def tr_report(message, *, context=CONTEXT, **values):
    """尊重报告翻译开关;动态字段在消息翻译之后填入"""
    translated = bpy.app.translations.pgettext_rpt(message, context)
    return translated.format(**values) if values else translated
