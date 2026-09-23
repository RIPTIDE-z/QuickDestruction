"""通用预设交互 - 会话令牌定位编辑目标, 全部工具共用一套算子与菜单

工具只需声明 PresetSpec 并在自己的面板里调用 draw_presets,
算子与菜单只注册一份, 与工具数量无关; 预设目录按工具标识分层
"""

from pathlib import Path
from typing import ClassVar

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Menu, Operator

from .common import preset_format, schema
from .common import preset_storage as storage
from .common.errors import DomainError, describe_error
from .common.translate import tr, tr_report
from .constants import UI_CONTEXT

# 令牌挂在布局上下文里的成员名, 预设菜单绘制时据此找回本次操作的目标
# 用上下文而不是参数组字段: 绘制回调里不允许写 ID 数据, 只能写上下文与 Python 状态
# 前缀 qd_ 避免与 Blender 自带的上下文成员重名
SESSION_MEMBER = "qd_preset_session"
# 会话表: 工具标识 -> 该工具的参数规格与参数持有者
# 面板每次重绘都刷新自己那一条, 因此它只随工具数量增长, 不随界面重绘增长
SESSIONS = {}


def preset_properties():
    """场景参数组要提供的预设字段: 当前预设名"""
    # preset_name 显示在面板上, 因此正常持久化; 它不属于预设文件里的参数
    return {"preset_name": StringProperty(name="Preset", translation_context=UI_CONTEXT)}


def panel_session(preset, owner):
    """登记侧栏的编辑目标并返回它的令牌

    令牌就是工具标识: 面板每次重绘都调用本函数, 刷新同一条记录
    """
    token = preset.key
    SESSIONS[token] = {"preset": preset, "owner": owner}
    return token


def target(token):
    """按令牌取本次预设操作的会话, 找不到时报明确错误"""
    session = SESSIONS.get(token)
    if session is None:
        raise ValueError("The preset target is unavailable")
    return session


def preset_name(token):
    """读取本次操作当前显示的预设名"""
    session = SESSIONS.get(token)
    return session["owner"].preset_name if session is not None else ""


def set_preset_name(context, token, name):
    """记录最近使用的预设名, 并刷新界面上的显示"""
    session = SESSIONS.get(token)
    if session is not None:
        session["owner"].preset_name = name
    # 名称显示在面板上, 需要主动重绘才会立刻变化
    if context.area:
        context.area.tag_redraw()


def preset_directory(preset, create=False):
    """取得该工具的用户预设目录, 保存时按需创建"""
    # user_resource 是 C 接口, 只接受 str, 传 Path 会抛 TypeError
    path = bpy.utils.user_resource("SCRIPTS", path=str(preset.subdir), create=create)
    # 用户脚本目录不可用时返回空值, 这里显式报错而不是静默退到别处
    if not path:
        raise OSError("Cannot resolve the preset directory")
    directory = Path(path)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def preset_filepath(preset, name):
    """只在本工具目录定位预设文件"""
    # 只读路径不创建目录: 目录不存在时由 list_presets 报"读不出来"
    return storage.preset_filepath(preset_directory(preset), name)


def list_presets(preset):
    """列出本工具的预设名, 按不区分大小写排序

    返回 (names, error): error 为 None 表示读取正常, 否则是 (消息键, 参数) 二元组
    区分两者是必要的 -- 目录解析不了或读不出来时, 菜单不该显示成"尚无预设",
    否则用户会以为预设丢了; 这里只返回消息键, 翻译交给调用方
    """
    try:
        directory = preset_directory(preset)
    except OSError:
        # 目录都定位不到: 用现成的消息键, 不再包一层
        return [], ("Cannot resolve the preset directory", {})
    try:
        return storage.list_presets(directory), None
    except OSError as error:
        # 与"目录里没有预设"区分开, 否则用户会以为预设丢了
        return [], ("Preset directory could not be read: {detail}", {"detail": str(error)})


def save_preset(preset, name, data, overwrite=False):
    """验证字段并原子保存本工具格式"""
    # 写入前加信封; 目录不存在时按需创建
    return storage.save_preset(
        preset_directory(preset, create=True),
        name,
        preset_format.encode(preset, data),
        overwrite,
    )


