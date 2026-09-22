"""根翻译域与 Blender 界面及报告适配"""
import bpy
from .CellFracture.constants import CONTEXT
from .CellFracture.translations import PROPERTY_MESSAGES, UI_MESSAGES

TRANSLATIONS_DOMAIN = __package__

MESSAGES = {**UI_MESSAGES, **PROPERTY_MESSAGES}

translations_dict = {
    language: {(context, source): translated
               for source, translated in MESSAGES.items()
               for context in (CONTEXT, "*")}
    for language in ("zh_HANS", "zh_CN")
}

def tr(message, **values):
    """按自定义上下文取译文, 并填入格式化参数"""
    translated = bpy.app.translations.pgettext_iface(message, CONTEXT)
    return translated.format(**values) if values else translated

def tr_report(message, **values):
    """尊重报告翻译开关;动态字段在消息翻译之后填入"""
    translated = bpy.app.translations.pgettext_rpt(message, CONTEXT)
    return translated.format(**values) if values else translated
