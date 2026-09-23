"""译文取词 - 界面层取译文的唯一入口, 不依赖工具清单因此不会形成导入环"""

import bpy

from ..constants import UI_CONTEXT


def tr(message, *, context=UI_CONTEXT, **values):
    """按自定义上下文取界面译文, 并填入格式化参数"""
    # 先取译文再填参数: 占位符由英文原文定义, 译文只调整顺序与格式
    translated = bpy.app.translations.pgettext_iface(message, context)
    return translated.format(**values) if values else translated


def tr_report(message, *, context=UI_CONTEXT, **values):
    """尊重报告翻译开关;动态字段在消息翻译之后填入"""
    # 报告走 pgettext_rpt: 它受 Blender 独立于界面的报告翻译开关控制
    translated = bpy.app.translations.pgettext_rpt(message, context)
    return translated.format(**values) if values else translated
