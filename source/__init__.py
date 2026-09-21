"""QuickDestruction 扩展入口: 注册算子、属性、侧栏面板、预设与翻译。"""

import traceback

import bpy
from bpy.app.handlers import persistent
from bpy.props import StringProperty
from bpy.types import Operator

from . import qd_panel, qd_presets, qd_settings
from .qd_pipeline import FractureError, main
from .qd_properties import CONTEXT, PROPERTY_SPECS, property_definitions
from .qd_translations import TRANSLATIONS_DOMAIN, translations_dict, tr


class OBJECT_OT_add_quick_destruction_objects(Operator):
    """按当前参数把选中的网格切成碎块; 源物体保留, 结果可撤销。"""

    bl_idname = "object.add_quick_destruction_objects"
    bl_label = "Quick Destruction Selected Mesh Objects"
    bl_description = "Cut selected editable meshes into shards; source objects are kept"
    bl_translation_context = CONTEXT
    # PRESET 与上游一致, 用于对话框里那套原生预设按钮
    bl_options = {'PRESET', 'UNDO'}

    # 属性与 PropertyGroup 共用同一份定义, 避免两处接口分叉;
    # preset_session 是隐藏字段, 只用于把对话框预设指向本次对话框而非侧栏
    __annotations__ = {
        **property_definitions(),
        'preset_session': StringProperty(options={'HIDDEN', 'SKIP_SAVE'}),
    }

    # 这里刻意不定义 poll()。上游 Cell Fracture 同样没有 poll, 菜单项因此任何时候
    # 都可点; 一旦加上"必须选中网格"这类条件, Blender 会把它在菜单里置灰,
    # 未选择物体时连点都点不了。选不到物体的情形改由 execute 走正常报错路径,
    # 由 main() 抛出可翻译的 "Select editable mesh objects in Object Mode"。

    def execute(self, context):
        try:
            data = qd_settings.settings_to_dict(self)
            keywords = qd_settings.validated_values(data)
            objects, seconds = main(context, **keywords)
            # 切割成功后把实际生效的参数同步回侧栏, 便于接着微调
            qd_settings.dict_to_settings(context.scene.qd_settings.fracture, data)
            if self.preset_session:
                context.scene.qd_settings.preset_name = qd_presets.preset_name(context, self.preset_session)
            self.report({'INFO'}, tr("Created {count} shards in {seconds:.2f} s",
                                    count=len(objects), seconds=seconds))
            return {'FINISHED'}
        except FractureError as error:
            self.report({'ERROR'}, tr(error.message, **error.values))
        except ValueError as error:
            # 参数校验失败时把字段标识换成可读名称再报错
            field = str(error)
            label = PROPERTY_SPECS.get(field, ('', {'name': field}))[1]['name']
            self.report({'ERROR'}, tr("Invalid parameter: {name}", name=tr(label)))
        except Exception as error:
            traceback.print_exc()
            self.report({'ERROR'}, tr("Fracture failed: {detail}", detail=str(error)))
        finally:
            qd_presets.end_session(self.preset_session)
            self.preset_session = ''
        return {'CANCELLED'}

    def invoke(self, context, event):
        # 调用方显式传入的参数优先, 其余字段从侧栏当前值补齐
        owner = context.scene.qd_settings.fracture
        defaults = qd_settings.operator_keywords(owner)
        for key, value in defaults.items():
            if not self.properties.is_property_set(key):
                setattr(self, key, value)
        qd_presets.end_session(self.preset_session)
        self.preset_session = qd_presets.new_session(self, context)
        # 关于弹窗宽度, Blender 5.2 的实际行为 (见 wm_block_dialog_create):
        #   data->width = width * UI_SCALE_FAC * ...
        #   dialog_width = max(text_width + columnspace*2.5, data->width)
        #   block_layout(..., dialog_width, ...) 里 dir 为纵向时 w = size
        # 也就是说这个 width 只是"布局根的最小尺寸", 最终宽度由内容算出来
        # (block_layout_resolve -> layout_end), 并且会被当前窗口宽度夹住。
        # 真正决定宽度的是内容布局, 这里只给一个普通下限即可。
        return context.window_manager.invoke_props_dialog(self, width=520)

    def cancel(self, context):
        # 取消时结束会话, 保证对话框里的临时改动不会写回侧栏
        qd_presets.end_session(self.preset_session)
        self.preset_session = ''

    def draw(self, context):
        # F9 重做没有活动对话框会话, 此时不显示预设操作, 以免误指到侧栏
        if self.preset_session:
            qd_presets.draw_presets(self.layout, context, self, self.preset_session)
        # 与侧栏一致使用横向紧凑排布: 参数并排而非每行一个,
        # 这样弹窗天然更宽更矮, 也不会再被"最小宽度"这类设置牵着走
        qd_panel.draw_parameters(self.layout, self, compact=True)


@persistent
def clear_sessions(_unused):
    """加载新文件前清空对话框会话, 避免旧会话指向已失效的场景。"""
    qd_presets.SESSIONS.clear()


def menu_func(self, context):
    """3D 视图 物体 -> 快速效果 里的入口, 与上游保持同样的分隔线写法。"""
    layout = self.layout
    layout.separator()
    layout.operator("object.add_quick_destruction_objects",
                    text="Quick Destruction Effect", text_ctxt=CONTEXT)


def register():
    bpy.app.translations.register(TRANSLATIONS_DOMAIN, translations_dict)
    qd_settings.register()
    bpy.utils.register_class(OBJECT_OT_add_quick_destruction_objects)
    qd_presets.register()
    qd_panel.register()
    bpy.types.VIEW3D_MT_object_quick_effects.append(menu_func)
    bpy.app.handlers.load_pre.append(clear_sessions)


def unregister():
    # 逆着注册顺序注销, 先撤掉引用后注册对象的部分
    bpy.app.handlers.load_pre.remove(clear_sessions)
    bpy.types.VIEW3D_MT_object_quick_effects.remove(menu_func)
    qd_panel.unregister()
    qd_presets.unregister()
    bpy.utils.unregister_class(OBJECT_OT_add_quick_destruction_objects)
    qd_settings.unregister()
    bpy.app.translations.unregister(TRANSLATIONS_DOMAIN)
