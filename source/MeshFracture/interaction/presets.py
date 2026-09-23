"""预设交互 - 用会话令牌定位编辑目标, 菜单与算子负责保存, 载入, 删除与打开目录"""

import uuid
from pathlib import Path
from typing import ClassVar

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Menu, Operator

from ...common import preset_storage as storage
from ...i18n import tr, tr_report
from ..errors import MeshFractureError
from ..settings import CONTEXT, describe_error, dict_to_settings, settings_to_dict
from .preset_format import decode, encode

# 预设留在 Blender 用户脚本目录, 不随扩展重装而删除
PRESET_SUBDIR = Path("quick_destruction") / "presets" / "mesh_fracture"
# 以一次性令牌区分"对话框参数"与"侧栏参数"两个编辑目标, 不依赖 active_operator
# 结构: {令牌: {"owner": 参数持有者, "scene": 归属场景, "name": 当前预设名}}
SESSIONS = {}


def new_session(owner, context):
    """为一个对话框建立会话, 记录它正在编辑谁"""
    # 令牌同时交给对话框里的算子实例, 后续预设操作据此找回编辑目标
    token = uuid.uuid4().hex
    SESSIONS[token] = {
        "owner": owner,
        "scene": context.scene,
        "name": context.scene.qd_settings.mesh_fracture.preset_name,
    }
    return token


def end_session(token):
    """释放会话令牌 - 已结束的会话无需再次处理"""
    # 用 pop 而不是 del: 重做与取消可能重复结束同一个会话
    SESSIONS.pop(token, None)


def target_settings(context, token=""):
    """取本次预设操作要读写的参数持有者, 无令牌时是侧栏"""
    if token:
        session = SESSIONS.get(token)
        # 场景已经换掉说明那个对话框早就失效了
        if session is None or session["scene"] != context.scene:
            raise ValueError("The parameter dialog has closed")
        return session["owner"]
    return context.scene.qd_settings.mesh_fracture


def preset_name(context, token=""):
    """读取会话或侧栏当前显示的预设名"""
    if token:
        # 会话里的名称只在本次对话框内有效, 会话结束即丢弃
        return SESSIONS.get(token, {}).get("name", "")
    return context.scene.qd_settings.mesh_fracture.preset_name


def set_preset_name(context, token, name):
    """记录最近使用的预设名, 并刷新界面上的显示"""
    if token:
        SESSIONS[token]["name"] = name
    else:
        context.scene.qd_settings.mesh_fracture.preset_name = name
    # 名称显示在面板上, 需要主动重绘才会立刻变化
    if context.area:
        context.area.tag_redraw()


def preset_directory(create=False):
    """取得本工具的用户预设目录, 保存时按需创建"""
    # user_resource 是 C 接口, 只接受 str, 传 Path 会抛 TypeError
    path = bpy.utils.user_resource("SCRIPTS", path=str(PRESET_SUBDIR), create=create)
    # 用户脚本目录不可用时返回空值, 这里显式报错而不是静默退到别处
    if not path:
        raise OSError("Cannot resolve the preset directory")
    directory = Path(path)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def preset_filepath(name):
    """只在本工具目录定位预设文件"""
    # 只读路径不创建目录: 目录不存在时由 list_presets 报"读不出来"
    return storage.preset_filepath(preset_directory(), name)


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
        # 与"目录里没有预设"区分开, 否则用户会以为预设丢了
        return [], ("Preset directory could not be read: {detail}", {"detail": str(error)})
    return names, None


def save_preset(name, data, overwrite=False):
    """验证字段并原子保存本工具格式"""
    # 写入前加信封; 目录不存在时按需创建
    return storage.save_preset(preset_directory(create=True), name, encode(data), overwrite)


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
    # session 由菜单或面板写入的令牌; name 是本次操作的预设名
    # SKIP_SAVE: 两者都不进入 .blend, 避免把指针指向已失效的会话
    return {
        "session": StringProperty(options={"HIDDEN", "SKIP_SAVE"}),
        "name": StringProperty(
            name="Name",
            description="Name of the preset",
            translation_context=CONTEXT,
            options={"SKIP_SAVE"},
        ),
    }


