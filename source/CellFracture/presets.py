"""共享的 JSON 预设: 面板与对话框各自定位编辑目标, 写入采用原子替换"""

import uuid
from pathlib import Path
from typing import ClassVar

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Menu, Operator

from ..common import preset_storage as storage
from ..i18n import tr, tr_report
from .presentation import describe_error
from .settings import CONTEXT, dict_to_settings, settings_to_dict, validated_values

# 预设存放在 Blender 用户脚本目录下, 与扩展本体分离, 重装扩展不会丢
# 常量本身用 Path 表达目录层级, 交给 Blender 时再转回 str
PRESET_SUBDIR = Path("quick_destruction") / "presets"
# 以一次性令牌区分"对话框参数"与"侧栏参数"两个编辑目标, 不依赖 active_operator
SESSIONS = {}


def new_session(owner, context):
    """为一个对话框建立会话, 记录它正在编辑谁"""
    token = uuid.uuid4().hex
    SESSIONS[token] = {
        "owner": owner,
        "scene": context.scene,
        "name": context.scene.qd_settings.preset_name,
    }
    return token


def end_session(token):
    """释放会话令牌 - 已结束的会话无需再次处理"""
    SESSIONS.pop(token, None)


def target_settings(context, token=""):
    """取本次预设操作要读写的参数持有者, 无令牌时是侧栏"""
    if token:
        session = SESSIONS.get(token)
        # 场景已经换掉说明那个对话框早就失效了
        if session is None or session["scene"] != context.scene:
            raise ValueError("The parameter dialog has closed")
        return session["owner"]
    return context.scene.qd_settings.fracture


def preset_name(context, token=""):
    """读取会话或侧栏当前显示的预设名"""
    if token:
        return SESSIONS.get(token, {}).get("name", "")
    return context.scene.qd_settings.preset_name


def set_preset_name(context, token, name):
    """记录最近使用的预设名, 并刷新界面上的显示"""
    if token:
        SESSIONS[token]["name"] = name
    else:
        context.scene.qd_settings.preset_name = name
    if context.area:
        context.area.tag_redraw()


def preset_directory(create=False):
    """预设目录, 取不到时抛错而不是静默失败"""
    # user_resource 是 C 接口, 只接受 str, 传 Path 会抛 TypeError
    path = bpy.utils.user_resource("SCRIPTS", path=str(PRESET_SUBDIR), create=create)
    if not path:
        raise OSError("Cannot resolve the preset directory")
    directory = Path(path)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def preset_filepath(name, create=False):
    """按原用户目录解析本阶段预设路径"""
    return storage.preset_filepath(preset_directory(create=create), name)


def list_presets():
    """列出可用预设名, 按不区分大小写排序

    返回 (names, error): error 为 None 表示读取正常, 否则是 (消息键, 参数) 二元组
    区分两者是必要的 -- 目录解析不了或读不出来时, 菜单不该显示成"尚无预设",
    否则用户会以为预设丢了这里只返回消息键, 翻译交给调用方
    """
    try:
        directory = preset_directory()
    except OSError:
        # 目录都定位不到: 用现成的消息键, 不再包一层
        return [], ("Cannot resolve the preset directory", {})
    try:
        names = storage.list_presets(directory)
    except OSError as error:
        return [], ("Preset directory could not be read: {detail}", {"detail": str(error)})
    return names, None


def save_preset(name, data, overwrite=False):
    """先验证业务字段再委托存储原子发布"""
    validated_values(data)
    return storage.save_preset(preset_directory(create=True), name, data, overwrite)


class PresetTarget:
    """预设算子的公共部分: 定位编辑目标并把错误翻译成人话"""

    def target(self, context):
        """按令牌定位本次预设读写的目标"""
        return target_settings(context, self.session)

    def failure(self, error):
        # 字段标识换成界面参数名这件事由 describe_error() 统一处理
        """翻译预设错误并返回取消状态"""
        message, values = describe_error(error)
        self.report(
            {"ERROR"},
            tr_report("Preset operation failed: {detail}", detail=tr_report(message, **values)),
        )
        return {"CANCELLED"}


def target_properties():
    """预设算子共用的两个隐藏/临时字段"""
    return {
        "session": StringProperty(options={"HIDDEN", "SKIP_SAVE"}),
        "name": StringProperty(
            name="Name",
            description="Name of the preset",
            translation_context=CONTEXT,
            options={"SKIP_SAVE"},
        ),
    }


class QD_OT_preset_save(PresetTarget, Operator):
    """保存当前目标参数为 JSON 预设"""

    bl_idname = "qd.preset_save"
    bl_label = "Save Preset"
    bl_description = "Save the parameters currently shown in this panel or dialog as a JSON preset"
    bl_translation_context = CONTEXT
    # INTERNAL: 只由预设菜单调用, 不进入算子搜索;
    # ClassVar 说明这是 Blender 读取的类级配置, 不随实例变化
    bl_options: ClassVar[set[str]] = {"INTERNAL"}
    __annotations__ = {
        **target_properties(),
        "overwrite": BoolProperty(
            name="Overwrite",
            description="Replace the existing preset with this name",
            translation_context=CONTEXT,
            options={"SKIP_SAVE"},
        ),
    }

    def invoke(self, context, _event):
        # 默认沿用当前预设名, 覆盖开关每次都从关闭开始
        """初始化本次交互并打开参数或确认对话框"""
        self.name = self.name or preset_name(context, self.session)
        self.overwrite = False
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, _context):
        """绘制当前目标的控件并保留独立的启用条件"""
        self.layout.prop(self, "name")
        self.layout.prop(self, "overwrite")

    def execute(self, context):
        """执行本算子并按成功或失败返回 Blender 状态"""
        try:
            name = save_preset(self.name, settings_to_dict(self.target(context)), self.overwrite)
            set_preset_name(context, self.session, name)
        except FileExistsError:
            self.report(
                {"WARNING"}, tr_report("Preset already exists; enable Overwrite to replace it")
            )
            return {"CANCELLED"}
        except (OSError, ValueError, TypeError, ReferenceError) as error:
            return self.failure(error)
        self.report({"INFO"}, tr_report('Saved preset "{name}"', name=name))
        return {"FINISHED"}


