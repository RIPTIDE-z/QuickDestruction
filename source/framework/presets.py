"""通用预设界面 - 全部工具共用一套算子与菜单, 按工具标识定位当前场景的参数组

工具在 ToolSpec 上声明 presets=True 并在面板里调用 draw_presets 即可获得保存, 载入,
删除与打开目录; 预设存放在扩展用户目录的 presets/<tool_id> 下, 升级扩展时不会被清掉
"""

import shutil
from pathlib import Path
from typing import ClassVar

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Menu, Operator

from ..common.translate import tr, tr_report
from . import preset_files
from .constants import CONTEXT
from .ui import ToolOperator

# 布局上下文里记录目标工具标识的成员名, 预设菜单绘制时据此找回目标工具
TOOL_MEMBER = "qd_preset_tool"
# 带预设的工具: 工具标识 -> 参数规格, 注册时由装配层登记, 注销时清空
SCHEMAS = {}
# 扩展根包名: 扩展用户目录接口只接受根包名, 不接受子包名
PACKAGE = __package__.rpartition(".")[0]


def name_property():
    """场景参数组上的预设名字段: 只记录界面显示的名称, 不属于预设参数"""
    return StringProperty(name="Preset", translation_context=CONTEXT)


def preset_directory(tool_id, create=False):
    """工具的预设目录, 位于扩展用户目录下"""
    return Path(bpy.utils.extension_path_user(PACKAGE, path=f"presets/{tool_id}", create=create))


def migrate_legacy_presets(tool_ids):
    """把旧版放在用户脚本目录的预设复制到扩展用户目录, 只在新目录还不存在时执行一次"""
    for tool_id in tool_ids:
        try:
            directory = preset_directory(tool_id)
            legacy = Path(
                bpy.utils.user_resource("SCRIPTS", path=f"quick_destruction/presets/{tool_id}")
            )
            if directory.exists() or not legacy.is_dir():
                continue
            directory.mkdir(parents=True)
            for path in legacy.glob("*.json"):
                shutil.copy2(path, directory / path.name)
        except (OSError, ValueError) as error:
            # 迁移失败只告警, 旧目录保持原样, 不影响扩展启用
            print(f"QuickDestruction: preset migration skipped for {tool_id}: {error}")


def _remember(context, owner, name):
    """记下最近使用的预设名, 并重绘面板让名称立刻更新"""
    owner.preset_name = name
    if context.area:
        context.area.tag_redraw()


class PresetOperator(ToolOperator):
    """预设算子的公共部分 - 按工具标识取当前场景的参数组与参数规格"""

    # 目标工具标识由菜单或保存按钮写入; SKIP_SAVE 让它不进入 .blend 与上次使用的参数
    __annotations__ = {"tool_id": StringProperty(options={"HIDDEN", "SKIP_SAVE"})}

    @property
    def schema(self):
        """目标工具的参数规格, 工具没有登记预设时为 None"""
        return SCHEMAS.get(self.tool_id)

    def target(self, context):
        """目标工具在当前场景的参数组"""
        if self.schema is None:
            raise preset_files.PresetError("The preset target is unavailable")
        return getattr(context.scene.qd_settings, self.tool_id)

    def filepath(self):
        """本次操作的预设文件"""
        return preset_files.preset_filepath(preset_directory(self.tool_id), self.name)


def _name_property():
    """本次操作的预设名; 每次调用都新建一份属性定义"""
    return StringProperty(
        name="Name",
        description="Name of the preset",
        translation_context=CONTEXT,
        options={"SKIP_SAVE"},
    )


class QD_OT_preset_save(PresetOperator, Operator):
    """把当前参数保存为 JSON 预设"""

    bl_idname = "qd.preset_save"
    bl_label = "Save Preset"
    bl_description = "Save the parameters currently shown in this panel as a JSON preset"
    bl_translation_context = CONTEXT
    # INTERNAL 只由预设菜单与保存按钮调用, 不进入算子搜索
    bl_options: ClassVar[set[str]] = {"INTERNAL"}
    __annotations__ = {
        "name": _name_property(),
        "overwrite": BoolProperty(
            name="Overwrite",
            description="Replace the existing preset with this name",
            translation_context=CONTEXT,
            options={"SKIP_SAVE"},
        ),
    }

    def invoke(self, context, _event):
        """打开填写预设名的对话框"""
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, _context):
        """绘制预设名与覆盖开关"""
        self.layout.prop(self, "name")
        self.layout.prop(self, "overwrite")

    def run(self, context):
        """读出目标参数, 加上信封后写入本工具的预设目录"""
        owner = self.target(context)
        data = preset_files.encode(self.tool_id, self.schema.read(owner))
        directory = preset_directory(self.tool_id, create=True)
        name = preset_files.save_preset(directory, self.name, data, self.overwrite)
        _remember(context, owner, name)
        return tr_report(CONTEXT, 'Saved preset "{name}"', name=name)


