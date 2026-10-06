"""交互层 - 破坏驱动的侧栏面板与算子: 在游标处添加驱动, 编辑活动驱动的参数, 列出场景里的驱动"""

from typing import ClassVar

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel

from ...common import asset, drivers
from ...common.blender_context import select_only
from ...common.translate import tr, tr_report
from ...framework.contract import scene_settings
from ...framework.operators import ToolOperator
from ...framework.ui import (
    ToolPanel,
    active_rule,
    draw_fields,
    label_row,
    picked_objects,
)
from .api import add_driver, affected_shards, make_driver, remove_driver
from .constants import CONTEXT, ROLE_ICONS, TOOL_ID
from .settings import SCHEMA

# 界面上提供的驱动角色, 也是按钮的顺序与文字: 只有爆炸与推力
# 旋涡, 区域, 曲线方向与弹道的数据契约与模拟仍然保留: 旧文件里的这些驱动照常参与模拟,
# 阶段 API 也仍能新建它们, 只是界面不再提供新建与转换的入口
ROLE_CHOICES = (
    (drivers.EXPLOSION, "Explosion", "Throw shards outward from the center of the sphere"),
    (drivers.PUSH, "Push", "Push shards along the arrow of the empty"),
)
# 作用范围由物体缩放决定的角色: 界面显示半径并提示缩放
RANGE_ROLES = (drivers.EXPLOSION, drivers.PUSH, drivers.VORTEX, drivers.ZONE)


def _role_property():
    """算子的驱动角色属性; SKIP_SAVE 让每次调用都按按钮给出的角色执行

    返回 每次调用都新建的 EnumProperty 定义
    """
    return EnumProperty(
        name="Driver Type",
        description="Kind of driver to create",
        items=ROLE_CHOICES,
        translation_context=CONTEXT,
        options={"HIDDEN", "SKIP_SAVE"},
    )


class QD_DRIVERS_OT_add(ToolOperator, Operator):
    """在 3D 游标处新建一个驱动空物体并选中它"""

    bl_idname = "qd_drivers.add"
    bl_label = "Add Driver"
    bl_description = (
        "Add a driver empty at the 3D cursor; move and scale it to place its range, the "
        "shown shape is the range"
    )
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}
    # 要新建的驱动角色, 由按钮写入
    __annotations__ = {"role": _role_property()}

    def run(self, context):
        """新建驱动并设为唯一选中的活动物体

        context Blender 上下文
        返回 报告文字
        """
        obj = add_driver(context, self.role)
        select_only(context, [obj], obj)
        return tr_report(CONTEXT, 'Added driver "{name}"', name=obj.name)


class QD_DRIVERS_OT_convert(ToolOperator, Operator):
    """把活动的空物体设为驱动"""

    bl_idname = "qd_drivers.convert"
    bl_label = "Use as Driver"
    bl_description = "Turn the active empty into a driver of this type"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}
    # 要设成的驱动角色, 由按钮写入
    __annotations__ = {"role": _role_property()}

    def run(self, context):
        """设置角色与对应的显示类型

        context Blender 上下文
        返回 报告文字
        """
        obj = make_driver(context.active_object, self.role)
        return tr_report(CONTEXT, '"{name}" is now a driver', name=obj.name)


class QD_DRIVERS_OT_select_affected(ToolOperator, Operator):
    """选中计划里会被驱动起动的碎块"""

    bl_idname = "qd_drivers.select_affected"
    bl_label = "Select Affected Shards"
    bl_description = "Select the shards of the selected assets that the drivers will start moving"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def run(self, context):
        """按计划选中有起动事件的碎块; 没有时不改动选择

        context Blender 上下文
        返回 报告文字
        """
        found = affected_shards(context, asset.resolve(picked_objects(context)))
        if not found:
            return tr_report(CONTEXT, "No shard is affected by the drivers")
        select_only(context, found, found[0])
        return tr_report(CONTEXT, "Selected {count} affected shards", count=len(found))


class QD_DRIVERS_OT_select(ToolOperator, Operator):
    """在驱动列表里点击时选中对应的驱动物体"""

    bl_idname = "qd_drivers.select"
    bl_label = "Select Driver"
    bl_description = "Select this driver and make it active to edit its settings"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}
    # 要选中的驱动物体名, 由列表里的按钮写入
    __annotations__ = {
        "name": StringProperty(
            name="Driver",
            description="Name of the driver object to select",
            translation_context=CONTEXT,
            options={"HIDDEN", "SKIP_SAVE"},
        )
    }

    def run(self, context):
        """选中给定名字的驱动物体

        context Blender 上下文
        返回 报告文字; 选中成功时没有报告
        """
        obj = context.scene.objects.get(self.name)
        # 列表可能比场景旧 (驱动刚被删除或重命名)
        if obj is None:
            return tr_report(CONTEXT, 'Driver "{name}" is not in this scene', name=self.name)
        select_only(context, [obj], obj)
        return None


