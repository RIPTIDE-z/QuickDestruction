"""界面约定 - 工具面板基类, 标题栏预设入口, 参数绘制, 以及按分组表生成参数子面板与弹出面板"""

from typing import NamedTuple

from bpy.types import Panel

from . import presets
from .constants import SIDEBAR_CATEGORY
from .contract import scene_settings

# 弹出面板的宽度, 单位是界面网格
POPOVER_WIDTH = 12


class ToolPanel:
    """工具侧栏面板的公共设置 - 固定在 3D 视图侧栏的插件标签页, 带预设的工具在标题栏显示预设入口"""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    # 所属工具的标识: 工具登记了预设时, 标题栏按它显示预设入口; 子面板保持 None
    tool_id = None

    def draw_header_preset(self, context):
        """在面板标题栏右侧绘制预设入口, 不占面板内容的高度"""
        if self.tool_id in presets.SCHEMAS:
            presets.draw_presets(self.layout, self.tool_id, scene_settings(context, self.tool_id))


class CheckGrid(NamedTuple):
    """一组布尔字段, 两列排开占满面板宽度, 省去分列布局左侧的空白"""

    # 布尔字段, 按行优先顺序排列
    fields: tuple


class ParamGroup(NamedTuple):
    """一个参数子面板或弹出面板的声明"""

    # 标题, 按工具上下文翻译
    title: str
    # 字段表: 字段名一行一个, CheckGrid 把几个布尔字段两列排开
    fields: tuple
    # 为真时子面板默认收起, 用于不常调的参数
    closed: bool = False
    # 画在子面板标题上的总开关字段, 关闭时子面板内容置灰
    toggle: str | None = None
    # 为真时不占子面板, 在面板底部画成弹出按钮, 用于很少调整的参数
    popover: bool = False


def field_keys(fields):
    """展开字段表里的全部字段名"""
    return [
        key for item in fields for key in (item.fields if isinstance(item, CheckGrid) else (item,))
    ]


def _draw_checks(layout, owner, keys, active):
    """两列排开一组布尔字段, 各自按 active 置灰"""
    grid = layout.grid_flow(row_major=True, columns=2, even_columns=True)
    grid.use_property_split = False
    for key in keys:
        cell = grid.row()
        cell.active = active(owner, key)
        cell.prop(owner, key)


def _draw_vector(layout, owner, schema, key):
    """向量字段: 标签在左, 各分量在右侧并排一行; 分列布局会把分量竖排, 因此手动分列"""
    # 0.4 与 Blender 分列布局的标签宽度一致
    split = layout.split(factor=0.4, align=True)
    label = split.row()
    label.alignment = "RIGHT"
    label.label(text=schema.specs[key][1]["name"], text_ctxt=schema.context)
    row = split.row(align=True)
    row.use_property_split = False
    row.prop(owner, key, text="")


def _draw_field(layout, owner, schema, key):
    """绘制一个字段; 向量的分量排在一行, 多选枚举的标签单独一行, 选项两列排开, 保证名称完整显示"""
    kind, options = schema.specs[key]
    if kind.endswith("VectorProperty"):
        _draw_vector(layout, owner, schema, key)
        return
    if "ENUM_FLAG" not in options.get("options", ()):
        layout.prop(owner, key)
        return
    column = layout.column(align=True)
    column.use_property_split = False
    column.label(text=options["name"], text_ctxt=schema.context)
    grid = column.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
    for identifier, name, _description in options["items"]:
        grid.prop_enum(owner, key, identifier, text=name, text_ctxt=schema.context)


def draw_fields(layout, owner, schema, fields, active):
    """按字段表绘制: 字段名一行一个, 标签在左数值在右; active(owner, key) 为假的字段置灰, 但仍可编辑"""
    column = layout.column()
    column.use_property_split = True
    column.use_property_decorate = False
    for item in fields:
        if isinstance(item, CheckGrid):
            _draw_checks(column, owner, item.fields, active)
            continue
        # 每个字段单独一层子布局, 置灰从属字段时不会连带置灰它的控制字段
        cell = column.column()
        cell.active = active(owner, item)
        _draw_field(cell, owner, schema, item)


