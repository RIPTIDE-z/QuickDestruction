"""界面约定 - 工具面板基类, 标题栏预设入口, 参数绘制, 阶段状态与资产摘要

还包括按分组表生成参数子面板与弹出面板
"""

import re
from collections.abc import Callable
from typing import NamedTuple

from bpy.types import Panel

from ..common import asset
from ..common.status import CURRENT, OUTDATED
from ..common.translate import tr
from . import presets
from .constants import CONTEXT, SIDEBAR_CATEGORY
from .contract import scene_settings

# 弹出面板的宽度, 单位是界面网格
POPOVER_WIDTH = 12
# 自绘字段的标签列占整行的比例, 与 Blender 分列布局的标签列宽度一致, 自绘字段才能与其他字段对齐
LABEL_FACTOR = 0.4
# 阶段状态的图标: 已过期, 输入未变; 未完成不在表里, 取默认的空心圆
STATUS_ICONS = {OUTDATED: "ERROR", CURRENT: "CHECKMARK"}


class ToolPanel:
    """工具侧栏面板的公共设置 - 固定在 3D 视图侧栏的插件标签页, 带预设的工具在标题栏显示预设入口"""

    # 面板放在 3D 视图的侧栏区域, 同一个标签页里
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY
    # 所属工具的标识: 工具登记了预设时, 标题栏按它显示预设入口; 子面板保持 None
    tool_id = None

    def draw_header_preset(self, context):
        """在面板标题栏右侧绘制预设入口, 不占面板内容的高度

        context Blender 上下文
        """
        # 子面板的 tool_id 为 None, 不在登记表里, 自然不画
        if self.tool_id in presets.SCHEMAS:
            presets.draw_presets(self.layout, self.tool_id, scene_settings(context, self.tool_id))


class CheckGrid(NamedTuple):
    """一组布尔字段, 两列排开占满面板宽度, 省去分列布局左侧的空白"""

    # 布尔字段, 按行优先顺序排列
    fields: tuple


class Expand(NamedTuple):
    """一个枚举字段画成并排的按钮, 选项少又常切换时比下拉框直观, 也能一眼看到全部选项"""

    # 枚举字段名
    field: str
    # 为假时不画标签, 按钮占满整行; 子面板标题已经说明了这个字段时使用, 避免标签与标题重复
    labeled: bool = True


class ParamGroup(NamedTuple):
    """一个参数子面板或弹出面板的声明"""

    # 标题, 按工具上下文翻译
    title: str
    # 字段表: 字段名一行一个, CheckGrid 把几个布尔字段两列排开
    fields: tuple
    # 为真时子面板默认收起, 用于不常调的参数
    closed: bool = False
    # 为真时不占子面板, 在面板底部画成弹出按钮, 用于很少调整的参数
    popover: bool = False
    # 工具专属的绘制函数 draw(layout, owner), 需要特殊排法时使用; fields 仍要列出它画的字段
    draw: Callable | None = None


def field_keys(fields):
    """展开字段表里的全部字段名

    fields 字段表, 元素是字段名, CheckGrid 或 Expand
    返回 字段名列表, 保持表中的顺序
    """
    keys = []
    for item in fields:
        # 三种表项: 布尔字段组, 展开的枚举字段, 普通字段名
        if isinstance(item, CheckGrid):
            keys.extend(item.fields)
        elif isinstance(item, Expand):
            keys.append(item.field)
        else:
            keys.append(item)
    return keys


def _draw_checks(layout, owner, keys, active):
    """两列排开一组布尔字段, 各自按 active 置灰

    layout 要绘制到的布局
    owner 持有字段的参数组
    keys 布尔字段名
    active 判断字段是否生效的函数 active(owner, key)
    """
    grid = layout.grid_flow(row_major=True, columns=2, even_columns=True)
    # 布尔字段不用分列布局: 勾选框与文字紧挨着更清晰
    grid.use_property_split = False
    for key in keys:
        cell = grid.row()
        cell.active = active(owner, key)
        cell.prop(owner, key)