class QD_DRIVERS_OT_delete(ToolOperator, Operator):
    """在驱动列表里删除一个驱动物体"""

    bl_idname = "qd_drivers.delete"
    bl_label = "Delete Driver"
    bl_description = "Delete this driver from the scene"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}
    # 要删除的驱动物体名, 由列表里的按钮写入
    __annotations__ = {
        "name": StringProperty(
            name="Driver",
            description="Name of the driver object to delete",
            translation_context=CONTEXT,
            options={"HIDDEN", "SKIP_SAVE"},
        )
    }

    def run(self, context):
        """删除给定名字的驱动物体

        context Blender 上下文
        返回 报告文字
        """
        obj = context.scene.objects.get(self.name)
        # 列表可能比场景旧 (驱动刚被删除或重命名)
        if obj is None:
            return tr_report(CONTEXT, 'Driver "{name}" is not in this scene', name=self.name)
        remove_driver(obj)
        return tr_report(CONTEXT, 'Deleted driver "{name}"', name=self.name)


# 驱动工具的字段都总是生效
_always = active_rule({})


def _role_name(role):
    """驱动角色的界面名称, 取自驱动规格的枚举项, 已按驱动上下文翻译

    role 驱动角色
    返回 名称; 未知角色返回标识本身
    """
    items = drivers.DRIVER_SPECS["role"][1]["items"]
    name = next((label for key, label, _description in items if key == role), role)
    return tr(CONTEXT, name)


def _role_buttons(layout, operator, choices=ROLE_CHOICES):
    """每种角色一个按钮, 两列排布

    layout 要绘制到的布局
    operator 按钮调用的算子 ID, 算子要有 role 属性
    choices 要列出的角色选项
    """
    grid = layout.grid_flow(row_major=True, columns=2, even_columns=True, align=True)
    for role, name, _description in choices:
        button = grid.operator(
            operator, text=tr(CONTEXT, name), translate=False, icon=ROLE_ICONS[role]
        )
        button.role = role


class QD_DRIVERS_PT_main(ToolPanel, Panel):
    """破坏驱动的侧栏主面板 - 在游标处添加驱动; 编辑与列表在子面板里"""

    bl_label = "Drivers"
    bl_description = "Explosions, pushes, vortices, curves and bullets that start the shards moving"
    bl_translation_context = CONTEXT
    tool_id = TOOL_ID

    def draw(self, context):
        """添加按钮, 选中受影响的碎块, 计划预览的开关与显示设置

        context Blender 上下文
        """
        layout = self.layout
        layout.label(text="Add at the 3D cursor:", text_ctxt=CONTEXT)
        _role_buttons(layout, QD_DRIVERS_OT_add.bl_idname)
        layout.separator()
        layout.operator(QD_DRIVERS_OT_select_affected.bl_idname, icon="RESTRICT_SELECT_OFF")
        settings = scene_settings(context, TOOL_ID)
        draw_fields(layout, settings, SCHEMA, ("show_preview",), _always)
        # 预览没开启时, 它的显示设置置灰但仍可编辑
        column = layout.column()
        column.active = settings.show_preview
        draw_fields(column, settings, SCHEMA, ("preview_scale", "color_by_time"), _always)


def _draw_range(layout, context, obj):
    """作用范围的半径, 按场景单位显示; 提示用缩放调整它

    layout 要绘制到的布局
    context Blender 上下文
    obj 驱动物体
    """
    unit = context.scene.unit_settings
    # 世界单位换成用户设置的长度单位显示
    radius = bpy.utils.units.to_string(
        unit.system, "LENGTH", drivers.range_radius(obj) * unit.scale_length
    )
    row = label_row(layout, tr(CONTEXT, "Range"), translate=False)
    row.label(text=radius, translate=False)
    layout.label(text="Scale the empty to resize", text_ctxt=CONTEXT, icon="INFO")


def _draw_hint(layout, role):
    """路径与区域角色没有半径可显示, 按角色给出调整方式的提示

    layout 要绘制到的布局
    role 驱动角色
    """
    if role == drivers.CURVE_FLOW:
        layout.label(
            text="Edit the points of the curve to change the path", text_ctxt=CONTEXT, icon="INFO"
        )
    elif role == drivers.PROJECTILE:
        layout.label(
            text="The bullet follows the arrow or the curve", text_ctxt=CONTEXT, icon="INFO"
        )