class QD_OT_preset_load(PresetTarget, Operator):
    """验证 JSON 后载入当前目标参数"""

    bl_idname = "qd.preset_load"
    bl_label = "Load Preset"
    bl_description = "Load a JSON preset into the parameters currently shown here"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"INTERNAL"}
    __annotations__ = target_properties()

    def execute(self, context):
        """执行本算子并按成功或失败返回 Blender 状态"""
        try:
            path = preset_filepath(self.name)
            # 只读取有限大小的文件, 且只当数据解析, 绝不执行其中内容
            data = storage.read_preset(path)
            count = dict_to_settings(self.target(context), data)
            set_preset_name(context, self.session, path.stem)
        except (OSError, ValueError, TypeError, ReferenceError, OverflowError) as error:
            return self.failure(error)
        self.report({"INFO"}, tr_report("Loaded {count} parameters", count=count))
        return {"FINISHED"}


class QD_OT_preset_remove(PresetTarget, Operator):
    """确认后删除当前名称对应的预设"""

    bl_idname = "qd.preset_remove"
    bl_label = "Remove Preset"
    bl_description = "Permanently delete this JSON preset after confirmation"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"INTERNAL"}
    __annotations__ = target_properties()

    def invoke(self, context, _event):
        # 删除不可撤销, 因此先弹确认
        """初始化本次交互并打开参数或确认对话框"""
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        """执行本算子并按成功或失败返回 Blender 状态"""
        try:
            self.target(context)
            path = preset_filepath(self.name)
            path.unlink()
            # 删掉的正好是当前预设时, 清空显示的名称
            if preset_name(context, self.session) == path.stem:
                set_preset_name(context, self.session, "")
        except (OSError, ValueError, ReferenceError) as error:
            return self.failure(error)
        self.report({"INFO"}, tr_report('Removed preset "{name}"', name=path.stem))
        return {"FINISHED"}


class QD_OT_preset_open_directory(Operator):
    """打开本阶段的用户预设目录"""

    bl_idname = "qd.preset_open_directory"
    bl_label = "Open Preset Directory"
    bl_description = "Open the folder containing the shared JSON presets"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"INTERNAL"}

    def execute(self, _context):
        """执行本算子并按成功或失败返回 Blender 状态"""
        try:
            bpy.ops.wm.path_open(filepath=str(preset_directory(create=True)))
        except (OSError, RuntimeError) as error:
            self.report(
                {"ERROR"},
                tr_report("Preset operation failed: {detail}", detail=tr_report(str(error))),
            )
            return {"CANCELLED"}
        return {"FINISHED"}


class QD_MT_presets(Menu):
    """预设下拉菜单: 保存/逐个加载/逐个删除/打开目录"""

    bl_idname = "QD_MT_presets"
    bl_label = "Presets"
    bl_translation_context = CONTEXT

    def draw(self, context):
        """绘制当前目标的控件并保留独立的启用条件"""
        layout = self.layout
        owner = getattr(context, "qd_preset_target", None)
        token = getattr(owner, "preset_session", "")
        row = layout.row()
        row.operator_context = "INVOKE_DEFAULT"
        save = row.operator("qd.preset_save", text="Save Preset...", text_ctxt=CONTEXT, icon="ADD")
        save.session = token
        save.name = preset_name(context, token)
        names, error = list_presets()
        layout.separator()
        for name in names:
            # 加载立即执行, 删除需要确认
            row = layout.row(align=True)
            row.operator_context = "EXEC_DEFAULT"
            load = row.operator("qd.preset_load", text=name, translate=False, icon="PRESET")
            load.name, load.session = name, token
            remove_row = row.row(align=True)
            remove_row.operator_context = "INVOKE_DEFAULT"
            remove = remove_row.operator("qd.preset_remove", text="", icon="X")
            remove.name, remove.session = name, token
        if error is not None:
            # 读不出来时说明原因, 不能显示成"尚无预设", 否则用户会以为预设丢了
            layout.label(text=tr_report(error[0], **error[1]), icon="ERROR")
        elif not names:
            layout.label(text="No presets yet", text_ctxt=CONTEXT)
        layout.separator()
        layout.operator("qd.preset_open_directory", icon="FILE_FOLDER")


def draw_presets(layout, context, owner, token=""):
    """预设入口: 预设名菜单加一个快捷保存按钮"""
    # 把编辑目标挂到 context 上, 菜单绘制时再取出来
    layout.context_pointer_set("qd_preset_target", owner)
    row = layout.row(align=True)
    name = preset_name(context, token)
    row.menu("QD_MT_presets", text=name or tr("Presets"), translate=False, icon="PRESET")
    row.operator_context = "INVOKE_DEFAULT"
    save = row.operator("qd.preset_save", text="", icon="ADD")
    save.session = token
    save.name = name


CLASSES = (
    QD_OT_preset_save,
    QD_OT_preset_load,
    QD_OT_preset_remove,
    QD_OT_preset_open_directory,
    QD_MT_presets,
)
