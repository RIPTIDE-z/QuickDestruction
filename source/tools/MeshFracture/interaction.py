"""交互层 - 侧栏面板, 参数子面板与生成算子, 参数直接读写场景参数组"""

from typing import ClassVar

import bpy
from bpy.types import Operator, Panel

from ...common import asset
from ...common.blender_context import restore_selection
from ...common.translate import tr, tr_report
from ...framework.contract import scene_settings
from ...framework.operators import ToolOperator
from ...framework.ui import (
    CheckGrid,
    Expand,
    ParamGroup,
    ToolPanel,
    active_rule,
    draw_fields,
    label_row,
    parameter_panels,
    primary_button,
)
from .api import (
    convert_parts,
    convert_uv_islands,
    generate_fragments,
    make_asset_unique,
    source_issues,
)
from .constants import CONTEXT, TOOL_ID
from .core.diagnosis import NOT_CLOSED, SELF_INTERSECTING
from .settings import ACTIVE_WHEN, SCHEMA


def _draw_source(layout, owner):
    """切割种子来源子面板: 自身与子物体各一行顶点与粒子按钮, 注解铅笔与物体无关, 归在特殊一行显示全称

    layout 要绘制到的布局
    owner 参数组
    """
    column = layout.column(align=True)
    verts, particles = tr(CONTEXT, "Verts"), tr(CONTEXT, "Particles")
    own = label_row(column, tr(CONTEXT, "Own"), translate=False)
    own.prop_enum(owner, "source", "VERT_OWN", text=verts, translate=False)
    own.prop_enum(owner, "source", "PARTICLE_OWN", text=particles, translate=False)
    children = label_row(column, tr(CONTEXT, "Children"), translate=False)
    children.prop_enum(owner, "source", "VERT_CHILD", text=verts, translate=False)
    children.prop_enum(owner, "source", "PARTICLE_CHILD", text=particles, translate=False)
    annotation = label_row(column, tr(CONTEXT, "Special"), translate=False)
    annotation.prop_enum(owner, "source", "PENCIL", text="Annotation Pencil", text_ctxt=CONTEXT)


# 按 UV 岛拆分子面板的字段
UV_FIELDS = ("uv_map", "use_uv_selected", Expand("uv_unsplit"), "strip_uv_scale")


def _draw_uv(layout, owner):
    """按 UV 岛拆分子面板: 字段之外, 开启仅拆选中部件时提示选择来自编辑模式

    物体模式下看不到面的选择, 不提示的话用户会以为选择是空的, 或者忘了还留着很久以前的选择
    layout 要绘制到的布局
    owner 参数组
    """
    draw_fields(layout, owner, SCHEMA, UV_FIELDS, _param_active)
    if owner.use_uv_selected:
        layout.label(text="Uses the faces selected in Edit Mode", text_ctxt=CONTEXT, icon="INFO")


def _page(*methods):
    """分组的显示条件: 破碎方式是 methods 之一时显示

    methods 破碎方式的枚举值
    返回 条件函数 show(owner)
    """
    return lambda owner: owner.method in methods


# 全部参数按用途分进子面板, 每个子面板只在用到它的破碎方式页里出现:
# 切割页是种子来源, 切割参数, 递归与胞体形状; 部件页与 UV 岛页各一个子面板; 断面与碎块各页共用
# 输出与调试画成底部的弹出按钮, 调试只属于切割
GROUPS = (
    ParamGroup("Seed Source", ("source",), draw=_draw_source, show=_page("CUT")),
    ParamGroup(
        "Cut Parameters",
        (
            "first_cut_seeds",
            "recursion_clamp",
            "source_noise",
            "random_seed",
            "margin",
            CheckGrid(("use_fill",)),
        ),
        show=_page("CUT"),
    ),
    ParamGroup(
        "Recursive Shatter",
        (
            "recursion",
            "sub_cut_pieces",
            "recursion_chance",
            "recursion_chance_select",
            "use_keep_parents",
        ),
        show=_page("CUT"),
    ),
    ParamGroup("Cell Shape", ("cell_scale",), closed=True, show=_page("CUT")),
    ParamGroup(
        "From UV Islands",
        UV_FIELDS,
        draw=_draw_uv,
        show=_page("UV_ISLANDS"),
    ),
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
        ("collection_name", "hide_source", "shard_suffix", "shard_index_padding"),
        popover=True,
    ),
    ParamGroup(
        "Debug",
        ("use_debug_redraw", "use_debug_points", "use_debug_bool"),
        popover=True,
        show=_page("CUT"),
    ),
)

