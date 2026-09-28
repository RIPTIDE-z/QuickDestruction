"""交互层 - 侧栏面板, 参数子面板与生成算子, 参数直接读写场景参数组"""

from typing import ClassVar

from bpy.types import Operator, Panel

from ...common.blender_context import restore_selection
from ...common.translate import tr, tr_report
from ...framework.contract import scene_settings
from ...framework.operators import ToolOperator
from ...framework.ui import CheckGrid, ParamGroup, ToolPanel, parameter_panels
from .api import generate_fragments, source_issues
from .constants import CONTEXT, TOOL_ID
from .core.diagnosis import NOT_CLOSED, SELF_INTERSECTING
from .settings import SCHEMA


def _source_row(column, heading):
    """切割种子来源的一行: 左侧行标签与其他字段的标签列对齐, 返回右侧放按钮的行"""
    # 0.4 与 Blender 分列布局的标签宽度一致
    split = column.split(factor=0.4, align=True)
    label = split.row()
    label.alignment = "RIGHT"
    label.label(text=heading, translate=False)
    return split.row(align=True)


def _draw_source(layout, owner):
    """切割种子来源子面板: 自身与子物体各一行顶点与粒子按钮, 注解铅笔与物体无关, 归在特殊一行显示全称"""
    column = layout.column(align=True)
    verts, particles = tr(CONTEXT, "Verts"), tr(CONTEXT, "Particles")
    own = _source_row(column, tr(CONTEXT, "Own"))
    own.prop_enum(owner, "source", "VERT_OWN", text=verts, translate=False)
    own.prop_enum(owner, "source", "PARTICLE_OWN", text=particles, translate=False)
    children = _source_row(column, tr(CONTEXT, "Children"))
    children.prop_enum(owner, "source", "VERT_CHILD", text=verts, translate=False)
    children.prop_enum(owner, "source", "PARTICLE_CHILD", text=particles, translate=False)
    annotation = _source_row(column, tr(CONTEXT, "Special"))
    annotation.prop_enum(owner, "source", "PENCIL", text="Annotation Pencil", text_ctxt=CONTEXT)


# 全部参数按用途分进子面板: 种子来源与切割参数最常调整, 递归默认展开, 胞体形状与断面碎块默认收起,
# 输出与调试画成底部的弹出按钮
GROUPS = (
    ParamGroup("Seed Source", ("source",), draw=_draw_source),
    ParamGroup("Cut Parameters", ("source_limit", "source_noise", "random_seed", "margin")),
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
        closed=True,
    ),
    ParamGroup(
        "Output",
        ("collection_name", "shard_prefix", "shard_index_padding"),
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
        "recursion_chance_select",
        "use_keep_parents",
    }
)


def _param_active(owner, key):
    """参数对应的执行阶段可达时才显示为可用, 否则置灰"""
    if key in DEBUG_INACTIVE or key == "recursion":
        return not owner.use_debug_bool
    recursion_enabled = owner.recursion > 0 and not owner.use_debug_bool
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


# 源网格问题提示框的宽度, 单位是像素
DIALOG_WIDTH = 420


class QD_MESH_FRACTURE_OT_generate(ToolOperator, Operator):
    """按侧栏参数切割选中的网格, 保留源物体并支持撤销"""

    bl_idname = "qd_mesh_fracture.generate"
    bl_label = "Generate Fragments"
    bl_description = "Generate fragments from selected editable meshes; source objects are kept"
    bl_translation_context = CONTEXT
    # UNDO 让整次生成进入撤销栈; INTERNAL 只由侧栏按钮调用, 不进入算子搜索, 也没有重做面板
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}
    # 提示框里列出的问题清单, 由 invoke 填写
    issues: ClassVar[list] = []

    def invoke(self, context, _event):
        """源网格不封闭或自相交时先弹窗说明后果, 由用户决定是否继续; 没有问题时直接切割"""
        found = source_issues(context, context.selected_editable_objects)
        if not found:
            return self.execute(context)
        # 弹窗绘制时要用到的问题清单: [(物体名, 是否不封闭, 是否自相交), ...]
        self.issues = [
            (obj.name, NOT_CLOSED in issues, SELF_INTERSECTING in issues)
            for obj, issues in found.items()
        ]
        return context.window_manager.invoke_props_dialog(
            self,
            width=DIALOG_WIDTH,
            title=tr(CONTEXT, "Source Mesh Problems"),
            confirm_text=tr(CONTEXT, "Cut Anyway"),
            translate=False,
        )

    def draw(self, _context):
        """弹窗内容: 列出有问题的物体, 说明后果与建议"""
        layout = self.layout
        column = layout.column(align=True)
        for name, not_closed, intersecting in self.issues:
            row = column.row()
            # 靠左排列, 物体名与问题紧挨着, 不被拉开到整行
            row.alignment = "LEFT"
            row.label(text=name, icon="ERROR", translate=False)
            if not_closed:
                row.label(text="Not closed", text_ctxt=CONTEXT)
            if intersecting:
                row.label(text="Self-intersecting", text_ctxt=CONTEXT)
        column = layout.column(align=True)
        column.label(text="Meshes that are not closed have no clear inside:", text_ctxt=CONTEXT)
        column.label(text="shards can have holes or open faces.", text_ctxt=CONTEXT)
        column.label(
            text="Self-intersecting parts can make the cut lose geometry.", text_ctxt=CONTEXT
        )
        column.separator()
        column.label(
            text="Close holes or use Voxel Remesh first for clean results.", text_ctxt=CONTEXT
        )

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
    """网格破碎的侧栏主面板 - 生成按钮, 预设入口在标题栏, 全部参数在子面板"""

    bl_label = "MeshFracture"
    bl_description = "Cut selected meshes into fragments with a Voronoi pattern"
    bl_translation_context = CONTEXT
    tool_id = TOOL_ID

    def draw(self, context):
        """生成按钮与未就绪提示; 参数都在下方的子面板里"""
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


# 参数子面板与弹出面板: 子面板挂在主面板下按分组顺序排列, 弹出按钮并排在最底部
SUBPANELS = parameter_panels(QD_MESH_FRACTURE_PT_main, SCHEMA, (), GROUPS, _param_active)
