"""共享的 JSON 预设: 面板与对话框各自定位编辑目标, 写入采用原子替换。"""

import json
import os
from pathlib import Path
import re
import tempfile
import unicodedata
import uuid

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy.types import Menu, Operator

from .qd_properties import CONTEXT, PROPERTY_SPECS
from .qd_settings import dict_to_settings, settings_to_dict, validated_values
from .qd_errors import InvalidParameterError
from .qd_translations import tr, tr_report

# 预设存放在 Blender 用户脚本目录下, 与扩展本体分离, 重装扩展不会丢
PRESET_SUBDIR = os.path.join("quick_destruction", "presets")
# 以一次性令牌区分"对话框参数"与"侧栏参数"两个编辑目标, 不依赖 active_operator
SESSIONS = {}


def new_session(owner, context):
    """为一个对话框建立会话, 记录它正在编辑谁。"""
    token = uuid.uuid4().hex
    SESSIONS[token] = {'owner': owner, 'scene': context.scene,
                       'name': context.scene.qd_settings.preset_name}
    return token


def end_session(token):
    SESSIONS.pop(token, None)


def target_settings(context, token=""):
    """取本次预设操作要读写的参数持有者, 无令牌时是侧栏。"""
    if token:
        session = SESSIONS.get(token)
        # 场景已经换掉说明那个对话框早就失效了
        if session is None or session['scene'] != context.scene:
            raise ValueError("The parameter dialog has closed")
        return session['owner']
    return context.scene.qd_settings.fracture


def preset_name(context, token=""):
    if token:
        return SESSIONS.get(token, {}).get('name', '')
    return context.scene.qd_settings.preset_name


def set_preset_name(context, token, name):
    """记录最近使用的预设名, 并刷新界面上的显示。"""
    if token:
        SESSIONS[token]['name'] = name
    else:
        context.scene.qd_settings.preset_name = name
    if context.area:
        context.area.tag_redraw()


def preset_directory(create=False):
    """预设目录, 取不到时抛错而不是静默失败。"""
    path = bpy.utils.user_resource('SCRIPTS', path=PRESET_SUBDIR, create=create)
    if not path:
        raise OSError("Cannot resolve the preset directory")
    directory = Path(path)
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def normalize_name(name):
    """把用户输入整理成安全的文件名, 拒绝空名、Windows 保留名与超长名。"""
    name = unicodedata.normalize('NFC', name.strip())
    # 路径分隔符与控制字符一律替换成下划线
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', name).strip('. ')
    if not name:
        raise ValueError("Preset name is empty")
    stem = name.split('.')[0]
    if re.fullmatch(r'(?i:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])', stem):
        raise ValueError("This preset name is reserved by Windows")
    if len(name) > 120:
        raise ValueError("Preset name is too long")
    return name


def preset_filepath(name, create=False):
    """定位预设文件, 并确认它没有经由符号链接跑到目录之外。"""
    directory = preset_directory(create=create).resolve()
    path = directory / (normalize_name(name) + ".json")
    if path.is_symlink() or path.resolve().parent != directory:
        raise ValueError("Preset path is outside the preset directory")
    return path


def list_presets():
    """列出可用预设名, 按不区分大小写排序。"""
    try:
        directory = preset_directory()
        return sorted((path.stem for path in directory.glob("*.json")
                       if path.is_file() and not path.is_symlink()), key=str.casefold)
    except OSError:
        return []


def save_preset(name, data, overwrite=False):
    """先写临时文件再原子发布, 避免中途失败留下半个预设。"""
    validated_values(data)
    path = preset_filepath(name, create=True)
    if path.exists() and not overwrite:
        raise FileExistsError(path.name)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                         prefix='.qd-', suffix='.tmp', delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        if overwrite:
            os.replace(temp_path, path)
        else:
            # 用硬链接发布: 目标已存在时会失败, 因此不会覆盖别人的同名预设
            os.link(temp_path, path)
        return path.stem
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


class PresetTarget:
    """预设算子的公共部分: 定位编辑目标并把错误翻译成人话。"""

    def target(self, context):
        return target_settings(context, self.session)

    def failure(self, error):
        detail = str(error)
        # 校验失败时抛的是字段标识, 换成界面上的参数名再显示
        if isinstance(error, InvalidParameterError):
            detail = tr_report("Invalid parameter: {name}",
                               name=tr_report(PROPERTY_SPECS[error.field][1]['name']))
        self.report({'ERROR'}, tr_report("Preset operation failed: {detail}", detail=tr_report(detail)))
        return {'CANCELLED'}


def target_properties():
    """预设算子共用的两个隐藏/临时字段。"""
    return {
        'session': StringProperty(options={'HIDDEN', 'SKIP_SAVE'}),
        'name': StringProperty(name="Name", description="Name of the preset",
                               translation_context=CONTEXT, options={'SKIP_SAVE'}),
    }