def _draw_driver(layout, context, obj):
    """活动驱动: 类型与启用在上, 只列出该类型用到的参数, 最后是作用范围

    layout 要绘制到的布局
    context Blender 上下文
    obj 活动的驱动物体
    """
    params = getattr(obj, drivers.ATTRIBUTE)
    text = tr(CONTEXT, "{name}: {role}", name=obj.name, role=_role_name(params.role))
    layout.label(text=text, translate=False, icon=ROLE_ICONS[params.role])
    # 类型画成只读文字而不是下拉框: 下拉框会列出界面不再提供的角色
    draw_fields(layout, params, drivers.DRIVER_SCHEMA, ("enabled",), _always)
    # 停用的驱动其余参数置灰
    column = layout.column()
    column.active = params.enabled
    # 时间, 强度与衰减, 方向, 自转与随机各成一组, 组间留空
    for group in drivers.ROLE_FIELDS[params.role]:
        column.separator()
        draw_fields(column, params, drivers.DRIVER_SCHEMA, group, _always)
    column.separator()
    # 有半径的角色显示半径; 路径与区域给出调整方式的提示
    if params.role in RANGE_ROLES:
        _draw_range(column, context, obj)
    else:
        _draw_hint(column, params.role)


class QD_DRIVERS_PT_active(ToolPanel, Panel):
    """活动驱动的参数; 活动物体是普通空物体时提供转换按钮"""

    bl_label = "Active Driver"
    bl_description = "Settings of the active driver"
    bl_translation_context = CONTEXT
    bl_parent_id = "QD_DRIVERS_PT_main"

    def draw(self, context):
        """按活动物体的种类给出参数, 转换按钮或提示

        context Blender 上下文
        """
        layout = self.layout
        obj = context.active_object
        # 三种情形: 已是驱动, 普通空物体 (可以转换), 其余
        if drivers.role_of(obj) != drivers.NONE:
            _draw_driver(layout, context, obj)
        elif obj is not None and obj.type == "EMPTY":
            layout.label(text="Use the active empty as:", text_ctxt=CONTEXT)
            _role_buttons(layout, QD_DRIVERS_OT_convert.bl_idname)
        else:
            layout.label(text="Select a driver, or add one above", text_ctxt=CONTEXT, icon="INFO")


class QD_DRIVERS_PT_list(ToolPanel, Panel):
    """场景里的全部驱动, 点击名字选中, 行尾按钮删除; 停用的驱动置灰"""

    bl_label = "Drivers in Scene"
    bl_description = "All drivers of the scene; click one to select it, or delete it with the button at the end of its row"
    bl_translation_context = CONTEXT
    bl_parent_id = "QD_DRIVERS_PT_main"

    def draw(self, context):
        """每个驱动一行: 名字按钮, 类型与起动帧, 删除按钮

        context Blender 上下文
        """
        layout = self.layout
        found = drivers.all_drivers(context.scene)
        if not found:
            layout.label(text="No drivers in this scene", text_ctxt=CONTEXT)
            return
        column = layout.column(align=True)
        for obj in found:
            params = getattr(obj, drivers.ATTRIBUTE)
            row = column.row(align=True)
            # 停用的驱动整行置灰
            row.active = params.enabled
            # 当前活动的驱动按钮显示为按下状态
            button = row.operator(
                QD_DRIVERS_OT_select.bl_idname,
                text=obj.name,
                translate=False,
                icon=ROLE_ICONS[params.role],
                depress=obj == context.active_object,
            )
            button.name = obj.name
            text = tr(
                CONTEXT, "{role}, frame {frame}", role=_role_name(params.role), frame=params.frame
            )
            row.label(text=text, translate=False)
            # 行尾的删除按钮只画图标, 节省窄侧栏的宽度
            remove = row.operator(QD_DRIVERS_OT_delete.bl_idname, text="", icon="X")
            remove.name = obj.name


# 本工具需要注册的全部类, 顺序即注册顺序, 子面板排在主面板之后
CLASSES = (
    QD_DRIVERS_OT_add,
    QD_DRIVERS_OT_convert,
    QD_DRIVERS_OT_select_affected,
    QD_DRIVERS_OT_select,
    QD_DRIVERS_OT_delete,
    QD_DRIVERS_PT_main,
    QD_DRIVERS_PT_active,
    QD_DRIVERS_PT_list,
)