def _group_draw(tool_id, schema, group, active):
    """生成子面板的 draw: 绘制本组字段, 标题开关关闭时整组置灰"""

    def draw(self, context):
        """绘制本组字段"""
        owner = scene_settings(context, tool_id)
        if group.toggle:
            self.layout.active = getattr(owner, group.toggle)
        draw_fields(self.layout, owner, schema, group.fields, active)

    return draw


def _toggle_draw(tool_id, toggle):
    """生成子面板的 draw_header: 在标题上绘制总开关"""

    def draw_header(self, context):
        """绘制标题上的总开关, 标签由子面板标题承担"""
        self.layout.prop(scene_settings(context, tool_id), toggle, text="")

    return draw_header


def _popover_row_draw(context_name, popovers):
    """生成底部弹出按钮面板的 draw: 各弹出面板的按钮并排一行"""

    def draw(self, _context):
        """并排绘制弹出按钮"""
        row = self.layout.row(align=True)
        for panel in popovers:
            row.popover(panel=panel.__name__, text=panel.bl_label, text_ctxt=context_name)

    return draw


def _panel_name(parent, title):
    """由父面板与标题派生稳定的类名, Blender 按它记住折叠状态"""
    return f"{parent.__name__}_{title.lower().replace(' ', '_')}"


def _popover_panel(parent, schema, group, active):
    """生成弹出面板类: 不进侧栏, 只由底部按钮弹出"""
    namespace = {
        "bl_space_type": "VIEW_3D",
        "bl_region_type": "HEADER",
        "bl_label": group.title,
        "bl_translation_context": schema.context,
        "bl_ui_units_x": POPOVER_WIDTH,
        "draw": _group_draw(parent.tool_id, schema, group, active),
    }
    return type(_panel_name(parent, group.title), (Panel,), namespace)


def parameter_panels(parent, schema, core, groups, active):
    """按分组表生成挂在 parent 下的参数子面板类与弹出面板类, 并检查每个参数恰好排布一次

    core 是 parent 自己绘制的字段; 返回的类按分组顺序注册, 子面板的注册顺序就是界面上的上下顺序
    弹出面板在全部子面板之后, 由一个不带标题的子面板把它们的按钮并排画在最底部
    """
    # 标题上的总开关也算已排布
    placed = [
        *core,
        *(key for g in groups for key in (*field_keys(g.fields), g.toggle) if key is not None),
    ]
    if sorted(placed) != sorted(schema.specs):
        missing = sorted(set(schema.specs) - set(placed))
        repeated = sorted({key for key in placed if placed.count(key) > 1})
        extra = sorted(set(placed) - set(schema.specs))
        raise ValueError(f"Parameter layout mismatch: {missing=} {repeated=} {extra=}")
    panels = []
    popovers = []
    for group in groups:
        if group.popover:
            popovers.append(_popover_panel(parent, schema, group, active))
            continue
        namespace = {
            "bl_label": group.title,
            "bl_parent_id": parent.__name__,
            "bl_translation_context": schema.context,
            "bl_options": {"DEFAULT_CLOSED"} if group.closed else set(),
            "draw": _group_draw(parent.tool_id, schema, group, active),
        }
        if group.toggle:
            namespace["draw_header"] = _toggle_draw(parent.tool_id, group.toggle)
        panels.append(type(_panel_name(parent, group.title), (ToolPanel, Panel), namespace))
    if popovers:
        footer = {
            "bl_label": "",
            "bl_parent_id": parent.__name__,
            "bl_options": {"HIDE_HEADER"},
            "draw": _popover_row_draw(schema.context, popovers),
        }
        panels.append(type(f"{parent.__name__}_popovers", (ToolPanel, Panel), footer))
    return (*popovers, *panels)
