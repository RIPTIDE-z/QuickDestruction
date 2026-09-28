"""交互层 - 侧栏面板, 参数子面板与生成算子, 参数直接读写场景参数组"""

from typing import ClassVar

from bpy.types import Operator, Panel

from ...common.blender_context import restore_selection
from ...common.translate import tr, tr_report
from ...framework.contract import scene_settings
from ...framework.operators import ToolOperator
from ...framework.ui import CheckGrid, ParamGroup, ToolPanel, draw_fields, parameter_panels
from .api import generate_fragments
from .constants import CONTEXT, TOOL_ID
from .settings import SCHEMA

# 主面板上每次破碎都会调整的参数, 画在来源之下; 随机种子是换一种结果最常用的入口
CORE_FIELDS = ("source_limit", "source_noise", "random_seed", "margin")
# 其余参数按用途分组: 递归默认展开, 胞体形状默认收起, 输出与调试画成底部的弹出按钮
GROUPS = (
    ParamGroup(
        "Recursive Shatter",
        (
            "recursion",
            "recursion_source_limit",
            "recursion_clamp",
            "recursion_chance",
            "recursion_chance_select",
            "use_keep_parents",
        ),
    ),
    ParamGroup("Cell Shape", ("cell_scale",), closed=True),
    ParamGroup(
        "Interior & Shards",
        (
            "material_index",
            CheckGrid(("use_smooth_faces", "use_sharp_edges", "use_interior_vgroup")),
            CheckGrid(("use_island_split", "use_recenter")),
        ),
    ),
    ParamGroup(
        "Output",
        ("collection_name", "shard_prefix", "shard_index_padding", "use_shard_collections"),
        popover=True,
    ),
    ParamGroup("Debug", ("use_debug_redraw", "use_debug_points", "use_debug_bool"), popover=True),
)
# 保留布尔修改器时, 收尾与递归阶段都不执行
DEBUG_INACTIVE = frozenset({"use_sharp_edges", "use_interior_vgroup", "use_island_split"})
# 只被递归阶段读取的参数
RECURSION_DEPENDENT = frozenset(
    {
        "recursion_source_limit",
        "recursion_clamp",
        "recursion_chance",
        "use_keep_parents",
        "use_shard_collections",
    }
)


def _param_active(owner, key):
    """参数对应的执行阶段可达时才显示为可用, 否则置灰"""
    if key in DEBUG_INACTIVE or key == "recursion":
        return not owner.use_debug_bool
    recursion_enabled = owner.recursion > 0 and not owner.use_debug_bool
    if key == "recursion_chance_select":
        # 优先细分仅在每轮细分比例严格介于 0 与 1 时才参与
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


def _source_row(column, heading):
    """来源的一行: 左侧行标签与下方字段的标签列对齐, 返回右侧放按钮的行"""
    # 0.4 与 Blender 分列布局的标签宽度一致
    split = column.split(factor=0.4, align=True)
    label = split.row()
    label.alignment = "RIGHT"
    label.label(text=heading, translate=False)
    return split.row(align=True)


def _draw_source(layout, owner):
    """来源: 标题一行, 自身与子物体各一行顶点与粒子按钮, 注解铅笔与物体无关, 归在特殊一行显示全称"""
    column = layout.column(align=True)
    column.label(text="Seed Source", text_ctxt=CONTEXT)
    verts, particles = tr(CONTEXT, "Verts"), tr(CONTEXT, "Particles")
    own = _source_row(column, tr(CONTEXT, "Own"))
    own.prop_enum(owner, "source", "VERT_OWN", text=verts, translate=False)
    own.prop_enum(owner, "source", "PARTICLE_OWN", text=particles, translate=False)
    children = _source_row(column, tr(CONTEXT, "Children"))
    children.prop_enum(owner, "source", "VERT_CHILD", text=verts, translate=False)
    children.prop_enum(owner, "source", "PARTICLE_CHILD", text=particles, translate=False)
    annotation = _source_row(column, tr(CONTEXT, "Special"))
    annotation.prop_enum(owner, "source", "PENCIL", text="Annotation Pencil", text_ctxt=CONTEXT)


class QD_MESH_FRACTURE_PT_main(ToolPanel, Panel):
    """网格破碎的侧栏主面板 - 生成按钮与常用参数, 预设入口在标题栏, 其余参数在子面板"""

    bl_label = "MeshFracture"
    bl_description = "Cut selected meshes into fragments with a Voronoi pattern"
    bl_translation_context = CONTEXT
    tool_id = TOOL_ID

    def draw(self, context):
        """生成按钮放在最上面, 打开面板就能看到; 其下是来源与常用参数"""
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
        _draw_source(layout, owner)
        draw_fields(layout, owner, SCHEMA, CORE_FIELDS, _param_active)


# 参数子面板与弹出面板: 子面板挂在主面板下按分组顺序排列, 弹出按钮并排在最底部
SUBPANELS = parameter_panels(
    QD_MESH_FRACTURE_PT_main, SCHEMA, ("source", *CORE_FIELDS), GROUPS, _param_active
)