class PresetOperator:
    """预设算子的公共部分: 定位编辑目标并把错误翻译成人话"""

    def failure(self, error):
        """把预设异常翻译成界面消息并返回取消状态

        参数规格取自当前会话, 用于把字段标识换成界面参数名;
        会话已经失效时退回空规格, 出错字段原样展示
        """
        session = SESSIONS.get(self.session)
        specs = session["preset"].specs if session is not None else {}
        message, values = describe_error(error, specs)
        self.report(
            {"ERROR"},
            tr_report("Preset operation failed: {detail}", detail=tr_report(message, **values)),
        )
        return {"CANCELLED"}


def target_properties():
    """预设算子共用的两个隐藏/临时字段"""
    # session 是编辑目标令牌, 由预设菜单按当前会话写入; name 是本次操作的预设名
    # SKIP_SAVE: 两者都不进入 .blend
    return {
        "session": StringProperty(options={"HIDDEN", "SKIP_SAVE"}),
        "name": StringProperty(
            name="Name",
            description="Name of the preset",
            translation_context=UI_CONTEXT,
            options={"SKIP_SAVE"},
        ),
    }


class QD_OT_preset_save(PresetOperator, Operator):
    """保存当前目标参数为 JSON 预设"""

    bl_idname = "qd.preset_save"
    bl_label = "Save Preset"
    bl_description = "Save the parameters currently shown in this panel as a JSON preset"
    bl_translation_context = UI_CONTEXT
    # INTERNAL: 只由预设菜单调用, 不进入算子搜索;
    # ClassVar 说明这是 Blender 读取的类级配置, 不随实例变化
    bl_options: ClassVar[set[str]] = {"INTERNAL"}
    __annotations__ = {
        **target_properties(),
        "overwrite": BoolProperty(
            name="Overwrite",
            description="Replace the existing preset with this name",
            translation_context=UI_CONTEXT,
            options={"SKIP_SAVE"},
        ),
    }

    def invoke(self, context, _event):
        """沿用当前预设名并重置覆盖开关, 打开保存对话框"""
        self.name = self.name or preset_name(self.session)
        self.overwrite = False
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, _context):
        """绘制预设名与覆盖开关两个字段"""
        self.layout.prop(self, "name")
        self.layout.prop(self, "overwrite")

    def execute(self, context):
        """读出目标参数并以信封格式写入本工具目录"""
        try:
            # 先定位目标: 会话失效时在这里就报错, 不会误写到别的目录
            session = target(self.session)
            preset = session["preset"]
            # 从当前目标读出参数, 加上信封后原子写入本工具目录
            data = schema.settings_to_dict(session["owner"], preset.specs)
            name = save_preset(preset, self.name, data, self.overwrite)
            # 名称只在会话或侧栏里记录, 不写进预设文件
            set_preset_name(context, self.session, name)
        except FileExistsError:
            # 同名文件已存在: 提示用户勾选覆盖, 不视为错误
            self.report(
                {"WARNING"}, tr_report("Preset already exists; enable Overwrite to replace it")
            )
            return {"CANCELLED"}
        except (DomainError, OSError, ValueError, TypeError, ReferenceError) as error:
            return self.failure(error)
        self.report({"INFO"}, tr_report('Saved preset "{name}"', name=name))
        return {"FINISHED"}


class QD_OT_preset_load(PresetOperator, Operator):
    """验证 JSON 后载入当前目标参数"""

    bl_idname = "qd.preset_load"
    bl_label = "Load Preset"
    bl_description = "Load a JSON preset into the parameters currently shown here"
    bl_translation_context = UI_CONTEXT
    bl_options: ClassVar[set[str]] = {"INTERNAL"}
    __annotations__ = target_properties()

    def execute(self, context):
        """读取并校验预设文件, 把其中的参数整体写回当前目标"""
        try:
            session = target(self.session)
            preset = session["preset"]
            path = preset_filepath(preset, self.name)
            # 只读取有限大小的文件, 且只当数据解析, 绝不执行其中内容
            data = preset_format.decode(preset, storage.read_preset(path))
            # 校验通过后整体写回目标, 失败的字段不会留下半套值
            count = schema.dict_to_settings(session["owner"], data, preset.specs)
            set_preset_name(context, self.session, path.stem)
        except (
            DomainError,
            OSError,
            ValueError,
            TypeError,
            ReferenceError,
            OverflowError,
        ) as error:
            return self.failure(error)
        self.report({"INFO"}, tr_report("Loaded {count} parameters", count=count))
        return {"FINISHED"}


