"""扩展装配 - 先校验工具声明的唯一性, 再顺序注册属性组, 面板, 算子与外部入口, 失败或停用时逆序回滚"""

import traceback
from collections.abc import Callable

import bpy
from bpy.props import PointerProperty

from .i18n import COMMON_MODULE, TRANSLATIONS_DOMAIN, translations_dict
from .preset_ui import CLASSES as PRESET_CLASSES
from .properties import QDSettings
from .tool_registry import ordered_tools

_undo_steps: list[Callable[[], None]] = []


def _collect_classes(tools):
    """取各工具的交互类, 取不到时补上工具标识

    classes() 内部是工具包的首次导入, 裸的导入错误里没有工具名, 很难定位
    """
    groups = []
    for tool in tools:
        try:
            groups.append(tool.classes())
        except Exception as error:
            raise RuntimeError(f"Tool {tool.tool_id} failed to provide its classes") from error
    return tuple(groups)


def _apply_panel_state(tools, tool_classes):
    """把工具声明的初始折叠状态落到它的面板类上

    Blender 在注册面板类时读取 bl_options, 因此必须在注册之前设置;
    以 ToolSpec.default_closed 为唯一来源: 声明收起就加上 DEFAULT_CLOSED,
    否则去掉, 工具自己写在面板类上的同名选项会被这里覆盖
    """
    for tool, group in zip(tools, tool_classes, strict=True):
        for cls in group:
            if not (isinstance(cls, type) and issubclass(cls, bpy.types.Panel)):
                continue
            options = set(getattr(cls, "bl_options", ()) or ())
            if tool.default_closed:
                options.add("DEFAULT_CLOSED")
            else:
                options.discard("DEFAULT_CLOSED")
            cls.bl_options = options


def _check_unique(tools, tool_classes):
    """在修改 Blender 注册状态前拒绝重名工具, 类和 RNA ID"""
    # classes() 的返回顺序就是注册顺序, 因此先确认它确实是类的元组,
    # 否则问题会等到逐个注册时才以 Blender 的错误形式暴露
    for tool, group in zip(tools, tool_classes, strict=True):
        if not isinstance(group, tuple) or not all(isinstance(cls, type) for cls in group):
            raise ValueError(f"Tool {tool.tool_id} classes() must return a tuple of classes")
    # 场景字段名与属性组类必须成对声明: 只给其中一个会让 QDSettings 挂出没有类型的字段
    for tool in tools:
        if (tool.settings_attr is None) != (tool.settings_class is None):
            raise ValueError(f"Tool {tool.tool_id} must declare both settings fields")
    # 工具身份字段全局唯一; None 表示该工具不声明这一项, 因此先过滤掉再比较
    for field in ("tool_id", "translation_module", "settings_attr"):
        values = [getattr(tool, field) for tool in tools if getattr(tool, field) is not None]
        if len(values) != len(set(values)):
            raise ValueError(f"Duplicate tool {field}")
    # 框架消息固定占用一个翻译分组, 工具与它同名会让语言文件的模块集合撞车
    for tool in tools:
        if tool.translation_module == COMMON_MODULE:
            raise ValueError(f"Tool {tool.tool_id} uses the reserved translation module")
    # 预设归属也要唯一: 工具标识决定预设目录, 同时是侧栏预设会话的令牌,
    # 两者撞车会让一个工具读写到另一个工具的预设
    presets = [tool.preset for tool in tools if tool.preset is not None]
    for field in ("key", "subdir"):
        values = [str(getattr(preset, field)) for preset in presets]
        if len(values) != len(set(values)):
            raise ValueError(f"Duplicate preset {field}")

    # 汇总本次要注册的全部类: 根层参数组, 框架的通用预设类,
    # 各工具的参数组, 各工具声明的交互类
    classes = (QDSettings, *PRESET_CLASSES)
    classes += tuple(tool.settings_class for tool in tools if tool.settings_class is not None)
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
        # 报出具体撞车的名字: 只说"有重复"会让新增工具时无从下手
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise ValueError(f"Duplicate {label} in tool registry: {', '.join(duplicates)}")


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
    """先注册参数类型, 再接场景指针, 预设类, 工具类与外部入口"""
    # 已经注册过就整体拒绝: 重复注册会让后一份类顶替前一份, 留下无法注销的残骸
    if _undo_steps:
        raise RuntimeError("QuickDestruction is already registered")
    # 工具集合按 weight 排序取一次, 注册顺序与翻译目录共用同一份
    tools = ordered_tools()
    # 交互类由各工具的 classes() 提供, 这里一次性取齐, 以便在改动任何
    # Blender 状态之前完成唯一性检查; 取类本身也会完成工具包的首次导入
    tool_classes = _collect_classes(tools)
    _check_unique(tools, tool_classes)
    # 初始折叠只认 bl_options, 必须在注册之前落到类上
    _apply_panel_state(tools, tool_classes)
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
        for tool in tools:
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
        # 5) 框架的通用预设算子与菜单: 各工具的侧栏面板绘制时会引用它们
        for cls in PRESET_CLASSES:
            _apply(
                lambda cls=cls: bpy.utils.register_class(cls),
                lambda cls=cls: bpy.utils.unregister_class(cls),
                applied,
            )
        # 6) 工具声明的交互类: 生成算子, 菜单与侧栏面板
        for group in tool_classes:
            for cls in group:
                _apply(
                    lambda cls=cls: bpy.utils.register_class(cls),
                    lambda cls=cls: bpy.utils.unregister_class(cls),
                    applied,
                )
        # 7) 外部入口最后挂: 菜单与 handler 一旦生效就可能被用户触发,
        #    因此回滚时它们会最先被摘掉
        for tool in tools:
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
            # 文件加载前后各一个挂点: load_pre 清理旧状态, load_post 按新场景重建
            for handler, callback in (
                (bpy.app.handlers.load_pre, tool.load_pre),
                (bpy.app.handlers.load_post, tool.load_post),
            ):
                if callback is None:
                    continue
                _apply(
                    lambda handler=handler, callback=callback: handler.append(callback),
                    lambda handler=handler, callback=callback: handler.remove(callback),
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
