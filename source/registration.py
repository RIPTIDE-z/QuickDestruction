"""扩展装配 - 先校验工具声明的唯一性, 再顺序注册属性组, 面板, 算子与外部入口, 失败或停用时逆序回滚"""

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
    """在修改 Blender 注册状态前拒绝重名工具, 类和 RNA ID"""
    # 场景字段名与属性组类必须成对声明: 只给其中一个会让 QDSettings 挂出没有类型的字段
    for tool in TOOLS:
        if (tool.settings_attr is None) != (tool.settings_class is None):
            raise ValueError(f"Tool {tool.tool_id} must declare both settings fields")
    # 工具身份字段全局唯一; None 表示该工具不声明这一项, 因此先过滤掉再比较
    for field in ("tool_id", "translation_module", "settings_attr"):
        values = [getattr(tool, field) for tool in TOOLS if getattr(tool, field) is not None]
        if len(values) != len(set(values)):
            raise ValueError(f"Duplicate tool {field}")

    # 汇总本次要注册的全部类: 根层参数组与父面板, 各工具的参数组, 各工具声明的交互类
    classes = (QDSettings, QD_PT_main)
    classes += tuple(tool.settings_class for tool in TOOLS if tool.settings_class is not None)
    classes += tuple(cls for group in tool_classes for cls in group)
    # Blender 对重复 bl_idname 不报错, 而是静默用后注册的实现顶替先注册的,
    # 之后注销被顶替的类会抛 RuntimeError 并打断整个 unregister 流程,
    # 因此类名与 bl_idname 两类重名都必须在这里, 在任何注册动作之前拦下
    for label, values in (
        ("class", [cls.__name__ for cls in classes]),
        (
            "bl_idname",
            # 只统计类自身直接定义的 bl_idname, 从基类继承来的不算重复
            [cls.__dict__["bl_idname"] for cls in classes if "bl_idname" in cls.__dict__],
        ),
    ):
        if len(values) != len(set(values)):
            raise ValueError(f"Duplicate {label} in tool registry")


def _apply(action, undo, applied):
    """只为已成功执行的注册步骤记录逆操作"""
    # 先执行再登记: 动作抛错时这一条不会进入 applied, 也就不会被回滚
    action()
    applied.append(undo)


def _release(applied):
    """尽量完成所有逆操作, 返回第一个错误供调用方报告"""
    first_error = None
    # 逆序执行: 最后挂上的外部入口最先摘掉, 类型注册最后注销
    for undo in reversed(applied):
        try:
            undo()
        except Exception as error:
            # 单条逆操作失败不能中断其余清理, 只记下第一个错误交给调用方
            traceback.print_exc()
            if first_error is None:
                first_error = error
    return first_error


def register():
    """先注册参数类型, 再接场景指针, 父子面板和菜单"""
    # 已经注册过就整体拒绝: 重复注册会让后一份类顶替前一份, 留下无法注销的残骸
    if _undo_steps:
        raise RuntimeError("QuickDestruction is already registered")
    # 交互类由各工具的 classes() 延迟提供, 这里一次性取齐, 以便在改动任何
    # Blender 状态之前完成唯一性检查; 取类本身也会执行工具包内的延迟导入
    tool_classes = tuple(tool.classes() for tool in TOOLS)
    _check_unique(tool_classes)
    # applied 记录本次已经成功的每一步及其逆操作; 中途失败时按它回滚
    applied = []
    try:
        # 1) 翻译目录最先注册: 后面的参数标签与报告消息都要能取到译文
        _apply(
            lambda: bpy.app.translations.register(TRANSLATIONS_DOMAIN, translations_dict),
            lambda: bpy.app.translations.unregister(TRANSLATIONS_DOMAIN),
            applied,
        )
        # 2) 各工具的参数组: QDSettings 的字段类型指向这些类, 必须先于它注册
        # 默认参数写法 cls=cls 是必需的: 否则所有 lambda 都引用循环结束后的同一个名字
        for tool in TOOLS:
            cls = tool.settings_class
            if cls is None:
                continue
            _apply(
                lambda cls=cls: bpy.utils.register_class(cls),
                lambda cls=cls: bpy.utils.unregister_class(cls),
                applied,
            )
        # 3) 聚合根属性组
        _apply(
            lambda: bpy.utils.register_class(QDSettings),
            lambda: bpy.utils.unregister_class(QDSettings),
            applied,
        )
        # 4) 场景指针: 类型已注册, 这里才把 qd_settings 挂到 Scene 上
        _apply(
            lambda: setattr(bpy.types.Scene, "qd_settings", PointerProperty(type=QDSettings)),
            lambda: delattr(bpy.types.Scene, "qd_settings"),
            applied,
        )
        # 5) 父面板: 子面板的 bl_parent_id 指向它, 必须已存在
        _apply(
            lambda: bpy.utils.register_class(QD_PT_main),
            lambda: bpy.utils.unregister_class(QD_PT_main),
            applied,
        )
        # 6) 工具声明的交互类: 生成算子, 预设算子, 菜单, 子面板
        for group in tool_classes:
            for cls in group:
                _apply(
                    lambda cls=cls: bpy.utils.register_class(cls),
                    lambda cls=cls: bpy.utils.unregister_class(cls),
                    applied,
                )
        # 7) 外部入口最后挂: 菜单与 handler 一旦生效就可能被用户触发,
        #    因此回滚时它们会最先被摘掉
        for tool in TOOLS:
            if tool.clear_sessions is not None:
                # 该回调只在卸载时执行, 没有对应的注册动作, 所以直接作为逆操作登记;
                # 它排在本轮菜单挂钩之前, 逆序执行时便会晚于菜单解锁,
                # 早于交互类注销, 正好满足"卸载交互类前清空会话"的要求
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
        # 任一步失败都回滚已完成的步骤; 只有全部成功才把逆操作交给模块级列表
        _release(applied)
        raise
    _undo_steps.extend(applied)


def unregister():
    """先移除外部入口, 再逆序注销交互类和参数类型"""
    # 先复制再清空: 逆操作执行中可能抛错, 但模块状态必须立刻复位,
    # 否则下一次 register() 会误判为仍在注册状态
    applied = list(_undo_steps)
    _undo_steps.clear()
    error = _release(applied)
    # 所有逆操作都已尝试, 把第一个错误交给 Blender 的扩展卸载流程
    if error is not None:
        raise error
