"""生成算子 - 读取界面参数调用阶段 API, 选中产物并同步侧栏, 失败时报告可翻译消息"""

import traceback
from typing import ClassVar

from bpy.props import StringProperty
from bpy.types import Operator

from ...common.blender_context import restore_selection
from ...common.errors import InvalidParameterError
from ...i18n import tr_report
from .. import settings
from ..api import generate_fragments
from ..errors import MeshFractureError
from ..settings import CONTEXT, describe_error, property_definitions
from . import panel, presets


class MESH_FRACTURE_OT_generate_fragments(Operator):
    """根据当前参数生成网格碎块, 保留源物体并支持撤销"""

    bl_idname = "mesh_fracture.generate_fragments"
    bl_label = "Generate Fragments"
    bl_description = "Generate fragments from selected editable meshes; source objects are kept"
    bl_translation_context = CONTEXT
    # 参数预设由自定义 JSON 菜单处理,几何操作支持撤销;
    # ClassVar 说明这是 Blender 读取的类级配置, 不随实例变化
    bl_options: ClassVar[set[str]] = {"UNDO"}

    # 属性与 PropertyGroup 共用同一份定义, 避免两处接口分叉;
    # preset_session 是隐藏字段, 只用于把对话框预设指向本次对话框而非侧栏
    __annotations__ = {
        **property_definitions(),
        "preset_session": StringProperty(options={"HIDDEN", "SKIP_SAVE"}),
    }

    # 菜单入口不使用 poll: 无选中网格时由阶段 API 报出具体原因

    def execute(self, context):
        """执行本算子并按成功或失败返回 Blender 状态"""
        try:
            data = settings.settings_to_dict(self)
            keywords = settings.validated_values(data)
            result = generate_fragments(
                context, context.selected_editable_objects, keywords, keywords["collection_name"]
            )
            objects, seconds = result.shards, result.seconds
            # 交互算子在服务恢复原状态后主动选中本次产物
            restore_selection(context, objects, objects[0])
            # API 返回即提交几何 - 侧栏同步失败不能伪装成未执行
            try:
                settings.dict_to_settings(context.scene.qd_settings.mesh_fracture, data)
                if self.preset_session:
                    context.scene.qd_settings.mesh_fracture.preset_name = presets.preset_name(
                        context, self.preset_session
                    )
            except Exception as error:
                traceback.print_exc()
                self.report(
                    {"WARNING"},
                    tr_report(
                        "Shards were created, but sidebar settings could not be synchronized: {detail}",
                        detail=tr_report(str(error)),
                    ),
                )
            self.report(
                {"INFO"},
                tr_report(
                    "Created {count} shards in {seconds:.2f} s", count=len(objects), seconds=seconds
                ),
            )
            return {"FINISHED"}
        except (MeshFractureError, InvalidParameterError) as error:
            # 两类可预期错误都由 describe_error() 转成消息键与参数:
            # 几何错误的 message 是消息原文, 校验错误的 field 需要换成界面参数名
            message, values = describe_error(error)
            self.report({"ERROR"}, tr_report(message, **values))
        except Exception as error:
            traceback.print_exc()
            self.report(
                {"ERROR"},
                tr_report("Fragment generation failed: {detail}", detail=tr_report(str(error))),
            )
        finally:
            presets.end_session(self.preset_session)
            self.preset_session = ""
        return {"CANCELLED"}

    def invoke(self, context, _event):
        # 调用方显式传入的参数优先, 其余字段从侧栏当前值补齐
        """初始化本次交互并打开参数或确认对话框"""
        owner = context.scene.qd_settings.mesh_fracture
        defaults = settings.operator_keywords(owner)
        for key, value in defaults.items():
            if not self.properties.is_property_set(key):
                setattr(self, key, value)
        presets.end_session(self.preset_session)
        self.preset_session = presets.new_session(self, context)
        return context.window_manager.invoke_props_dialog(self, width=520)

    def cancel(self, _context):
        # 取消时结束会话, 保证对话框里的临时改动不会写回侧栏
        """结束当前对话框会话 - 不写回侧栏参数"""
        presets.end_session(self.preset_session)
        self.preset_session = ""

    def draw(self, context):
        # F9 重做没有活动对话框会话, 此时不显示预设操作, 以免误指到侧栏
        """绘制当前目标的控件并保留独立的启用条件"""
        if self.preset_session:
            presets.draw_presets(self.layout, context, self, self.preset_session)
        # 与侧栏一致使用横向紧凑排布: 参数并排而非每行一个,
        # 这样弹窗天然更宽更矮, 也不会再被"最小宽度"这类设置牵着走
        panel.draw_parameters(self.layout, self)
