"""译文取词 - 界面文字与报告消息的翻译入口, 上下文由调用方显式给出"""

import bpy


def tr(context, message, /, **values):
    """按上下文取界面译文, 再填入格式化参数

    context 翻译上下文, 与语言文件里注册的上下文一致
    message 英文原文, 占位符由它定义
    values 占位符的取值
    返回 译文, 没有译文时是填好参数的英文原文
    """
    # 先取译文再填参数: 占位符由英文原文定义, 译文只调整顺序与格式
    translated = bpy.app.translations.pgettext_iface(message, context)
    return translated.format(**values) if values else translated


def tr_report(context, message, /, **values):
    """按上下文取报告译文, 遵守 Blender 独立的报告翻译开关

    context 翻译上下文
    message 英文原文
    values 占位符的取值
    返回 译文, 没有译文时是填好参数的英文原文
    """
    translated = bpy.app.translations.pgettext_rpt(message, context)
    return translated.format(**values) if values else translated
