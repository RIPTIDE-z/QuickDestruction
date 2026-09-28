"""交互层 - 侧栏面板与生成算子, 参数直接读写场景参数组, 预设界面由框架层提供"""

from typing import ClassVar

from bpy.types import Operator, Panel

from ...common.blender_context import restore_selection
from ...common.translate import tr_report
from ...framework.contract import scene_settings
from ...framework.presets import draw_presets
from ...framework.ui import ParamGroup, ToolOperator, ToolPanel, draw_parameters
from .api import generate_fragments
from .constants import CONTEXT, TOOL_ID
from .settings import SCHEMA

# 参数分组: 每个字段只在这里排布一次, 一行内的字段各自判断启用状态
GROUPS = (
    ParamGroup(
        "Point Source",
        (("source",), ("source_limit", "source_noise"), ("random_seed",), ("cell_scale",)),
        notes=("Seeds are existing geometry positions, not random points",),
    ),
    ParamGroup(
        "Recursive Shatter",
        (
            ("recursion",),
            ("recursion_source_limit", "recursion_clamp"),
            ("recursion_chance", "recursion_chance_select"),
        ),
        notes=(
            "Both seed limits act only below the parent shard vertex count; "
            "larger values change nothing",
        ),
    ),
    ParamGroup(
        "Mesh Data",
        (
            ("use_smooth_faces", "use_sharp_edges", "use_sharp_edges_apply", "use_data_match"),
            ("material_index", "use_interior_vgroup"),
            ("margin", "use_island_split"),
            ("use_remove_original",),
        ),
    ),
    ParamGroup("Object", (("use_recenter",),)),
    ParamGroup("Collection", (("collection_name",), ("shard_prefix", "use_shard_collections"))),
    ParamGroup(
        "Debug",
        (("use_debug_redraw", "use_debug_points", "use_debug_bool"), ("shard_index_padding",)),
    ),
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


def _param_enabled(owner, key):
    """仅在参数对应的执行阶段可达时启用控件"""
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
    if key == "material_index":
        # 不匹配源材质槽时, 索引无法对应最终碎块的材质
        return owner.use_data_match
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
    """网格破碎的侧栏面板 - 预设入口, 全部参数与生成按钮"""

    bl_label = "MeshFracture"
    bl_description = "Cut selected meshes into fragments with a Voronoi pattern"
    bl_translation_context = CONTEXT

    def draw(self, context):
        """依次绘制预设入口, 参数分组与生成按钮"""
        layout = self.layout
        owner = scene_settings(context, TOOL_ID)
        draw_presets(layout, TOOL_ID, owner)
        draw_parameters(layout, owner, SCHEMA, GROUPS, _param_enabled)
        ready = _can_apply(context)
        actions = layout.column(align=True)
        actions.enabled = ready
        actions.operator(QD_MESH_FRACTURE_OT_generate.bl_idname, icon="MOD_EXPLODE")
        if not ready:
            # 置灰时说明原因, 否则只看到一个灰按钮
            layout.label(
                text="Select editable mesh objects in Object Mode", text_ctxt=CONTEXT, icon="INFO"
            )