# 共用子面板里只对部分破碎方式生效的字段: 字段名 -> 生效的破碎方式; 没登记的字段各页都生效
FIELD_METHODS = {
    "material_index": {"CUT", "PARTS", "UV_ISLANDS"},
    "use_smooth_faces": {"CUT", "UV_ISLANDS"},
    "use_sharp_edges": {"CUT", "UV_ISLANDS"},
    "use_interior_vgroup": {"CUT", "PARTS", "UV_ISLANDS"},
    "use_island_split": {"CUT"},
}

# 切割页的生效条件: 条件不成立时置灰但仍可编辑; 规则与计算层共用, 见 settings.ACTIVE_WHEN
_cut_active = active_rule(ACTIVE_WHEN)


def _param_active(owner, key):
    """字段是否生效: 先看当前破碎方式用不用它, 切割页再按切割的生效条件判断

    settings.ACTIVE_WHEN 的条件都是切割流水线的条件, 例如保留布尔修改器时跳过断面收尾,
    对另外两种方式不成立, 所以只在切割页使用
    owner 参数组
    key 字段名
    返回 生效为真
    """
    if owner.method not in FIELD_METHODS.get(key, {owner.method}):
        return False
    return owner.method != "CUT" or _cut_active(owner, key)


# 各破碎方式能在哪些模式下执行: 按 UV 岛拆分要读编辑模式里的面选择, 也接受编辑模式
METHOD_MODES = {"CUT": {"OBJECT"}, "PARTS": {"OBJECT"}, "UV_ISLANDS": {"OBJECT", "EDIT_MESH"}}


def _can_apply(context, method="CUT"):
    """当前模式能执行该破碎方式且选中了可编辑网格时启用生成按钮

    context Blender 上下文
    method 破碎方式
    返回 可以生成为真
    """
    return (
        context.mode in METHOD_MODES[method]
        and context.scene is not None
        and any(obj.type == "MESH" for obj in context.selected_editable_objects)
    )


# 源网格问题提示框的宽度, 单位是像素
DIALOG_WIDTH = 420
# 重叠告警里最多列出的碎块对数
OVERLAP_NAMES_SHOWN = 5


class QD_MESH_FRACTURE_OT_generate(ToolOperator, Operator):
    """按侧栏参数切割选中的网格, 保留源物体 (默认隐藏) 并支持撤销"""

    bl_idname = "qd_mesh_fracture.generate"
    bl_label = "Generate Fragments"
    bl_description = (
        "Generate fragments from selected editable meshes; source objects are kept and, by "
        "default, hidden"
    )
    bl_translation_context = CONTEXT
    # UNDO 让整次生成进入撤销栈; INTERNAL 只由侧栏按钮调用, 不进入算子搜索, 也没有重做面板
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}
    # 提示框里列出的问题清单, 由 invoke 填写
    issues: ClassVar[list] = []

    def invoke(self, context, _event):
        """源网格不封闭或自相交时先弹窗说明后果, 由用户决定是否继续; 没有问题时直接切割

        context Blender 上下文
        _event 触发事件, 不使用
        返回 算子返回集合
        """
        # 体检出错也走失败报告, 不以原始异常出现
        try:
            found = source_issues(context, context.selected_editable_objects)
        except Exception as error:
            return self.fail(error)
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
        """弹窗内容: 列出有问题的物体, 说明后果与建议

        _context Blender 上下文, 不使用
        """
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
        """读取场景参数调用阶段 API, 成功后选中本次生成的碎块

        context Blender 上下文
        返回 报告文字
        """
        data = SCHEMA.read(scene_settings(context, TOOL_ID))
        result = generate_fragments(context, context.selected_editable_objects, data)
        restore_selection(context, result.shards, result.shards[0])
        return tr_report(
            CONTEXT,
            "Created {count} shards in {seconds:.2f} s",
            count=len(result.shards),
            seconds=result.seconds,
        )


