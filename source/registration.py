"""扩展装配 - 汇总工具声明, 生成参数组, 校验唯一性, 按顺序注册并在失败或停用时逆序回滚"""

import re
import traceback

import bpy
from bpy.props import PointerProperty
from bpy.types import Operator, PropertyGroup

from .common.localization import DEFAULT_CONTEXT, build_translations
from .framework import presets
from .framework.constants import CONTEXT, LOCALES, SIDEBAR_CATEGORY
from .MeshFracture.tool import TOOL as MESH_FRACTURE
from .PhysicalSettings.tool import TOOL as PHYSICAL_SETTINGS

# 工具清单: 元组顺序即注册顺序, 也就是侧栏里面板的上下顺序
TOOLS = (MESH_FRACTURE, PHYSICAL_SETTINGS)

# 已完成的注册步骤的逆操作, 停用时逆序执行
_undo_steps = []


def _settings_classes():
    """按参数规格生成各工具的参数组, 最后一个是挂到场景上的参数根"""
    groups = {}
    for tool in TOOLS:
        if tool.schema is None:
            continue
        annotations = tool.schema.definitions()
        if tool.presets:
            annotations["preset_name"] = presets.name_property()
        groups[tool.tool_id] = type(
            f"QD_PG_{tool.tool_id}", (PropertyGroup,), {"__annotations__": annotations}
        )
    fields = {tool_id: PointerProperty(type=cls) for tool_id, cls in groups.items()}
    return (*groups.values(), type("QD_PG_settings", (PropertyGroup,), {"__annotations__": fields}))


def _check(classes):
    """在改动 Blender 状态之前拒绝不合规的工具声明, 重名与缺少前缀的类"""
    for tool in TOOLS:
        # 工具标识同时是场景字段名与预设目录名
        if not re.fullmatch(r"[a-z][a-z0-9_]*", tool.tool_id):
            raise ValueError(f"Invalid tool_id: {tool.tool_id!r}")
        if tool.presets and tool.schema is None:
            raise ValueError(f"Tool {tool.tool_id} declares presets without a schema")
    # 未显式声明 bl_idname 的类由 Blender 取类名; 继承来的不算
    idnames = [cls.__dict__.get("bl_idname", cls.__name__) for cls in classes]
    for cls, idname in zip(classes, idnames, strict=True):
        # 统一前缀, 减少与其他插件撞车: Blender 对重复的 bl_idname 静默顶替
        operator_ok = not issubclass(cls, Operator) or idname.startswith(("qd.", "qd_"))
        if not cls.__name__.startswith("QD_") or not operator_ok:
            raise ValueError(f"Class {cls.__name__} lacks the QD prefix")
    for label, values in (
        ("tool_id", [tool.tool_id for tool in TOOLS]),
        ("class", [cls.__name__ for cls in classes]),
        ("bl_idname", idnames),
    ):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise ValueError(f"Duplicate {label}: {', '.join(duplicates)}")


def _translations():
    """合并框架与各工具的语言文件, 侧栏分类名另外登记到默认上下文"""
    modules = [(CONTEXT, LOCALES), *((tool.context, tool.locales) for tool in TOOLS)]
    translations = build_translations(modules)
    # Blender 在默认上下文里翻译侧栏分类名, 只把这一条单独登记过去, 其余界面文字不外溢
    for table in translations.values():
        if (CONTEXT, SIDEBAR_CATEGORY) in table:
            table[DEFAULT_CONTEXT, SIDEBAR_CATEGORY] = table[CONTEXT, SIDEBAR_CATEGORY]
    return translations


def _class_step(cls):
    """注册一个类的步骤与它的逆操作"""
    return lambda: bpy.utils.register_class(cls), lambda: bpy.utils.unregister_class(cls)


def _handler_step(name, callback):
    """挂一个应用挂钩的步骤与它的逆操作"""
    handlers = getattr(bpy.app.handlers, name)
    return lambda: handlers.append(callback), lambda: handlers.remove(callback)


def _release(applied):
    """尽量完成所有逆操作, 返回第一个错误供调用方报告"""
    first_error = None
    # 逆序执行: 最后挂上的挂钩最先摘掉, 翻译最后注销
    for undo in reversed(applied):
        try:
            undo()
        except Exception as error:
            # 单条逆操作失败不能中断其余清理
            traceback.print_exc()
            if first_error is None:
                first_error = error
    return first_error


def register():
    """注册翻译, 类, 场景指针, 预设登记与应用挂钩, 任一步失败都逆序回滚已完成的步骤"""
    # 重复注册会让后一份类顶替前一份, 留下无法注销的残骸
    if _undo_steps:
        raise RuntimeError("QuickDestruction is already registered")
    settings = _settings_classes()
    classes = (*settings, *presets.CLASSES, *(cls for tool in TOOLS for cls in tool.classes))
    _check(classes)
    preset_tools = {tool.tool_id: tool.schema for tool in TOOLS if tool.presets}
    presets.migrate_legacy_presets(preset_tools)
    translations = _translations()
    steps = (
        (
            lambda: bpy.app.translations.register(__package__, translations),
            lambda: bpy.app.translations.unregister(__package__),
        ),
        # 参数组排在参数根之前: 参数根的字段类型指向它们
        *(_class_step(cls) for cls in classes),
        (
            lambda: setattr(bpy.types.Scene, "qd_settings", PointerProperty(type=settings[-1])),
            lambda: delattr(bpy.types.Scene, "qd_settings"),
        ),
        (lambda: presets.SCHEMAS.update(preset_tools), presets.SCHEMAS.clear),
        *(_handler_step(name, callback) for tool in TOOLS for name, callback in tool.handlers),
    )
    applied = []
    try:
        for action, undo in steps:
            # 先执行再登记逆操作: 动作抛错时这一步不会被回滚
            action()
            applied.append(undo)
    except Exception:
        _release(applied)
        raise
    _undo_steps.extend(applied)


def unregister():
    """按注册的逆序撤销全部步骤"""
    # 先复制再清空: 逆操作可能抛错, 但模块状态必须立刻复位, 否则下次注册会误判
    applied = list(_undo_steps)
    _undo_steps.clear()
    error = _release(applied)
    if error is not None:
        raise error
