"""界面约定 - 工具面板基类, 标题栏预设入口, 一行一个字段的参数绘制, 以及按分组表生成参数子面板"""

from typing import NamedTuple

from bpy.types import Panel

from . import presets
from .constants import SIDEBAR_CATEGORY
from .contract import scene_settings


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


class ParamGroup(NamedTuple):
    """一个参数子面板的声明"""

    # 子面板标题, 按工具上下文翻译
    title: str
    # 子面板里的字段, 一行一个
    fields: tuple
    # 为真时子面板默认收起, 用于不常调的参数
    closed: bool = False
    # 画在子面板标题上的总开关字段, 关闭时子面板内容置灰
    toggle: str | None = None


def _draw_field(layout, owner, schema, key):
    """绘制一个字段; 多选枚举的标签单独一行, 选项两列排开, 保证名称完整显示"""
    options = schema.specs[key][1]
    if "ENUM_FLAG" not in options.get("options", ()):
        layout.prop(owner, key)
        return
    column = layout.column(align=True)
    column.use_property_split = False
    column.label(text=options["name"], text_ctxt=schema.context)
    grid = column.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
    for identifier, name, _description in options["items"]:
        grid.prop_enum(owner, key, identifier, text=name, text_ctxt=schema.context)


def draw_fields(layout, owner, schema, keys, active):
    """一行一个字段, 标签在左数值在右; active(owner, key) 为假的字段置灰, 但仍可编辑"""
    column = layout.column()
    column.use_property_split = True
    column.use_property_decorate = False
    for key in keys:
        # 每个字段单独一层子布局, 置灰从属字段时不会连带置灰它的控制字段
        item = column.column()
        item.active = active(owner, key)
        _draw_field(item, owner, schema, key)


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


def parameter_panels(parent, schema, core, groups, active):
    """按分组表生成挂在 parent 下的参数子面板类, 并检查每个参数恰好排布一次

    core 是 parent 自己绘制的常用字段; 返回的类按分组顺序注册, 也就是界面上的上下顺序
    """
    # 标题上的总开关也算已排布
    placed = [*core, *(key for g in groups for key in (*g.fields, g.toggle) if key is not None)]
    if sorted(placed) != sorted(schema.specs):
        missing = sorted(set(schema.specs) - set(placed))
        repeated = sorted({key for key in placed if placed.count(key) > 1})
        extra = sorted(set(placed) - set(schema.specs))
        raise ValueError(f"Parameter layout mismatch: {missing=} {repeated=} {extra=}")
    panels = []
    for group in groups:
        namespace = {
            "bl_label": group.title,
            "bl_parent_id": parent.__name__,
            "bl_translation_context": schema.context,
            "bl_options": {"DEFAULT_CLOSED"} if group.closed else set(),
            "draw": _group_draw(parent.tool_id, schema, group, active),
        }
        if group.toggle:
            namespace["draw_header"] = _toggle_draw(parent.tool_id, group.toggle)
        # 类名由父面板与标题派生, 保持稳定, Blender 按它记住折叠状态
        name = f"{parent.__name__}_{group.title.lower().replace(' ', '_')}"
        panels.append(type(name, (ToolPanel, Panel), namespace))
    return tuple(panels)
