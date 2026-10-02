"""交互层 - 破坏驱动的侧栏面板与算子: 在游标处添加驱动, 编辑活动驱动的参数, 列出场景里的驱动"""

from typing import ClassVar

import bpy
from bpy.props import EnumProperty, StringProperty
from bpy.types import Operator, Panel

from ...common import drivers
from ...common.blender_context import select_only
from ...common.translate import tr, tr_report
from ...framework.operators import ToolOperator
from ...framework.ui import ToolPanel, draw_fields, label_row
from .api import add_driver, make_driver
from .constants import CONTEXT, ROLE_ICONS, TOOL_ID

# 算子的驱动角色选项; 按钮会写明各自的文字, 这里的名称只用于算子属性本身
ROLE_CHOICES = (
    (drivers.EXPLOSION, "Explosion", "Throw shards outward from the center of the sphere"),
    (drivers.PUSH, "Push", "Push shards along the arrow of the empty"),
)


def _role_property():
    """算子的驱动角色属性; SKIP_SAVE 让每次调用都按按钮给出的角色执行"""
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
    __annotations__ = {"role": _role_property()}

    def run(self, context):
        """新建驱动并设为唯一选中的活动物体"""
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
    __annotations__ = {"role": _role_property()}

    def run(self, context):
        """设置角色与对应的显示类型"""
        obj = make_driver(context.active_object, self.role)
        return tr_report(CONTEXT, '"{name}" is now a driver', name=obj.name)


class QD_DRIVERS_OT_select(ToolOperator, Operator):
    """在驱动列表里点击时选中对应的驱动物体"""

    bl_idname = "qd_drivers.select"
    bl_label = "Select Driver"
    bl_description = "Select this driver and make it active to edit its settings"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}
    __annotations__ = {
        "name": StringProperty(
            name="Driver",
            description="Name of the driver object to select",
            translation_context=CONTEXT,
            options={"HIDDEN", "SKIP_SAVE"},
        )
    }

    def run(self, context):
        """选中给定名字的驱动物体"""
        obj = context.scene.objects.get(self.name)
        if obj is None:
            return tr_report(CONTEXT, 'Driver "{name}" is not in this scene', name=self.name)
        select_only(context, [obj], obj)
        return None


def _role_buttons(layout, operator):
    """每种角色一个按钮, 并排一行"""
    row = layout.row(align=True)
    for role, name, _description in ROLE_CHOICES:
        button = row.operator(
            operator, text=tr(CONTEXT, name), translate=False, icon=ROLE_ICONS[role]
        )
        button.role = role


class QD_DRIVERS_PT_main(ToolPanel, Panel):
    """破坏驱动的侧栏主面板 - 在游标处添加驱动; 编辑与列表在子面板里"""

    bl_label = "Drivers"
    bl_description = "Explosions and pushes that start the shards moving"
    bl_translation_context = CONTEXT
    tool_id = TOOL_ID

    def draw(self, _context):
        """添加按钮一行, 下面说明放置方式"""
        layout = self.layout
        layout.label(text="Add at the 3D cursor:", text_ctxt=CONTEXT)
        _role_buttons(layout, QD_DRIVERS_OT_add.bl_idname)


def _draw_range(layout, context, obj):
    """作用范围的半径, 按场景单位显示; 提示用缩放调整它"""
    unit = context.scene.unit_settings
    radius = bpy.utils.units.to_string(
        unit.system, "LENGTH", drivers.range_radius(obj) * unit.scale_length
    )
    row = label_row(layout, tr(CONTEXT, "Range"), translate=False)
    row.label(text=radius, translate=False)
    layout.label(text="Scale the empty to resize", text_ctxt=CONTEXT, icon="INFO")


def _draw_driver(layout, context, obj):
    """活动驱动: 类型与启用在上, 只列出该类型用到的参数, 最后是作用范围"""
    params = getattr(obj, drivers.ATTRIBUTE)
    layout.label(text=obj.name, translate=False, icon=ROLE_ICONS[params.role])
    draw_fields(layout, params, drivers.DRIVER_SCHEMA, ("role", "enabled"), lambda *_args: True)
    column = layout.column()
    column.active = params.enabled
    # 时间, 强度与衰减, 方向, 自转与随机各成一组, 组间留空
    for group in drivers.ROLE_FIELDS[params.role]:
        column.separator()
        draw_fields(column, params, drivers.DRIVER_SCHEMA, group, lambda *_args: True)
    column.separator()
    _draw_range(column, context, obj)


class QD_DRIVERS_PT_active(ToolPanel, Panel):
    """活动驱动的参数; 活动物体是普通空物体时提供转换按钮"""

    bl_label = "Active Driver"
    bl_description = "Settings of the active driver"
    bl_translation_context = CONTEXT
    bl_parent_id = "QD_DRIVERS_PT_main"

    def draw(self, context):
        """按活动物体的种类给出参数, 转换按钮或提示"""
        layout = self.layout
        obj = context.active_object
        if drivers.role_of(obj) != drivers.NONE:
            _draw_driver(layout, context, obj)
        elif obj is not None and obj.type == "EMPTY":
            layout.label(text="Use the active empty as:", text_ctxt=CONTEXT)
            _role_buttons(layout, QD_DRIVERS_OT_convert.bl_idname)
        else:
            layout.label(text="Select a driver, or add one above", text_ctxt=CONTEXT, icon="INFO")


class QD_DRIVERS_PT_list(ToolPanel, Panel):
    """场景里的全部驱动, 点击名字选中; 停用的驱动置灰"""

    bl_label = "Drivers in Scene"
    bl_description = "All drivers of the scene; click one to select it"
    bl_translation_context = CONTEXT
    bl_parent_id = "QD_DRIVERS_PT_main"

    def draw(self, context):
        """每个驱动一行: 名字按钮, 类型与起动帧"""
        layout = self.layout
        found = drivers.all_drivers(context.scene)
        if not found:
            layout.label(text="No drivers in this scene", text_ctxt=CONTEXT)
            return
        column = layout.column(align=True)
        names = {role: tr(CONTEXT, name) for role, name, _description in ROLE_CHOICES}
        for obj in found:
            params = getattr(obj, drivers.ATTRIBUTE)
            row = column.row(align=True)
            row.active = params.enabled
            button = row.operator(
                QD_DRIVERS_OT_select.bl_idname,
                text=obj.name,
                translate=False,
                icon=ROLE_ICONS[params.role],
                depress=obj == context.active_object,
            )
            button.name = obj.name
            text = tr(CONTEXT, "{role}, frame {frame}", role=names[params.role], frame=params.frame)
            row.label(text=text, translate=False)


CLASSES = (
    QD_DRIVERS_OT_add,
    QD_DRIVERS_OT_convert,
    QD_DRIVERS_OT_select,
    QD_DRIVERS_PT_main,
    QD_DRIVERS_PT_active,
    QD_DRIVERS_PT_list,
)
