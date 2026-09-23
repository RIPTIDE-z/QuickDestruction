"""按工具清单装配扩展, 并在失败或停用时逆序释放资源。"""

import traceback
from collections.abc import Callable

import bpy
from bpy.props import PointerProperty

from .i18n import TRANSLATIONS_DOMAIN, translations_dict
from .panel import QD_PT_main
from .properties import QDSettings
from .tool_registry import TOOLS

_undo_steps: list[Callable[[], None]] = []


def _check_unique(tool_classes):
    """在修改 Blender 注册状态前拒绝重名工具、类和 RNA ID。"""
    for tool in TOOLS:
        if (tool.settings_attr is None) != (tool.settings_class is None):
            raise ValueError(f"Tool {tool.tool_id} must declare both settings fields")
    for field in ("tool_id", "translation_module", "settings_attr"):
        values = [getattr(tool, field) for tool in TOOLS if getattr(tool, field) is not None]
        if len(values) != len(set(values)):
            raise ValueError(f"Duplicate tool {field}")

    classes = (QDSettings, QD_PT_main)
    classes += tuple(tool.settings_class for tool in TOOLS if tool.settings_class is not None)
    classes += tuple(cls for group in tool_classes for cls in group)
    for label, values in (
        ("class", [cls.__name__ for cls in classes]),
        (
            "bl_idname",
            [cls.__dict__["bl_idname"] for cls in classes if "bl_idname" in cls.__dict__],
        ),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"Duplicate {label} in tool registry")


def _apply(action, undo, applied):
    """只为已成功执行的注册步骤记录逆操作。"""
    action()
    applied.append(undo)


def _release(applied):
    """尽量完成所有逆操作, 返回第一个错误供调用方报告。"""
    first_error = None
    for undo in reversed(applied):
        try:
            undo()
        except Exception as error:
            traceback.print_exc()
            if first_error is None:
                first_error = error
    return first_error


def register():
    """先注册参数类型, 再接场景指针、父子面板和菜单。"""
    if _undo_steps:
        raise RuntimeError("QuickDestruction is already registered")
    tool_classes = tuple(tool.classes() for tool in TOOLS)
    _check_unique(tool_classes)
    applied = []
    try:
        _apply(
            lambda: bpy.app.translations.register(TRANSLATIONS_DOMAIN, translations_dict),
            lambda: bpy.app.translations.unregister(TRANSLATIONS_DOMAIN),
            applied,
        )
        for tool in TOOLS:
            cls = tool.settings_class
            if cls is None:
                continue
            _apply(
                lambda cls=cls: bpy.utils.register_class(cls),
                lambda cls=cls: bpy.utils.unregister_class(cls),
                applied,
            )
        _apply(
            lambda: bpy.utils.register_class(QDSettings),
            lambda: bpy.utils.unregister_class(QDSettings),
            applied,
        )
        _apply(
            lambda: setattr(bpy.types.Scene, "qd_settings", PointerProperty(type=QDSettings)),
            lambda: delattr(bpy.types.Scene, "qd_settings"),
            applied,
        )
        _apply(
            lambda: bpy.utils.register_class(QD_PT_main),
            lambda: bpy.utils.unregister_class(QD_PT_main),
            applied,
        )
        for group in tool_classes:
            for cls in group:
                _apply(
                    lambda cls=cls: bpy.utils.register_class(cls),
                    lambda cls=cls: bpy.utils.unregister_class(cls),
                    applied,
                )
        # 对话框可能持有旧场景; 在卸载交互类前清空会话。
        for tool in TOOLS:
            if tool.clear_sessions is not None:
                applied.append(tool.clear_sessions)
            for menu_type, callback in tool.menu_hooks:
                _apply(
                    lambda menu_type=menu_type, callback=callback: menu_type.append(callback),
                    lambda menu_type=menu_type, callback=callback: menu_type.remove(callback),
                    applied,
                )
            if tool.load_pre is not None:
                _apply(
                    lambda tool=tool: bpy.app.handlers.load_pre.append(tool.load_pre),
                    lambda tool=tool: bpy.app.handlers.load_pre.remove(tool.load_pre),
                    applied,
                )
    except Exception:
        _release(applied)
        raise
    _undo_steps.extend(applied)


def unregister():
    """先移除外部入口, 再逆序注销交互类和参数类型。"""
    applied = list(_undo_steps)
    _undo_steps.clear()
    error = _release(applied)
    if error is not None:
        raise error