class MESH_FRACTURE_OT_preset_save(PresetTarget, Operator):
    """保存当前目标参数为 JSON 预设"""

    bl_idname = "mesh_fracture.preset_save"
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
            # 从当前目标读出参数, 加上信封后原子写入本工具目录
            name = save_preset(self.name, settings_to_dict(self.target(context)), self.overwrite)
            # 名称只在会话或侧栏里记录, 不写进预设文件
            set_preset_name(context, self.session, name)
        except FileExistsError:
            # 同名文件已存在: 提示用户勾选覆盖, 不视为错误
            self.report(
                {"WARNING"}, tr_report("Preset already exists; enable Overwrite to replace it")
            )
            return {"CANCELLED"}
        except (MeshFractureError, OSError, ValueError, TypeError, ReferenceError) as error:
            return self.failure(error)
        self.report({"INFO"}, tr_report('Saved preset "{name}"', name=name))
        return {"FINISHED"}


class MESH_FRACTURE_OT_preset_load(PresetTarget, Operator):
    """验证 JSON 后载入当前目标参数"""

    bl_idname = "mesh_fracture.preset_load"
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
            data = decode(storage.read_preset(path))
            # 校验通过后整体写回目标, 失败的字段不会留下半套值
            count = dict_to_settings(self.target(context), data)
            set_preset_name(context, self.session, path.stem)
        except (
            MeshFractureError,
            OSError,
            ValueError,
            TypeError,
            ReferenceError,
            OverflowError,
        ) as error:
            return self.failure(error)
        self.report({"INFO"}, tr_report("Loaded {count} parameters", count=count))
        return {"FINISHED"}


class MESH_FRACTURE_OT_preset_remove(PresetTarget, Operator):
    """确认后删除当前名称对应的预设"""

    bl_idname = "mesh_fracture.preset_remove"
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
            # 先解析目标: 对话框已关闭时在这里就报错, 不会误删别的目录
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


class MESH_FRACTURE_OT_preset_open_directory(Operator):
    """打开本阶段的用户预设目录"""

    bl_idname = "mesh_fracture.preset_open_directory"
    bl_label = "Open Preset Directory"
    bl_description = "Open the folder for MeshFracture JSON presets"
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


class MESH_FRACTURE_MT_presets(Menu):
    """预设下拉菜单: 保存/逐个加载/逐个删除/打开目录"""

    bl_idname = "MESH_FRACTURE_MT_presets"
    bl_label = "Presets"
    bl_translation_context = CONTEXT

    def draw(self, context):
        """绘制当前目标的控件并保留独立的启用条件"""
        layout = self.layout
        owner = getattr(context, "mesh_fracture_preset_target", None)
        token = getattr(owner, "preset_session", "")
        row = layout.row()
        row.operator_context = "INVOKE_DEFAULT"
        save = row.operator(
            "mesh_fracture.preset_save", text="Save Preset...", text_ctxt=CONTEXT, icon="ADD"
        )
        save.session = token
        save.name = preset_name(context, token)
        names, error = list_presets()
        layout.separator()
        for name in names:
            # 加载立即执行, 删除需要确认
            row = layout.row(align=True)
            row.operator_context = "EXEC_DEFAULT"
            load = row.operator(
                "mesh_fracture.preset_load", text=name, translate=False, icon="PRESET"
            )
            load.name, load.session = name, token
            remove_row = row.row(align=True)
            remove_row.operator_context = "INVOKE_DEFAULT"
            remove = remove_row.operator("mesh_fracture.preset_remove", text="", icon="X")
            remove.name, remove.session = name, token
        if error is not None:
            # 读不出来时说明原因, 不能显示成"尚无预设", 否则用户会以为预设丢了
            layout.label(text=tr_report(error[0], **error[1]), icon="ERROR")
        elif not names:
            layout.label(text="No presets yet", text_ctxt=CONTEXT)
        layout.separator()
        layout.operator("mesh_fracture.preset_open_directory", icon="FILE_FOLDER")


def draw_presets(layout, context, owner, token=""):
    """预设入口: 预设名菜单加一个快捷保存按钮"""
    # 把编辑目标挂到 context 上, 菜单绘制时再取出来
    layout.context_pointer_set("mesh_fracture_preset_target", owner)
    row = layout.row(align=True)
    name = preset_name(context, token)
    row.menu("MESH_FRACTURE_MT_presets", text=name or tr("Presets"), translate=False, icon="PRESET")
    row.operator_context = "INVOKE_DEFAULT"
    save = row.operator("mesh_fracture.preset_save", text="", icon="ADD")
    save.session = token
    save.name = name


CLASSES = (
    MESH_FRACTURE_OT_preset_save,
    MESH_FRACTURE_OT_preset_load,
    MESH_FRACTURE_OT_preset_remove,
    MESH_FRACTURE_OT_preset_open_directory,
    MESH_FRACTURE_MT_presets,
)
