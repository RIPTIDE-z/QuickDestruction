"""根翻译域 - 按工具清单建立翻译目录, 并提供 tr 与 tr_report 两个取词入口"""

from pathlib import Path

import bpy

from .common.localization import build_translations, load_catalogs
from .constants import UI_CONTEXT
from .tool_registry import ordered_tools

TRANSLATIONS_DOMAIN = __package__

# 模块规格与上下文由代码维护; 每种独立语言在 locales 中维护一个 JSON 文件
# 每个工具贡献一个模块: 上下文元组决定它的消息进入哪些翻译上下文
MODULES = {
    tool.translation_module: ((tool.translation_context, "*"), tool.property_specs)
    for tool in ordered_tools()
}
# 启动时一次性读入并校验全部语言文件, 之后绘制与报告都不再读磁盘
CATALOGS = load_catalogs(Path(__file__).resolve().parent / "locales", MODULES)
# zh_CN 与 zh_HANS 共用一套译文, 用别名避免维护两份内容相同的 JSON
LOCALE_ALIASES = {"zh_CN": "zh_HANS"}
translations_dict = build_translations(CATALOGS, aliases=LOCALE_ALIASES)


def tr(message, *, context=UI_CONTEXT, **values):
    """按自定义上下文取译文, 并填入格式化参数"""
    # 先取译文再填参数: 占位符由英文原文定义, 译文只调整顺序与格式
    translated = bpy.app.translations.pgettext_iface(message, context)
    return translated.format(**values) if values else translated


def tr_report(message, *, context=UI_CONTEXT, **values):
    """尊重报告翻译开关;动态字段在消息翻译之后填入"""
    # 报告走 pgettext_rpt: 它受 Blender 独立于界面的报告翻译开关控制
    translated = bpy.app.translations.pgettext_rpt(message, context)
    return translated.format(**values) if values else translated
