"""根翻译域与 Blender 界面及报告适配"""

import bpy

from .CellFracture.constants import CONTEXT
from .CellFracture.translations import CATALOGS as CELL_FRACTURE_CATALOGS
from .common.localization import build_translations

TRANSLATIONS_DOMAIN = __package__

# 新业务模块在此显式接入 - 相同语言和上下文中的冲突会报错
CATALOGS = (("CellFracture", (CONTEXT, "*"), CELL_FRACTURE_CATALOGS),)
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
