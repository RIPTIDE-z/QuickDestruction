"""扩展装配 - 汇总工具声明, 生成场景参数组与挂载参数组, 校验唯一性

按顺序注册, 并在失败或停用时逆序回滚
"""

import re
import traceback

import bpy
from bpy.props import PointerProperty
from bpy.types import Operator, PropertyGroup

from .common.localization import DEFAULT_CONTEXT, build_translations
from .framework import presets
from .framework.constants import CONTEXT, LOCALES, SIDEBAR_CATEGORY
from .tools.Drivers.tool import TOOL as DRIVERS
from .tools.GameExport.tool import TOOL as GAME_EXPORT
from .tools.MeshFracture.tool import TOOL as MESH_FRACTURE
from .tools.Physics.tool import TOOL as PHYSICS
from .tools.Simulation.tool import TOOL as SIMULATION

# 工具清单: 元组顺序即注册顺序, 也就是侧栏里面板的上下顺序
TOOLS = (MESH_FRACTURE, PHYSICS, DRIVERS, SIMULATION, GAME_EXPORT)

# 已完成的注册步骤的逆操作, 停用时逆序执行
_undo_steps = []


def _settings_classes():
    """按参数规格生成各工具的参数组

    返回 类元组, 最后一个是挂到场景上的参数根, 它的字段指向前面各工具的参数组
    """
    groups = {}
    for tool in TOOLS:
        # 没有参数规格的工具 (例如只有面板) 没有参数组
        if tool.schema is None:
            continue
        annotations = tool.schema.definitions()
        # 带预设的工具多一个记录最近预设名的字段, 它不属于预设参数
        if tool.presets:
            annotations["preset_name"] = presets.current_preset_property()
        groups[tool.tool_id] = type(
            f"QD_PG_{tool.tool_id}", (PropertyGroup,), {"__annotations__": annotations}
        )
    fields = {tool_id: PointerProperty(type=cls) for tool_id, cls in groups.items()}
    return (*groups.values(), type("QD_PG_settings", (PropertyGroup,), {"__annotations__": fields}))


def _attachment_classes():
    """按各工具的挂载声明生成属性组

    返回 [(挂载声明, 属性组类), ...]
    """
    result = []
    for tool in TOOLS:
        for attachment in tool.attachments:
            # 类名由工具标识与去掉 qd_ 前缀的属性名组成, 例如 QD_PG_drivers_driver
            name = f"QD_PG_{tool.tool_id}_{attachment.attribute.removeprefix('qd_')}"
            annotations = attachment.schema.definitions()
            result.append(
                (attachment, type(name, (PropertyGroup,), {"__annotations__": annotations}))
            )
    return result


def _check_attachments():
    """检查挂载声明: 属性名以 qd_ 开头, 同一类型上不能重名

    不合规时抛 ValueError
    """
    seen = set()
    for tool in TOOLS:
        for attachment in tool.attachments:
            # qd_ 前缀避免与 Blender 或其他插件的属性撞名
            if not attachment.attribute.startswith("qd_"):
                raise ValueError(f"Attachment {attachment.attribute} must use the qd_ prefix")
            key = (attachment.owner, attachment.attribute)
            # 同一类型上的同名属性会让后挂的顶替先挂的
            if key in seen:
                raise ValueError(f"Duplicate attachment: {attachment.attribute}")
            seen.add(key)


def _idname(cls):
    """类的 bl_idname; 未显式声明时 Blender 取类名, 继承来的不算

    cls 要查询的类
    返回 标识字符串
    """
    return cls.__dict__.get("bl_idname", cls.__name__)


def _check_prefix(cls, class_prefix, operator_prefix):
    """类名与算子 ID 必须带规定的前缀, 减少与其他插件撞车

    cls 要检查的类
    class_prefix 类名必须有的前缀
    operator_prefix 算子 ID 必须有的前缀, 只对算子类检查
    """
    if not cls.__name__.startswith(class_prefix):
        raise ValueError(f"Class {cls.__name__} must use the {class_prefix} prefix")
    if issubclass(cls, Operator) and not _idname(cls).startswith(operator_prefix):
        raise ValueError(
            f"Operator {_idname(cls)} of class {cls.__name__} must use the {operator_prefix} prefix"
        )


