"""交互层 - 侧栏面板, 参数子面板与生成算子, 参数直接读写场景参数组"""

from typing import ClassVar

from bpy.types import Operator, Panel

from ...common.blender_context import restore_selection
from ...common.translate import tr_report
from ...framework.contract import scene_settings
from ...framework.operators import ToolOperator
from ...framework.ui import ParamGroup, ToolPanel, draw_fields, parameter_panels
from .api import generate_fragments
from .constants import CONTEXT, TOOL_ID
from .settings import SCHEMA

# 主面板上的常用参数, 一行一个, 始终可见
CORE_FIELDS = ("source", "source_limit", "source_noise", "recursion", "margin")
# 其余参数按用途分进子面板; 不常调的默认收起
GROUPS = (
    ParamGroup("Pattern", ("random_seed", "cell_scale"), closed=True),
    ParamGroup(
        "Recursive Shatter",
        (
            "recursion_source_limit",
            "recursion_clamp",
            "recursion_chance",
            "recursion_chance_select",
            "use_remove_original",
        ),
    ),
    ParamGroup(
        "Mesh Data",
        (
            "use_smooth_faces",
            "use_sharp_edges",
            "use_sharp_edges_apply",
            "use_interior_vgroup",
            "use_island_split",
            "use_recenter",
        ),
    ),
    ParamGroup("Match Data", ("material_index",), toggle="use_data_match"),
    ParamGroup(
        "Output",
        ("collection_name", "shard_prefix", "use_shard_collections", "shard_index_padding"),
        closed=True,
    ),
    ParamGroup("Debug", ("use_debug_redraw", "use_debug_points", "use_debug_bool"), closed=True),
)
# 保留布尔修改器时, 收尾与递归阶段都不执行
DEBUG_INACTIVE = frozenset(
    {"use_sharp_edges", "use_sharp_edges_apply", "use_interior_vgroup", "use_island_split"}
)
# 只被递归阶段读取的参数
RECURSION_DEPENDENT = frozenset(
    {
        "recursion_source_limit",
        "recursion_clamp",
        "recursion_chance",
        "use_remove_original",
        "use_shard_collections",
    }
)


def _param_active(owner, key):
    """参数对应的执行阶段可达时才显示为可用, 否则置灰"""
    # 拆分是锐边标记的后续动作, 必须排在 DEBUG_INACTIVE 之前判断
    if key == "use_sharp_edges_apply":
        return owner.use_sharp_edges and not owner.use_debug_bool
    if key in DEBUG_INACTIVE or key == "recursion":
        return not owner.use_debug_bool
    recursion_enabled = owner.recursion > 0 and not owner.use_debug_bool
    if key == "recursion_chance_select":
        # 选择方式仅在比例严格介于 0 与 1 时才参与
        return recursion_enabled and 0 < owner.recursion_chance < 1
    if key in RECURSION_DEPENDENT:
        return recursion_enabled
    return True


def _can_apply(context):
    """仅在物体模式且选中可编辑网格时启用生成按钮"""
    return (
        context.mode == "OBJECT"
        and context.scene is not None
        and any(obj.type == "MESH" for obj in context.selected_editable_objects)
    )


class QD_MESH_FRACTURE_OT_generate(ToolOperator, Operator):
    """按侧栏参数切割选中的网格, 保留源物体并支持撤销"""

    bl_idname = "qd_mesh_fracture.generate"
    bl_label = "Generate Fragments"
    bl_description = "Generate fragments from selected editable meshes; source objects are kept"
    bl_translation_context = CONTEXT
    # UNDO 让整次生成进入撤销栈; INTERNAL 只由侧栏按钮调用, 不进入算子搜索, 也没有重做面板
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def run(self, context):
        """读取场景参数调用阶段 API, 成功后选中本次生成的碎块"""
        data = SCHEMA.read(scene_settings(context, TOOL_ID))
        result = generate_fragments(context, context.selected_editable_objects, data)
        restore_selection(context, result.shards, result.shards[0])
        return tr_report(
            CONTEXT,
            "Created {count} shards in {seconds:.2f} s",
            count=len(result.shards),
            seconds=result.seconds,
        )


class QD_MESH_FRACTURE_PT_main(ToolPanel, Panel):
    """网格破碎的侧栏主面板 - 生成按钮与常用参数, 预设入口在标题栏, 其余参数在子面板"""

    bl_label = "MeshFracture"
    bl_description = "Cut selected meshes into fragments with a Voronoi pattern"
    bl_translation_context = CONTEXT
    tool_id = TOOL_ID

    def draw(self, context):
        """生成按钮放在最上面, 打开面板就能看到; 其下是常用参数"""
        layout = self.layout
        ready = _can_apply(context)
        button = layout.column()
        button.enabled = ready
        button.scale_y = 1.4
        button.operator(QD_MESH_FRACTURE_OT_generate.bl_idname, icon="MOD_EXPLODE")
        if not ready:
            # 置灰时说明原因, 否则只看到一个灰按钮
            layout.label(
                text="Select editable mesh objects in Object Mode", text_ctxt=CONTEXT, icon="INFO"
            )
        owner = scene_settings(context, TOOL_ID)
        draw_fields(layout, owner, SCHEMA, CORE_FIELDS, _param_active)


# 参数子面板, 挂在主面板下, 按分组顺序排列
SUBPANELS = parameter_panels(QD_MESH_FRACTURE_PT_main, SCHEMA, CORE_FIELDS, GROUPS, _param_active)