def active_rule(rules):
    """由生效条件表生成判断函数: 表里有条件的字段按条件判断, 其余字段总是生效

    rules {字段名: 条件函数 rule(owner)}; 没有条件的工具传空字典
    返回 判断函数 active(owner, key), 供 draw_fields 与 parameter_panels 使用
    """

    def active(owner, key):
        """字段是否生效; 不生效时置灰但仍可编辑

        owner 参数组
        key 字段名
        返回 生效为真
        """
        rule = rules.get(key)
        return rule is None or rule(owner)

    return active


def label_row(layout, text, *, text_ctxt="", translate=True):
    """自绘字段的一行: 左侧是右对齐的标签列, 右侧放控件

    layout 要绘制到的布局
    text 标签文字
    text_ctxt 标签的翻译上下文
    translate 为假时标签文字原样显示
    返回 右侧放控件的行
    """
    split = layout.split(factor=LABEL_FACTOR, align=True)
    label = split.row()
    label.alignment = "RIGHT"
    label.label(text=text, text_ctxt=text_ctxt, translate=translate)
    return split.row(align=True)


def _draw_vector(layout, owner, schema, key):
    """向量字段: 标签在左, 各分量在右侧并排一行; 分列布局会把分量竖排, 因此手动分列

    layout 要绘制到的布局
    owner 持有字段的参数组
    schema 参数规格, 提供标签与翻译上下文
    key 字段名
    """
    row = label_row(layout, schema.specs[key][1]["name"], text_ctxt=schema.context)
    row.use_property_split = False
    row.prop(owner, key, text="")


def _draw_expanded(layout, owner, schema, key, labeled):
    """枚举字段: 全部选项并排成一组按钮; 带标签时标签在左, 不带时按钮占满整行

    layout 要绘制到的布局
    owner 持有字段的参数组
    schema 参数规格
    key 枚举字段名
    labeled 是否画标签
    """
    # 不带标签: 按钮占满整行
    if not labeled:
        row = layout.row()
        row.use_property_split = False
        row.prop(owner, key, expand=True)
        return
    row = label_row(layout, schema.specs[key][1]["name"], text_ctxt=schema.context)
    row.use_property_split = False
    row.prop(owner, key, expand=True)


def _draw_field(layout, owner, schema, key):
    """绘制一个字段; 向量的分量排在一行, 其余按 Blender 默认的分列布局

    layout 要绘制到的布局
    owner 持有字段的参数组
    schema 参数规格
    key 字段名
    """
    if schema.specs[key][0].endswith("VectorProperty"):
        _draw_vector(layout, owner, schema, key)
    else:
        layout.prop(owner, key)


def draw_fields(layout, owner, schema, fields, active):
    """按字段表绘制: 字段名一行一个, 标签在左数值在右; active(owner, key) 为假的字段置灰, 但仍可编辑

    layout 要绘制到的布局
    owner 持有字段的参数组
    schema 参数规格
    fields 字段表, 元素是字段名, CheckGrid 或 Expand
    active 判断字段是否生效的函数 active(owner, key)
    """
    column = layout.column()
    column.use_property_split = True
    column.use_property_decorate = False
    for item in fields:
        # 布尔字段组: 两列排开
        if isinstance(item, CheckGrid):
            _draw_checks(column, owner, item.fields, active)
            continue
        # 每个字段单独一层子布局, 置灰从属字段时不会连带置灰它的控制字段
        cell = column.column()
        # 展开的枚举字段: 选项并排成按钮
        if isinstance(item, Expand):
            cell.active = active(owner, item.field)
            _draw_expanded(cell, owner, schema, item.field, item.labeled)
            continue
        cell.active = active(owner, item)
        _draw_field(cell, owner, schema, item)


