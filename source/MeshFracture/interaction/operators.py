"""生成算子 - 读取界面参数调用阶段 API, 选中产物并同步侧栏, 失败时报告可翻译消息"""

import traceback
from typing import ClassVar

from bpy.types import Operator

from ...common.blender_context import restore_selection
from ...common.errors import InvalidParameterError
from ...common.translate import tr_report
from .. import settings
from ..api import generate_fragments
from ..errors import MeshFractureError
from ..settings import CONTEXT, describe_error, property_definitions, scene_settings


class MESH_FRACTURE_OT_generate_fragments(Operator):
    """根据当前参数生成网格碎块, 保留源物体并支持撤销"""

    bl_idname = "mesh_fracture.generate_fragments"
    bl_label = "Generate Fragments"
    bl_description = "Generate fragments from selected editable meshes; source objects are kept"
    bl_translation_context = CONTEXT
    # UNDO: 几何改动进入撤销栈, 用 Ctrl+Z 回退;
    # INTERNAL: 只由侧栏面板的按钮执行, 不进入算子搜索, 也就没有重做面板;
    # ClassVar 说明这是 Blender 读取的类级配置, 不随实例变化
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    # 属性与 PropertyGroup 共用同一份定义, 避免两处接口分叉
    __annotations__ = property_definitions()

    # 不使用 poll: 按钮的可用性由侧栏面板判断, 这里让阶段 API 报出具体原因

    def execute(self, context):
        """按当前参数切割选中物体, 成功后选中产物并把参数回写侧栏"""
        try:
            # 算子字段是本次调用的参数快照: 侧栏按钮把自己的值写进来
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
                settings.dict_to_settings(scene_settings(context), data)
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
        return {"CANCELLED"}
