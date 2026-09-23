"""生成算子 - 读取界面参数调用阶段 API, 选中产物并同步侧栏, 失败时报告可翻译消息"""

import traceback
from typing import ClassVar

from bpy.types import Operator

from ... import preset_ui
from ...common.blender_context import restore_selection
from ...common.errors import InvalidParameterError
from ...common.translate import tr_report
from .. import settings
from ..api import generate_fragments
from ..errors import MeshFractureError
from ..settings import CONTEXT, PRESET, describe_error, property_definitions, scene_settings
from . import panel


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
        "preset_session": preset_ui.session_property(),
    }

    # 菜单入口不使用 poll: 无选中网格时由阶段 API 报出具体原因

    def execute(self, context):
        """按当前参数切割选中物体, 成功后选中产物并把参数回写侧栏"""
        try:
            # 算子字段是本次调用的参数快照: 侧栏按钮与对话框都会把自己的值写进来
            data = settings.settings_to_dict(self)
            keywords = settings.validated_values(data)
            # collection_name 作为输出集合显式传给 API, API 不再从配置里隐式取
            result = generate_fragments(
                context, context.selected_editable_objects, keywords, keywords["collection_name"]
            )
            objects, seconds = result.shards, result.seconds
            # 交互算子在服务恢复原状态后主动选中本次产物
            restore_selection(context, objects, objects[0])
            # API 返回即提交几何 - 侧栏同步失败不能伪装成未执行
            try:
                owner = scene_settings(context)
                settings.dict_to_settings(owner, data)
                if self.preset_session:
                    # 对话框里的改动只写回名称, 参数本身不覆盖侧栏
                    owner.preset_name = preset_ui.preset_name(self.preset_session)
            except Exception as error:
                # 几何已经生成, 这里只提示同步失败, 不做回滚
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
            preset_ui.end_session(self.preset_session)
            self.preset_session = ""
        return {"CANCELLED"}

    def invoke(self, context, _event):
        """用侧栏当前值补齐未显式设置的字段, 建立预设会话并打开参数对话框

        调用方显式传入的参数优先, 用 is_property_set 判断,
        使 F9 重做时带上上次的参数而不是被侧栏覆盖
        """
        owner = scene_settings(context)
        defaults = settings.operator_keywords(owner)
        for key, value in defaults.items():
            if not self.properties.is_property_set(key):
                setattr(self, key, value)
        # 会话令牌把本次对话框与侧栏区分开, 预设算子据此定位读写目标
        preset_ui.end_session(self.preset_session)
        self.preset_session = preset_ui.new_session(PRESET, self, context, owner.preset_name)
        return context.window_manager.invoke_props_dialog(self, width=520)

    def cancel(self, _context):
        """结束对话框会话 - 保证未确认的临时改动不会写回侧栏"""
        preset_ui.end_session(self.preset_session)
        self.preset_session = ""

    def draw(self, context):
        """绘制预设入口与全部参数, 与侧栏共用同一套紧凑布局

        F9 重做没有活动会话, 此时不画预设操作, 以免误指到侧栏
        """
        if self.preset_session:
            preset_ui.draw_presets(self.layout, context, PRESET, self, self.preset_session)
        # 参数并排而非每行一个, 弹窗因此更宽更矮
        panel.draw_parameters(self.layout, self)
