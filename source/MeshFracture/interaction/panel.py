"""参数布局 - 分组行表, 启用条件与侧栏子面板, 与操作弹窗共用同一套绘制"""

from bpy.types import Panel

from ...constants import SIDEBAR_CATEGORY
from ..settings import CONTEXT, PROPERTY_SPECS, operator_keywords
from .presets import draw_presets

# 每个字段只在这里排布一次; 一行内的字段独立判断启用状态
ROW_LAYOUT = (
    (
        "Point Source",
        (
            ("source",),
            ("source_limit", "source_noise"),
            ("random_seed",),
            ("cell_scale",),
        ),
    ),
    (
        "Recursive Shatter",
        (
            ("recursion",),
            ("recursion_source_limit", "recursion_clamp"),
            ("recursion_chance", "recursion_chance_select"),
        ),
    ),
    (
        "Mesh Data",
        (
            ("use_smooth_faces", "use_sharp_edges", "use_sharp_edges_apply", "use_data_match"),
            ("material_index", "use_interior_vgroup"),
            ("margin", "use_island_split"),
            ("use_remove_original",),
        ),
    ),
    ("Object", (("use_recenter",),)),
    (
        "Collection",
        (
            ("collection_name",),
            ("shard_prefix", "use_shard_collections"),
        ),
    ),
    (
        "Debug",
        (
            ("use_debug_redraw", "use_debug_points", "use_debug_bool"),
            ("shard_index_padding",),
        ),
    ),
)

# 未应用布尔修改器时, 收尾与递归阶段不执行
DEBUG_INACTIVE = frozenset(
    {"use_sharp_edges", "use_sharp_edges_apply", "use_interior_vgroup", "use_island_split"}
)
RECURSION_DEPENDENT = frozenset(
    {"recursion_source_limit", "recursion_clamp", "recursion_chance", "recursion_chance_select"}
)
GROUP_NOTES = {
    "Point Source": ("Seeds are existing geometry positions, not random points",),
    "Recursive Shatter": (
        "Both seed limits act only below the parent shard vertex count; larger values change nothing",
    ),
}
COMPONENT_AXES = {"cell_scale": ("X", "Y", "Z")}


def _param_enabled(owner, key):
    """仅在参数对应的执行阶段可达时启用控件"""
    # 拆分是锐边标记的后续动作, 且调试布尔会跳过整段收尾, 两个条件要同时成立;
    # 这一条必须排在 DEBUG_INACTIVE 之前, 否则会被下面的分支覆盖掉对锐边的依赖
    if key == "use_sharp_edges_apply":
        return owner.use_sharp_edges and not owner.use_debug_bool
    # 调试布尔会跳过收尾与递归, 这些参数此刻确实不起作用
    if key in DEBUG_INACTIVE or key == "recursion":
        return not owner.use_debug_bool

    # 递归关闭时, 只被递归阶段读取的参数统一置灰
    recursion_enabled = owner.recursion > 0 and not owner.use_debug_bool
    if key == "recursion_chance_select":
        # 选择方式仅在比例严格介于 0 与 1 时才参与
        return recursion_enabled and 0 < owner.recursion_chance < 1
    if key in RECURSION_DEPENDENT or key in {"use_remove_original", "use_shard_collections"}:
        return recursion_enabled
    if key == "material_index":
        # 不匹配源材质槽时, 索引无法可靠对应最终碎块材质
        return owner.use_data_match
    # 其余参数在任何模式下都有效
    return True


def _draw_component_row(row, owner, key):
    """显式标注向量属性名和 X/Y/Z, 避免 Blender 覆盖其中一个标签"""
    row.use_property_split = False
    row.alignment = "EXPAND"
    row.label(text=PROPERTY_SPECS[key][1]["name"], text_ctxt=CONTEXT)
    for index, axis in enumerate(COMPONENT_AXES[key]):
        if index:
            row.separator()
        row.prop(owner, key, index=index, text=axis, text_ctxt=CONTEXT)