class QD_MESH_FRACTURE_OT_parts(ToolOperator, Operator):
    """把模型自带的部件直接当作碎块, 不切割"""

    bl_idname = "qd_mesh_fracture.parts"
    bl_label = "Use Parts as Shards"
    bl_description = (
        "Turn the parts the model already has into shards without cutting: the connected "
        "parts of one selected mesh, or each of several selected objects"
    )
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def run(self, context):
        """读取场景参数调用阶段 API, 成功后选中本次生成的碎块

        context Blender 上下文
        返回 报告文字
        """
        data = SCHEMA.read(scene_settings(context, TOOL_ID))
        result = convert_parts(context, context.selected_editable_objects, data)
        restore_selection(context, result.shards, result.shards[0])
        message = tr_report(
            CONTEXT,
            "Created {count} shards from parts in {seconds:.2f} s",
            count=len(result.shards),
            seconds=result.seconds,
        )
        if not result.overlaps:
            return message
        # 重叠只告警不修复: 模拟里重叠的碰撞体会被猛地推开, 要让用户知道是哪几对
        names = ", ".join(f"{a.name} / {b.name}" for a, b in result.overlaps[:OVERLAP_NAMES_SHOWN])
        if len(result.overlaps) > OVERLAP_NAMES_SHOWN:
            names += ", ..."
        self.report(
            {"WARNING"},
            message
            + " "
            + tr_report(
                CONTEXT,
                "{count} pairs of shards overlap and may push each other apart in the "
                "simulation: {names}",
                count=len(result.overlaps),
                names=names,
            ),
        )
        return None


class QD_MESH_FRACTURE_OT_uv_islands(ToolOperator, Operator):
    """把每个 UV 岛当作一块碎片, 不切割"""

    bl_idname = "qd_mesh_fracture.uv_islands"
    bl_label = "Split by UV Islands"
    bl_description = (
        "Turn each UV island of the selected meshes into a shard without cutting; matching "
        "outer and inner islands of a thin shell are joined and closed with side walls"
    )
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def run(self, context):
        """读取场景参数调用阶段 API, 成功后选中本次生成的碎块

        context Blender 上下文
        返回 报告文字
        """
        # 编辑模式里的面选择要回到物体模式才写进网格数据, 拆分也只能在物体模式进行
        from_edit = context.mode == "EDIT_MESH"
        if from_edit:
            bpy.ops.object.mode_set(mode="OBJECT")
        data = SCHEMA.read(scene_settings(context, TOOL_ID))
        try:
            result = convert_uv_islands(context, context.selected_editable_objects, data)
        except Exception:
            # 失败时回到用户原来的编辑模式, 选择也还在, 改了参数可以直接再点
            if from_edit:
                bpy.ops.object.mode_set(mode="EDIT")
            raise
        restore_selection(context, result.shards, result.shards[0])
        message = tr_report(
            CONTEXT,
            "Created {count} shards from UV islands, {split} of {parts} parts split, "
            "in {seconds:.2f} s",
            count=len(result.shards),
            split=result.split_parts,
            parts=result.total_parts,
            seconds=result.seconds,
        )
        if not result.open_shards:
            return message
        # 应当封闭却没有封闭的碎块要明确提示, 它们的侧壁有缺口, 物理与导出时可能出问题
        names = ", ".join(shard.name for shard in result.open_shards)
        self.report(
            {"WARNING"},
            message
            + " "
            + tr_report(
                CONTEXT,
                "{count} shards could not be closed: {names}",
                count=len(result.open_shards),
                names=names,
            ),
        )
        return None