class QD_OT_preset_save(PresetTarget, Operator):
    bl_idname = "qd.preset_save"
    bl_label = "Save Preset"
    bl_description = "Save the parameters currently shown in this panel or dialog as a JSON preset"
    bl_translation_context = CONTEXT
    bl_options = {'INTERNAL'}
    __annotations__ = {
        **target_properties(),
        'overwrite': BoolProperty(name="Overwrite", description="Replace the existing preset with this name",
                                  translation_context=CONTEXT, options={'SKIP_SAVE'}),
    }

    def invoke(self, context, _event):
        # 默认沿用当前预设名, 覆盖开关每次都从关闭开始
        self.name = self.name or preset_name(context, self.session)
        self.overwrite = False
        return context.window_manager.invoke_props_dialog(self, width=360)

    def draw(self, context):
        self.layout.prop(self, "name")
        self.layout.prop(self, "overwrite")

    def execute(self, context):
        try:
            name = save_preset(self.name, settings_to_dict(self.target(context)), self.overwrite)
            set_preset_name(context, self.session, name)
        except FileExistsError:
            self.report({'WARNING'}, tr_report("Preset already exists; enable Overwrite to replace it"))
            return {'CANCELLED'}
        except (OSError, ValueError, TypeError, ReferenceError) as error:
            return self.failure(error)
        self.report({'INFO'}, tr_report('Saved preset "{name}"', name=name))
        return {'FINISHED'}


class QD_OT_preset_load(PresetTarget, Operator):
    bl_idname = "qd.preset_load"
    bl_label = "Load Preset"
    bl_description = "Load a JSON preset into the parameters currently shown here"
    bl_translation_context = CONTEXT
    bl_options = {'INTERNAL'}
    __annotations__ = target_properties()

    def execute(self, context):
        try:
            path = preset_filepath(self.name)
            # 只读取有限大小的文件, 且只当数据解析, 绝不执行其中内容
            if path.stat().st_size > 1024 * 1024:
                raise ValueError("Preset file is too large")
            with path.open(encoding='utf-8') as handle:
                data = json.load(handle)
            count = dict_to_settings(self.target(context), data)
            set_preset_name(context, self.session, path.stem)
        except (OSError, ValueError, TypeError, ReferenceError, OverflowError) as error:
            return self.failure(error)
        self.report({'INFO'}, tr_report("Loaded {count} parameters", count=count))
        return {'FINISHED'}


class QD_OT_preset_remove(PresetTarget, Operator):
    bl_idname = "qd.preset_remove"
    bl_label = "Remove Preset"
    bl_description = "Permanently delete this JSON preset after confirmation"
    bl_translation_context = CONTEXT
    bl_options = {'INTERNAL'}
    __annotations__ = target_properties()

    def invoke(self, context, _event):
        # 删除不可撤销, 因此先弹确认
        return context.window_manager.invoke_confirm(self, _event)

    def execute(self, context):
        try:
            self.target(context)
            path = preset_filepath(self.name)
            path.unlink()
            # 删掉的正好是当前预设时, 清空显示的名称
            if preset_name(context, self.session) == path.stem:
                set_preset_name(context, self.session, '')
        except (OSError, ValueError, ReferenceError) as error:
            return self.failure(error)
        self.report({'INFO'}, tr_report('Removed preset "{name}"', name=path.stem))
        return {'FINISHED'}


class QD_OT_preset_open_directory(Operator):
    bl_idname = "qd.preset_open_directory"
    bl_label = "Open Preset Directory"
    bl_description = "Open the folder containing the shared JSON presets"
    bl_translation_context = CONTEXT
    bl_options = {'INTERNAL'}

    def execute(self, context):
        try:
            bpy.ops.wm.path_open(filepath=str(preset_directory(create=True)))
        except (OSError, RuntimeError) as error:
            self.report({'ERROR'}, tr_report("Preset operation failed: {detail}", detail=tr_report(str(error))))
            return {'CANCELLED'}
        return {'FINISHED'}


class QD_MT_presets(Menu):
    """预设下拉菜单: 保存、逐个加载、逐个删除、打开目录。"""

    bl_idname = "QD_MT_presets"
    bl_label = "Presets"
    bl_translation_context = CONTEXT

    def draw(self, context):
        layout = self.layout
        owner = getattr(context, 'qd_preset_target', None)
        token = getattr(owner, 'preset_session', '')
        row = layout.row()
        row.operator_context = 'INVOKE_DEFAULT'
        save = row.operator("qd.preset_save", text="Save Preset...", text_ctxt=CONTEXT, icon='ADD')
        save.session = token
        save.name = preset_name(context, token)
        names = list_presets()
        layout.separator()
        for name in names:
            # 加载立即执行, 删除需要确认
            row = layout.row(align=True)
            row.operator_context = 'EXEC_DEFAULT'
            load = row.operator("qd.preset_load", text=name, translate=False, icon='PRESET')
            load.name, load.session = name, token
            remove_row = row.row(align=True)
            remove_row.operator_context = 'INVOKE_DEFAULT'
            remove = remove_row.operator("qd.preset_remove", text="", icon='X')
            remove.name, remove.session = name, token
        if not names:
            layout.label(text="No presets yet", text_ctxt=CONTEXT)
        layout.separator()
        layout.operator("qd.preset_open_directory", icon='FILE_FOLDER')


def draw_presets(layout, context, owner, token=""):
    """预设入口: 预设名菜单加一个快捷保存按钮。"""
    # 把编辑目标挂到 context 上, 菜单绘制时再取出来
    layout.context_pointer_set("qd_preset_target", owner)
    row = layout.row(align=True)
    name = preset_name(context, token)
    row.menu("QD_MT_presets", text=name or tr("Presets"), translate=False, icon='PRESET')
    row.operator_context = 'INVOKE_DEFAULT'
    save = row.operator("qd.preset_save", text="", icon='ADD')
    save.session = token
    save.name = name


CLASSES = (QD_OT_preset_save, QD_OT_preset_load, QD_OT_preset_remove,
           QD_OT_preset_open_directory, QD_MT_presets)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    SESSIONS.clear()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