def draw_stage_status(layout, title, status):
    """阶段状态行: 左侧是已翻译的阶段名, 右侧是状态; 已过期时原因另起一行, 窄侧栏里也能读全

    layout 要绘制到的布局
    title 已翻译的阶段名
    status Status, 提供状态与过期原因
    """
    # 状态文字: 输入未变, 已过期, 未完成三选一
    if status.state == CURRENT:
        text = tr(CONTEXT, "Inputs unchanged")
    elif status.state == OUTDATED:
        text = tr(CONTEXT, "Outdated")
    else:
        text = tr(CONTEXT, "Not done")
    column = layout.column(align=True)
    row = label_row(column, title, translate=False)
    row.label(text=text, translate=False, icon=STATUS_ICONS.get(status.state, "RADIOBUT_OFF"))
    # 过期原因另起一行, 避免在窄侧栏里被截断
    if status.state == OUTDATED:
        reason = label_row(column, "", translate=False)
        reason.label(text=tr(CONTEXT, status.reason), translate=False)


def primary_button(layout, operator, icon, enabled=True):
    """工具的主按钮: 加高并画成按下状态, 按下状态取主题的强调色 (默认主题是蓝色)

    Blender 不能给按钮指定任意颜色, 按下状态是突出主按钮最接近的办法, 点击行为不变
    layout 要绘制到的布局
    operator 算子 ID
    icon 图标名
    enabled 为假时整个按钮置灰
    """
    column = layout.column()
    column.enabled = enabled
    column.scale_y = 1.4
    column.operator(operator, icon=icon, depress=True)


def picked_objects(context):
    """面板作用的物体: 选中的物体, 没有选中时退回活动物体

    context Blender 上下文
    返回 物体列表, 可能为空
    """
    if context.selected_objects:
        return list(context.selected_objects)
    return [context.active_object] if context.active_object else []


def selected_assets(context):
    """当前选择对应的资产根集合

    context Blender 上下文
    返回 (根集合列表, 解析失败时已翻译的原因); 失败时列表为空
    """
    try:
        return asset.resolve(picked_objects(context)), ""
    except asset.AssetError as error:
        return [], tr(error.context or CONTEXT, error.message, **error.values)


def draw_assets(layout, context):
    """面板顶部的资产摘要: 一个资产写名字与碎块数, 多个写个数; 解析失败时写原因

    layout 要绘制到的布局
    context Blender 上下文
    返回 根集合列表; 解析失败或没有选中资产时为空
    """
    roots, reason = selected_assets(context)
    # 只有一个资产时要列出碎块, 此时碎块路径重复也算解析失败
    if roots and len(roots) == 1:
        try:
            count = len(asset.shards_of(roots[0]))
        except asset.AssetError as error:
            roots, reason = [], tr(error.context or CONTEXT, error.message, **error.values)
    # 没有可用资产: 把原因显示出来, 面板其余部分据此置灰
    if not roots:
        layout.label(text=reason, translate=False, icon="INFO")
        return roots
    if len(roots) == 1:
        text = tr(CONTEXT, "{name}: {count} shards", name=roots[0].name, count=count)
    else:
        text = tr(CONTEXT, "{count} assets selected", count=len(roots))
    layout.label(text=text, translate=False, icon="OUTLINER_COLLECTION")
    return roots


def _group_draw(tool_id, schema, group, active):
    """生成子面板的 draw: 有自绘函数时交给它, 否则按字段表绘制

    tool_id 工具标识
    schema 参数规格
    group ParamGroup
    active 判断字段是否生效的函数
    返回 面板的 draw 方法
    """

    def draw(self, context):
        """绘制本组字段

        context Blender 上下文
        """
        owner = scene_settings(context, tool_id)
        # 工具专属的绘制函数优先: 需要特殊排法的分组自己画
        if group.draw:
            group.draw(self.layout, owner)
            return
        draw_fields(self.layout, owner, schema, group.fields, active)

    return draw


def _popover_row_draw(context_name, popovers):
    """生成底部弹出按钮面板的 draw: 各弹出面板的按钮并排一行

    context_name 弹出按钮标题的翻译上下文
    popovers 弹出面板类列表
    返回 面板的 draw 方法
    """

    def draw(self, _context):
        """并排绘制弹出按钮

        _context Blender 上下文, 不使用
        """
        row = self.layout.row(align=True)
        for panel in popovers:
            row.popover(panel=panel.__name__, text=panel.bl_label, text_ctxt=context_name)

    return draw