class QD_MESH_FRACTURE_OT_make_unique(ToolOperator, Operator):
    """给复制出来的资产换一个新标识, 原资产保持不变"""

    bl_idname = "qd_mesh_fracture.make_unique"
    bl_label = "Make Asset Unique"
    bl_description = (
        "Give the duplicated asset of the active object its own identity; its physics and "
        "simulation results are cleared and must be applied again"
    )
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def run(self, context):
        """改写活动物体所在资产的标识

        context Blender 上下文
        返回 报告文字
        """
        root = make_asset_unique(context.active_object)
        return tr_report(CONTEXT, 'Asset "{name}" now has its own identity', name=root.name)


def _draw_duplicate_warning(layout, context):
    """活动物体所在的资产被复制过时, 提示原因并给出改正按钮; 其他时候不占位置

    layout 要绘制到的布局
    context Blender 上下文
    """
    root = asset.duplicated_root(context.active_object)
    if root is None:
        return
    box = layout.box()
    box.label(text="This asset was duplicated", text_ctxt=CONTEXT, icon="ERROR")
    box.label(text="Other tools refuse to work on it until it is unique", text_ctxt=CONTEXT)
    box.operator(QD_MESH_FRACTURE_OT_make_unique.bl_idname, icon="DUPLICATE")


class QD_MESH_FRACTURE_PT_main(ToolPanel, Panel):
    """网格破碎的侧栏主面板 - 生成按钮, 预设入口在标题栏, 全部参数在子面板"""

    bl_label = "MeshFracture"
    bl_description = "Cut selected meshes into fragments with a Voronoi pattern"
    bl_translation_context = CONTEXT
    tool_id = TOOL_ID

    def draw(self, context):
        """破碎方式切换, 当前方式的生成按钮与未就绪提示; 参数都在下方属于这一页的子面板里

        context Blender 上下文
        """
        layout = self.layout
        owner = scene_settings(context, TOOL_ID)
        # 破碎方式画成并排的页签按钮, 切换后下面的按钮与子面板跟着换
        row = layout.row()
        row.use_property_split = False
        row.prop(owner, "method", expand=True)
        method = owner.method
        ready = _can_apply(context, method)
        operator, icon = METHOD_BUTTONS[method]
        primary_button(layout, operator.bl_idname, icon, ready)
        # 置灰时说明原因, 否则只看到一个灰按钮; 按 UV 岛拆分也接受编辑模式
        if not ready and method == "UV_ISLANDS":
            layout.label(
                text="Select editable mesh objects in Object or Edit Mode",
                text_ctxt=CONTEXT,
                icon="INFO",
            )
        elif not ready:
            layout.label(
                text="Select editable mesh objects in Object Mode", text_ctxt=CONTEXT, icon="INFO"
            )
        _draw_duplicate_warning(layout, context)


# 每种破碎方式的生成按钮与图标
METHOD_BUTTONS = {
    "CUT": (QD_MESH_FRACTURE_OT_generate, "MOD_EXPLODE"),
    "PARTS": (QD_MESH_FRACTURE_OT_parts, "OUTLINER_OB_GROUP_INSTANCE"),
    "UV_ISLANDS": (QD_MESH_FRACTURE_OT_uv_islands, "UV_ISLANDSEL"),
}


# 参数子面板与弹出面板: 子面板挂在主面板下按分组顺序排列, 弹出按钮并排在最底部; 破碎方式由主面板自己画
SUBPANELS = parameter_panels(QD_MESH_FRACTURE_PT_main, SCHEMA, ("method",), GROUPS, _param_active)