class QD_OT_preset_load(PresetOperator, Operator):
    """校验 JSON 预设后整体载入当前参数"""

    bl_idname = "qd.preset_load"
    bl_label = "Load Preset"
    bl_description = "Load a JSON preset into the parameters currently shown here"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"INTERNAL"}
    __annotations__ = {"name": _name_property()}

    def run(self, context):
        """读取并核对信封, 校验通过后整体写回; 缺字段保留界面现值"""
        owner = self.target(context)
        path = self.filepath()
        values = preset_files.decode(self.tool_id, preset_files.read_preset(path))
        count = self.schema.write(owner, values)
        _remember(context, owner, path.stem)
        return tr_report(CONTEXT, "Loaded {count} parameters", count=count)


class QD_OT_preset_remove(PresetOperator, Operator):
    """确认后删除预设文件"""

    bl_idname = "qd.preset_remove"
    bl_label = "Remove Preset"
    bl_description = "Permanently delete this JSON preset after confirmation"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"INTERNAL"}
    __annotations__ = {"name": _name_property()}

    def invoke(self, context, event):
        """删除不可撤销, 先弹确认对话框"""
        return context.window_manager.invoke_confirm(self, event)

    def run(self, context):
        """删除预设文件; 删掉的正好是当前预设时清空显示的名称"""
        owner = self.target(context)
        path = self.filepath()
        path.unlink()
        if owner.preset_name == path.stem:
            _remember(context, owner, "")
        return tr_report(CONTEXT, 'Removed preset "{name}"', name=path.stem)


class QD_OT_preset_open_directory(PresetOperator, Operator):
    """在系统文件管理器中打开工具的预设目录"""

    bl_idname = "qd.preset_open_directory"
    bl_label = "Open Preset Directory"
    bl_description = "Open the preset folder of this tool"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"INTERNAL"}

    def run(self, context):
        """目录不存在时先创建再打开"""
        self.target(context)
        bpy.ops.wm.path_open(filepath=str(preset_directory(self.tool_id, create=True)))


class QD_MT_presets(Menu):
    """预设下拉菜单: 保存, 逐个载入与删除, 打开目录"""

    bl_idname = "QD_MT_presets"
    bl_label = "Presets"
    bl_description = "Save, load or remove presets of this tool"
    bl_translation_context = CONTEXT

    def draw(self, context):
        """目标工具由绘制方经布局上下文传入, 读不出目录时说明原因而不是显示成没有预设"""
        layout = self.layout
        tool_id = getattr(context, TOOL_MEMBER, "")
        if tool_id not in SCHEMAS:
            layout.label(text="The preset target is unavailable", text_ctxt=CONTEXT, icon="ERROR")
            return
        owner = getattr(context.scene.qd_settings, tool_id)
        row = layout.row()
        row.operator_context = "INVOKE_DEFAULT"
        save = row.operator(
            QD_OT_preset_save.bl_idname, text="Save Preset...", text_ctxt=CONTEXT, icon="ADD"
        )
        save.tool_id, save.name = tool_id, owner.preset_name
        layout.separator()
        try:
            names = preset_files.list_presets(preset_directory(tool_id))
        except (OSError, ValueError) as error:
            names = []
            text = tr(CONTEXT, "Preset directory could not be read: {detail}", detail=str(error))
            layout.label(text=text, translate=False, icon="ERROR")
        for name in names:
            # 载入立即执行, 删除需要确认
            row = layout.row(align=True)
            load = row.operator(
                QD_OT_preset_load.bl_idname, text=name, translate=False, icon="PRESET"
            )
            load.tool_id, load.name = tool_id, name
            remove_row = row.row(align=True)
            remove_row.operator_context = "INVOKE_DEFAULT"
            remove = remove_row.operator(QD_OT_preset_remove.bl_idname, text="", icon="X")
            remove.tool_id, remove.name = tool_id, name
        if not names:
            layout.label(text="No presets yet", text_ctxt=CONTEXT)
        layout.separator()
        opener = layout.operator(QD_OT_preset_open_directory.bl_idname, icon="FILE_FOLDER")
        opener.tool_id = tool_id


def draw_presets(layout, tool_id, owner):
    """预设入口: 预设名下拉菜单加一个快捷保存按钮"""
    # 工具标识经布局上下文交给同一布局上的菜单, 绘制期不写任何 ID 数据
    layout.context_string_set(TOOL_MEMBER, tool_id)
    row = layout.row(align=True)
    text = owner.preset_name or tr(CONTEXT, "Presets")
    row.menu(QD_MT_presets.bl_idname, text=text, translate=False, icon="PRESET")
    row.operator_context = "INVOKE_DEFAULT"
    save = row.operator(QD_OT_preset_save.bl_idname, text="", icon="ADD")
    save.tool_id, save.name = tool_id, owner.preset_name


CLASSES = (
    QD_OT_preset_save,
    QD_OT_preset_load,
    QD_OT_preset_remove,
    QD_OT_preset_open_directory,
    QD_MT_presets,
)