class QD_OT_preset_remove(PresetOperator, Operator):
    """确认后删除当前名称对应的预设"""

    bl_idname = "qd.preset_remove"
    bl_label = "Remove Preset"
    bl_description = "Permanently delete this JSON preset after confirmation"
    bl_translation_context = UI_CONTEXT
    bl_options: ClassVar[set[str]] = {"INTERNAL"}
    __annotations__ = target_properties()

    def invoke(self, context, _event):
        """先弹确认对话框再删除 - 删除不可撤销"""
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        """删除当前名称对应的预设文件, 必要时清空显示的名称"""
        try:
            # 先定位目标: 会话失效时在这里就报错, 不会误删别的目录
            session = target(self.session)
            path = preset_filepath(session["preset"], self.name)
            path.unlink()
            # 删掉的正好是当前预设时, 清空显示的名称
            if preset_name(self.session) == path.stem:
                set_preset_name(context, self.session, "")
        except (OSError, ValueError, ReferenceError) as error:
            return self.failure(error)
        self.report({"INFO"}, tr_report('Removed preset "{name}"', name=path.stem))
        return {"FINISHED"}


class QD_OT_preset_open_directory(PresetOperator, Operator):
    """打开本次操作所属工具的用户预设目录"""

    bl_idname = "qd.preset_open_directory"
    bl_label = "Open Preset Directory"
    bl_description = "Open the preset folder of this tool"
    bl_translation_context = UI_CONTEXT
    bl_options: ClassVar[set[str]] = {"INTERNAL"}
    __annotations__ = target_properties()

    def execute(self, _context):
        """在系统文件管理器中打开本工具的预设目录; 目录不存在时先创建"""
        try:
            session = target(self.session)
            bpy.ops.wm.path_open(filepath=str(preset_directory(session["preset"], create=True)))
        except (OSError, RuntimeError, ValueError) as error:
            return self.failure(error)
        return {"FINISHED"}


class QD_MT_presets(Menu):
    """预设下拉菜单: 保存/逐个加载/逐个删除/打开目录"""

    bl_idname = "QD_MT_presets"
    bl_label = "Presets"
    bl_translation_context = UI_CONTEXT

    def draw(self, context):
        """列出保存入口, 每个预设的载入与删除项, 以及打开目录项

        令牌由绘制方通过布局上下文传入, 这里只取它对应的会话
        """
        layout = self.layout
        # 与 Blender 自带的上下文成员一样用 getattr 兜底, 避免取出未设置的成员时抛错
        token = getattr(context, SESSION_MEMBER, "")
        session = SESSIONS.get(token)
        if session is None:
            # 会话已经失效: 常见于换文件之后, 此时不提供任何预设操作
            layout.label(text=tr_report("The preset target is unavailable"), icon="ERROR")
            return
        row = layout.row()
        row.operator_context = "INVOKE_DEFAULT"
        save = row.operator(
            "qd.preset_save", text="Save Preset...", text_ctxt=UI_CONTEXT, icon="ADD"
        )
        save.session = token
        save.name = preset_name(token)
        names, error = list_presets(session["preset"])
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
            layout.label(text="No presets yet", text_ctxt=UI_CONTEXT)
        layout.separator()
        layout.operator("qd.preset_open_directory", icon="FILE_FOLDER").session = token


def draw_presets(layout, preset, owner):
    """预设入口: 预设名菜单加一个快捷保存按钮

    每次绘制都刷新本工具的会话记录, 令牌就是工具标识
    """
    token = panel_session(preset, owner)
    # 令牌经布局上下文交给同一布局上的菜单, 绘制期不写任何 ID 数据
    layout.context_string_set(SESSION_MEMBER, token)
    row = layout.row(align=True)
    name = preset_name(token)
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