def _panel_names(parent, groups):
    """由父面板与标题派生稳定的类名, Blender 按它记住折叠状态

    只保留小写 ASCII 字母与数字, 其余连续字符折成一个下划线; 标题里没有可用字符时用分组序号
    类名有重复时直接报错, 底部子面板固定叫 popovers, 分组标题不能与它同名
    parent 父面板类
    groups ParamGroup 序列
    返回 (各分组的类名列表, 底部子面板的类名)
    """
    slugs = [
        re.sub(r"[^0-9a-z]+", "_", group.title.lower()).strip("_") or f"group{index}"
        for index, group in enumerate(groups)
    ]
    names = [f"{parent.__name__}_{slug}" for slug in slugs]
    footer = f"{parent.__name__}_popovers"
    # 重名的类会让后注册的覆盖先注册的, 提前报错
    duplicates = sorted({name for name in [*names, footer] if [*names, footer].count(name) > 1})
    if duplicates:
        raise ValueError(f"Group titles produce the same panel class name: {duplicates}")
    return names, footer


def _popover_panel(parent, schema, group, active, name):
    """生成弹出面板类: 不进侧栏, 只由底部按钮弹出

    parent 父面板类, 提供工具标识
    schema 参数规格
    group ParamGroup
    active 判断字段是否生效的函数
    name 面板类名
    返回 Panel 子类
    """
    namespace = {
        "bl_space_type": "VIEW_3D",
        "bl_region_type": "HEADER",
        "bl_label": group.title,
        "bl_translation_context": schema.context,
        "bl_ui_units_x": POPOVER_WIDTH,
        "draw": _group_draw(parent.tool_id, schema, group, active),
    }
    return type(name, (Panel,), namespace)


def parameter_panels(parent, schema, core, groups, active):
    """按分组表生成挂在 parent 下的参数子面板类与弹出面板类, 并检查每个参数恰好排布一次

    返回的类按分组顺序注册, 子面板的注册顺序就是界面上的上下顺序
    弹出面板在全部子面板之后, 由一个不带标题的子面板把它们的按钮并排画在最底部
    parent 父面板类
    schema 参数规格
    core parent 自己绘制的字段名
    groups ParamGroup 序列
    active 判断字段是否生效的函数 active(owner, key)
    返回 要注册的类元组; 字段漏排, 重复或多余时抛 ValueError
    """
    # 每个参数必须恰好排布一次: 漏排的参数用户永远看不到, 重复的会画两遍
    placed = [*core, *(key for group in groups for key in field_keys(group.fields))]
    if sorted(placed) != sorted(schema.specs):
        missing = sorted(set(schema.specs) - set(placed))
        repeated = sorted({key for key in placed if placed.count(key) > 1})
        extra = sorted(set(placed) - set(schema.specs))
        raise ValueError(f"Parameter layout mismatch: {missing=} {repeated=} {extra=}")
    names, footer_name = _panel_names(parent, groups)
    panels = []
    popovers = []
    for group, name in zip(groups, names, strict=True):
        # 弹出分组不占侧栏子面板, 先收集起来
        if group.popover:
            popovers.append(_popover_panel(parent, schema, group, active, name))
            continue
        namespace = {
            "bl_label": group.title,
            "bl_parent_id": parent.__name__,
            "bl_translation_context": schema.context,
            "bl_options": {"DEFAULT_CLOSED"} if group.closed else set(),
            "draw": _group_draw(parent.tool_id, schema, group, active),
        }
        panels.append(type(name, (ToolPanel, Panel), namespace))
    # 全部弹出按钮并排放在一个没有标题的底部子面板里
    if popovers:
        footer = {
            "bl_label": "",
            "bl_parent_id": parent.__name__,
            "bl_options": {"HIDE_HEADER"},
            "draw": _popover_row_draw(schema.context, popovers),
        }
        panels.append(type(footer_name, (ToolPanel, Panel), footer))
    return (*popovers, *panels)