def _draw_group(box, owner, rows):
    """按行绘制一组控件, 从属参数各自置灰"""
    column = box.column()
    for row_spec in rows:
        # 单独占一行的项用对齐行, 与多列行保持相同的缩进
        row = column.row(align=True) if len(row_spec) == 1 else column.row()
        for key in row_spec:
            # 每个控件套一层子布局: 禁用从属项时不会连带禁用它的控制开关
            item = row.row(align=True)
            item.enabled = _param_enabled(owner, key)
            if key == "source":
                # 来源是多选枚举, 逐项画成按钮而不是下拉
                item.use_property_split = False
                for identifier, name, _description in PROPERTY_SPECS[key][1]["items"]:
                    item.prop_enum(owner, key, identifier, text=name, text_ctxt=CONTEXT)
            elif key in COMPONENT_AXES:
                _draw_component_row(item, owner, key)
            else:
                # 枚举使用默认下拉样式, 避免横排按钮挤掉同行字段
                item.prop(owner, key)


def draw_parameters(layout, owner):
    """在侧栏和操作弹窗中绘制同一套参数"""
    # 紧凑排布由行表决定, 不按属性分栏, 因此侧栏与弹窗的宽度表现一致
    layout.use_property_split = False
    layout.use_property_decorate = False
    for title, rows in ROW_LAYOUT:
        box = layout.box()
        box.label(text=title, text_ctxt=CONTEXT)
        _draw_group(box, owner, rows)
        # 分组末尾附上 RNA 提示里没有写、但用户一定会踩的前提
        for message in GROUP_NOTES.get(title, ()):
            box.label(text=message, text_ctxt=CONTEXT, icon="INFO")


def configure_operator(operator, owner):
    """把侧栏值写入按钮生成的算子实例"""
    # 逐字段 setattr, 使按钮点击时就地生效, 不依赖算子的默认值
    for key, value in operator_keywords(owner).items():
        setattr(operator, key, value)


def can_apply(context):
    """仅在物体模式且选中可编辑网格时启用侧栏按钮"""
    # 只用于界面置灰; 菜单入口仍随时可点, 由算子自身走报错路径
    return (
        context.mode == "OBJECT"
        and context.scene is not None
        and any(obj.type == "MESH" for obj in context.selected_editable_objects)
    )


class MESH_FRACTURE_PT_settings(Panel):
    """当前场景的 MeshFracture 参数和生成入口"""

    bl_idname = "MESH_FRACTURE_PT_settings"
    bl_parent_id = "QD_PT_main"
    bl_label = "MeshFracture"
    bl_translation_context = CONTEXT
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY

    def draw(self, context):
        """绘制预设, 参数和使用当前侧栏值的生成按钮"""
        layout = self.layout
        owner = context.scene.qd_settings.mesh_fracture
        # 预设入口在参数之上, 与弹窗里的顺序保持一致
        draw_presets(layout, context, owner)
        draw_parameters(layout, owner)

        # 生成按钮固定在全部参数之后, 也就是面板最底部
        ready = can_apply(context)
        actions = layout.column(align=True)
        actions.enabled = ready
        # EXEC_DEFAULT: 侧栏按钮直接执行, 不再弹一次参数对话框
        actions.operator_context = "EXEC_DEFAULT"
        generate = actions.operator(
            "mesh_fracture.generate_fragments",
            text="Generate Fragments",
            text_ctxt=CONTEXT,
            icon="MOD_EXPLODE",
        )
        # 传面板当前值, 不依赖算子默认值
        configure_operator(generate, owner)
        if not ready:
            # 置灰时说明原因, 否则只看到一个灰按钮会不知道差什么
            layout.label(
                text="Select editable mesh objects in Object Mode", text_ctxt=CONTEXT, icon="INFO"
            )