def _check(framework_classes):
    """在改动 Blender 状态之前拒绝不合规的工具声明, 缺少前缀与重名的类

    framework_classes 框架自己的类 (参数组与预设界面), 它们用通用前缀
    """
    _check_attachments()
    for tool in TOOLS:
        # 工具标识同时是场景字段名, 预设目录名与类名前缀
        if not re.fullmatch(r"[a-z][a-z0-9_]*", tool.tool_id):
            raise ValueError(f"Invalid tool_id: {tool.tool_id!r}")
        # 预设界面靠参数规格读写参数, 没有规格就没有预设
        if tool.presets and tool.schema is None:
            raise ValueError(f"Tool {tool.tool_id} declares presets without a schema")
        # 工具的类名与算子 ID 都带自己的标识, 工具之间不会占用彼此的命名空间
        for cls in tool.classes:
            _check_prefix(cls, f"QD_{tool.tool_id.upper()}_", f"qd_{tool.tool_id}.")
    for cls in framework_classes:
        _check_prefix(cls, "QD_", "qd.")
    classes = (*framework_classes, *(cls for tool in TOOLS for cls in tool.classes))
    # Blender 对重复的 bl_idname 不报错而是静默顶替, 之后注销被顶替的类会失败
    for label, values in (
        ("tool_id", [tool.tool_id for tool in TOOLS]),
        ("class", [cls.__name__ for cls in classes]),
        ("bl_idname", [_idname(cls) for cls in classes]),
    ):
        duplicates = sorted({value for value in values if values.count(value) > 1})
        if duplicates:
            raise ValueError(f"Duplicate {label}: {', '.join(duplicates)}")


def _translations():
    """合并框架与各工具的语言文件, 侧栏分类名另外登记到默认上下文

    返回 {语言: {(上下文, 原文): 译文}}
    """
    modules = [(CONTEXT, LOCALES), *((tool.context, tool.locales) for tool in TOOLS)]
    translations = build_translations(modules)
    # Blender 在默认上下文里翻译侧栏分类名, 只把这一条单独登记过去, 其余界面文字不外溢
    for table in translations.values():
        if (CONTEXT, SIDEBAR_CATEGORY) in table:
            table[DEFAULT_CONTEXT, SIDEBAR_CATEGORY] = table[CONTEXT, SIDEBAR_CATEGORY]
    return translations


def _class_step(cls):
    """注册一个类的步骤与它的逆操作

    cls 要注册的类
    返回 (注册函数, 注销函数)
    """
    return lambda: bpy.utils.register_class(cls), lambda: bpy.utils.unregister_class(cls)


def _attach_step(attachment, cls):
    """把挂载参数组挂到目标类型上的步骤与它的逆操作

    attachment 挂载声明
    cls 该声明生成的属性组类
    返回 (挂上函数, 摘下函数)
    """
    return (
        lambda: setattr(attachment.owner, attachment.attribute, PointerProperty(type=cls)),
        lambda: delattr(attachment.owner, attachment.attribute),
    )


def _release(applied):
    """尽量完成所有逆操作

    applied 已完成步骤的逆操作列表, 按完成顺序排列
    返回 (失败的逆操作, 第一个错误); 失败的逆操作按原顺序返回, 供下次重试
    """
    failed = []
    first_error = None
    # 逆序执行: 最后完成的步骤最先撤销, 翻译最后注销
    for undo in reversed(applied):
        try:
            undo()
        except Exception as error:
            # 单条逆操作失败不能中断其余清理
            traceback.print_exc()
            failed.append(undo)
            if first_error is None:
                first_error = error
    # 失败列表是逆序收集的, 翻回注册顺序再保存, 下次重试时仍按逆序执行
    failed.reverse()
    return failed, first_error


def register():
    """注册翻译, 类, 场景指针与预设登记, 任一步失败都逆序回滚已完成的步骤"""
    # 重复注册会让后一份类顶替前一份, 留下无法注销的残骸
    if _undo_steps:
        raise RuntimeError("QuickDestruction is already registered")
    settings = _settings_classes()
    attached = _attachment_classes()
    groups = (*settings, *(cls for _attachment, cls in attached))
    _check((*groups, *presets.CLASSES))
    classes = (*groups, *presets.CLASSES, *(cls for tool in TOOLS for cls in tool.classes))
    preset_tools = {tool.tool_id: tool.schema for tool in TOOLS if tool.presets}
    translations = _translations()
    # 每一步是 (动作, 逆操作) 对, 顺序即注册顺序
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
        # 挂载参数组的类已在上面注册, 这里再把指针挂到目标类型上
        *(_attach_step(attachment, cls) for attachment, cls in attached),
        # 工具的钩子排在类之后: 钩子里的回调可能用到已注册的属性
        *(hook for tool in TOOLS for hook in tool.hooks),
        (lambda: presets.SCHEMAS.update(preset_tools), presets.SCHEMAS.clear),
    )
    applied = []
    try:
        for action, undo in steps:
            # 先执行再登记逆操作: 动作抛错时这一步不会被回滚
            action()
            applied.append(undo)
    except Exception:
        # 回滚中仍失败的逆操作留在记录里, 停用插件时会再试一次
        failed, _error = _release(applied)
        _undo_steps.extend(failed)
        raise
    _undo_steps.extend(applied)


def unregister():
    """按注册的逆序撤销全部步骤, 有失败的逆操作时抛出第一个错误"""
    # 先取出全部记录再执行: 失败的逆操作放回记录, 下次停用时重试, 不会因为一次失败而丢失
    applied = list(_undo_steps)
    _undo_steps.clear()
    failed, error = _release(applied)
    _undo_steps.extend(failed)
    if error is not None:
        raise error
