"""交互层 - 物理设置的侧栏面板, 参数子面板与算子; 算子作用于选中物体所属的资产"""

from typing import ClassVar

from bpy.types import Operator, Panel

from ...common import asset
from ...common.status import CURRENT, physics_status
from ...common.translate import tr, tr_report
from ...framework.contract import scene_settings
from ...framework.operators import ToolOperator
from ...framework.ui import (
    Expand,
    ParamGroup,
    ToolPanel,
    draw_assets,
    draw_fields,
    draw_stage_status,
    parameter_panels,
    picked_objects,
)
from .api import apply_physics, remove_physics, reset_rest
from .constants import CONTEXT, TOOL_ID
from .errors import PhysicsError
from .settings import ACTIVE_WHEN, SCHEMA, material_edited

# 多个资产时逐个列出状态的上限, 更多时只写汇总
STATUS_ROWS = 6


def _param_active(owner, key):
    """参数的生效条件成立时才可用, 否则置灰但仍可编辑"""
    rule = ACTIVE_WHEN.get(key)
    return rule is None or rule(owner)


def _draw_material(layout, owner):
    """材料子面板: 材料下拉与各物理量; 选择材料后又手动改过数值时提示, 以免误以为仍是预设值"""
    draw_fields(
        layout,
        owner,
        SCHEMA,
        ("material", "density", "friction", "restitution", "linear_damping", "angular_damping"),
        _param_active,
    )
    if material_edited(owner):
        layout.label(
            text="Values were edited after choosing the material", text_ctxt=CONTEXT, icon="INFO"
        )


# 参数分组: 材料与受击前状态最常调整, 默认展开; 碰撞边距很少改动, 默认收起
GROUPS = (
    ParamGroup(
        "Material",
        ("material", "density", "friction", "restitution", "linear_damping", "angular_damping"),
        draw=_draw_material,
    ),
    ParamGroup(
        "Before Impact", (Expand("activation", labeled=False), "use_anchor_bottom", "anchor_height")
    ),
    ParamGroup("Collision", (Expand("margin_mode"), "margin"), closed=True),
)


class QD_PHYSICS_OT_apply(ToolOperator, Operator):
    """按侧栏参数为选中的资产应用物理"""

    bl_idname = "qd_physics.apply"
    bl_label = "Apply Physics"
    bl_description = (
        "Give every shard of the selected assets a rigid body with the material below, "
        "move each origin to its center of mass and remember the rest pose"
    )
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def run(self, context):
        """读取场景参数并应用, 报告碎块数与总质量"""
        roots = asset.resolve(picked_objects(context))
        data = SCHEMA.read(scene_settings(context, TOOL_ID))
        result = apply_physics(context, roots, data)
        message = tr_report(
            CONTEXT,
            "Applied physics to {count} shards, total mass {mass:.1f} kg",
            count=result.shards,
            mass=result.mass,
        )
        if result.open_meshes:
            message += " " + tr_report(
                CONTEXT,
                "({count} open shards used their convex hull volume)",
                count=result.open_meshes,
            )
        return message


class QD_PHYSICS_OT_load_applied(ToolOperator, Operator):
    """把选中资产已应用的物理设置读回面板"""

    bl_idname = "qd_physics.load_applied"
    bl_label = "Load Applied Settings"
    bl_description = (
        "Show the settings the selected asset was applied with in this panel, to review or "
        "adjust them"
    )
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def run(self, context):
        """只接受一个已应用物理的资产, 生效记录里的字段整体写回面板"""
        roots = asset.resolve(picked_objects(context))
        record = asset.physics_record(roots[0]) if len(roots) == 1 else None
        if record is None:
            raise PhysicsError("Select one asset with physics applied")
        values = {key: record[key] for key in SCHEMA.specs if key in record}
        SCHEMA.write(scene_settings(context, TOOL_ID), values)
        return tr_report(CONTEXT, 'Loaded the applied settings of "{name}"', name=roots[0].name)


class QD_PHYSICS_OT_remove(ToolOperator, Operator):
    """删除选中资产的刚体数据"""

    bl_idname = "qd_physics.remove"
    bl_label = "Remove Physics"
    bl_description = (
        "Delete the rigid bodies of the selected assets; origins and rest poses are kept"
    )
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def run(self, context):
        """删除刚体并报告碎块数"""
        count = remove_physics(context, asset.resolve(picked_objects(context)))
        return tr_report(CONTEXT, "Removed physics from {count} shards", count=count)


class QD_PHYSICS_OT_reset_rest(ToolOperator, Operator):
    """把碎块当前的位置记为新的静止姿态"""

    bl_idname = "qd_physics.reset_rest"
    bl_label = "Set Rest Pose"
    bl_description = (
        "Remember where the shards are now as their rest pose, for example after moving "
        "some of them by hand; only allowed before simulating"
    )
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def run(self, context):
        """重设静止姿态并报告碎块数"""
        count = reset_rest(context, asset.resolve(picked_objects(context)))
        return tr_report(CONTEXT, "Rest pose updated for {count} shards", count=count)


def _differs(owner, root):
    """面板上的设置与资产已应用的设置是否不同; 没有应用过时不算不同"""
    record = asset.physics_record(root)
    if record is None:
        return False
    current = SCHEMA.read(owner)
    return any(
        abs(value - record[key]) > 1e-6 if isinstance(value, float) else value != record[key]
        for key, value in current.items()
        if key in record
    )


def _draw_statuses(layout, context, roots):
    """逐个资产画出物理状态; 单个资产时面板与已应用的设置不同则提示并给出读取按钮"""
    if len(roots) == 1:
        draw_stage_status(layout, tr(CONTEXT, "Status"), physics_status(roots[0]))
        if _differs(scene_settings(context, TOOL_ID), roots[0]):
            box = layout.box()
            box.label(
                text="The panel differs from the applied settings", text_ctxt=CONTEXT, icon="INFO"
            )
            box.operator(QD_PHYSICS_OT_load_applied.bl_idname, icon="IMPORT")
        return
    if len(roots) <= STATUS_ROWS:
        for root in roots:
            draw_stage_status(layout, root.name, physics_status(root))
        return
    current = sum(physics_status(root).state == CURRENT for root in roots)
    text = tr(
        CONTEXT,
        "{current} of {count} assets have current physics",
        current=current,
        count=len(roots),
    )
    layout.label(text=text, translate=False, icon="INFO")


class QD_PHYSICS_PT_main(ToolPanel, Panel):
    """物理设置的侧栏主面板 - 资产摘要, 状态, 应用按钮; 参数在子面板里"""

    bl_label = "Physics"
    bl_description = "Rigid body material and behavior of fractured assets"
    bl_translation_context = CONTEXT
    tool_id = TOOL_ID

    def draw(self, context):
        """资产与状态在上, 主按钮居中加高, 次要操作并排在下"""
        layout = self.layout
        roots = draw_assets(layout, context)
        if roots:
            _draw_statuses(layout, context, roots)
        button = layout.column()
        button.enabled = bool(roots)
        button.scale_y = 1.4
        button.operator(QD_PHYSICS_OT_apply.bl_idname, icon="PHYSICS")
        row = layout.row(align=True)
        row.enabled = bool(roots)
        row.operator(QD_PHYSICS_OT_remove.bl_idname, icon="X")
        row.operator(QD_PHYSICS_OT_reset_rest.bl_idname, icon="ARMATURE_DATA")


# 参数子面板: 挂在主面板下, 按分组顺序排列
SUBPANELS = parameter_panels(QD_PHYSICS_PT_main, SCHEMA, (), GROUPS, _param_active)
