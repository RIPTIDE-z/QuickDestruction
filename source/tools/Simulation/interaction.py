"""交互层 - 模拟烘焙的侧栏面板与算子; 界面上的烘焙是可取消的模态任务, 推进期间视口实时显示模拟

脚本经 execute 同步烘焙到底; 界面经 invoke 逐帧推进, 按 Esc 取消时场景回到烘焙之前
取消只在帧与帧之间生效: 单帧的推进不可打断
"""

from typing import ClassVar

from bpy.types import Operator, Panel

from ...common import asset, busy, shards
from ...common.status import (
    CURRENT,
    drivers_signature,
    scene_signature,
    simulation_status,
)
from ...common.translate import tr, tr_report
from ...framework.contract import scene_settings
from ...framework.longtask import LongTaskOperator
from ...framework.operators import ToolOperator
from ...framework.ui import (
    ParamGroup,
    ToolPanel,
    draw_assets,
    draw_stage_status,
    parameter_panels,
    picked_objects,
)
from .api import add_ground, bake_steps, reset
from .constants import CONTEXT, SUBSTEP_LIMIT, TOOL_ID
from .core.session import suggestion
from .settings import ACTIVE_WHEN, FINAL, SCHEMA, frame_range

# 多个资产时逐个列出状态的上限
STATUS_ROWS = 6

# 参数分组: 帧范围最常调整; 求解设置很少改动, 默认收起
GROUPS = (
    ParamGroup("Frame Range", ("use_scene_range", "frame_start", "frame_end")),
    ParamGroup("Solver", ("quality", "substeps", "solver_iterations", "time_scale"), closed=True),
)


def _param_active(owner, key):
    """参数的生效条件成立时才可用, 否则置灰但仍可编辑

    owner 参数组
    key 字段名
    返回 生效为真, 没有条件的字段总是生效
    """
    rule = ACTIVE_WHEN.get(key)
    return rule is None or rule(owner)


def _report_text(result):
    """烘焙完成的报告: 帧数, 碎块数, 耗时与动过的块数; 有被推迟的起动帧等情况时附上说明

    result SimulationResult
    返回 已翻译的报告文字
    """
    text = tr_report(
        CONTEXT,
        "Baked {frames} frames for {count} shards in {seconds:.1f} s; {moved} of them moved",
        frames=result.frames,
        count=result.shards,
        seconds=result.seconds,
        moved=result.moved,
    )
    # 以下附加说明只在对应情况发生时追加, 逐条解释结果里的异常之处
    if result.clamped:
        text += " " + tr_report(
            CONTEXT,
            "({count} start frames were moved to the frame after Start Frame)",
            count=result.clamped,
        )
    if result.raised:
        text += " " + tr_report(
            CONTEXT, "(Substeps raised to {count} for final quality)", count=result.substeps
        )
    if result.capped:
        text += " " + tr_report(
            CONTEXT,
            "(The suggested substeps are above the limit of {count}; fast shards may still "
            "pass through thin ones)",
            count=SUBSTEP_LIMIT,
        )
    if result.blocked:
        text += " " + tr_report(
            CONTEXT,
            "({count} starts were held back by frozen or fixed bodies; Sleeping lets impacts pass on)",
            count=result.blocked,
        )
    return text


class QD_SIMULATION_OT_bake(LongTaskOperator, Operator):
    """把选中的资产放在一起模拟, 逐帧烘焙成关键帧"""

    bl_idname = "qd_simulation.bake"
    bl_label = "Bake Simulation"
    bl_description = (
        "Simulate the selected assets together and bake their motion into keyframes; "
        "press Esc to cancel and restore the scene"
    )
    bl_translation_context = CONTEXT
    # UNDO 让完成的烘焙进入撤销栈; 取消时场景已经恢复, 不推送撤销
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def begin(self, context):
        """读完选中的资产与面板参数, 返回烘焙的生成器

        context Blender 上下文
        返回 烘焙生成器
        """
        roots = asset.resolve(picked_objects(context))
        data = SCHEMA.read(scene_settings(context, TOOL_ID))
        return bake_steps(context, roots, data)

    def report_text(self, result):
        """烘焙完成的报告

        result SimulationResult
        返回 已翻译的报告文字
        """
        return _report_text(result)

    def progress_text(self, progress):
        """状态栏的进度文字

        progress 当前进度
        返回 已翻译的文字
        """
        return tr(
            CONTEXT,
            "Baking frame {done} of {total}, press Esc to cancel",
            done=progress.done,
            total=progress.total,
        )

    def cancelled_text(self):
        """取消后的报告

        返回 已翻译的报告文字
        """
        return tr_report(CONTEXT, "Bake cancelled; the scene was restored")


class QD_SIMULATION_OT_reset(ToolOperator, Operator):
    """删除选中资产的模拟成果, 碎块回到静止姿态"""

    bl_idname = "qd_simulation.reset"
    bl_label = "Reset Simulation"
    bl_description = (
        "Delete the baked motion of the selected assets and put the shards back to their rest pose"
    )
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def run(self, context):
        """复位并报告碎块数

        context Blender 上下文
        返回 报告文字
        """
        count = reset(context, asset.resolve(picked_objects(context)))
        return tr_report(CONTEXT, "Reset {count} shards to their rest pose", count=count)


class QD_SIMULATION_OT_add_ground(ToolOperator, Operator):
    """在选中资产的最低点下方添加有厚度的静态刚体地面"""

    bl_idname = "qd_simulation.add_ground"
    bl_label = "Add Ground"
    bl_description = (
        "Add a thick static rigid body ground just below the selected assets, "
        "or fit the existing one to them"
    )
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def run(self, context):
        """添加或重新对齐地面并报告顶面高度

        context Blender 上下文
        返回 报告文字
        """
        result = add_ground(context, asset.resolve(picked_objects(context)))
        # 新建与重新对齐给出不同的报告
        if result.created:
            return tr_report(
                CONTEXT, "Added a ground with its top at height {top:.2f}", top=result.top
            )
        return tr_report(CONTEXT, "Moved the ground to height {top:.2f}", top=result.top)


def _settings_changed(root, context):
    """面板上的设置与这个资产上次烘焙时实际使用的设置是否不同

    root 资产根集合
    context Blender 上下文
    返回 不同为真; 没有烘焙过或会话记录丢失时为假
    """
    record = asset.sim_record(root)
    run = asset.runs(context.scene).get(record.get("run")) if record else None
    if run is None:
        return False
    owner = scene_settings(context, TOOL_ID)
    start, end = frame_range(owner, context.scene)
    current = {
        "frame_start": start,
        "frame_end": end,
        "requested": owner.substeps,
        "solver_iterations": owner.solver_iterations,
        "time_scale": owner.time_scale,
    }
    used = run["settings"]
    # 质量档不是数值, 单独比较
    if owner.quality != used.get("quality", owner.quality):
        return True
    # 旧记录缺少的字段按"没变"处理; 浮点数留一点容差
    return any(abs(current[key] - used.get(key, current[key])) > 1e-6 for key in current)


# 子步建议的缓存: 键是 (各资产的物理修订号, 驱动摘要, 场景签名, 帧范围, 时间缩放)
# 值是 (建议值, 是否截断)
# 输入没变时绘制不重复编译
_SUGGESTION = {"key": None, "value": (0, False)}


def _suggested(context, roots, owner):
    """选中资产的子步建议值; 只在输入变化后重新计算, 因为要读全部碎块的网格

    context Blender 上下文
    roots 选中的资产根集合
    owner 模拟参数组
    返回 (建议值, 是否被上限截断)
    """
    revisions = tuple(
        (shards.asset_of(root), (asset.physics_record(root) or {}).get("rev")) for root in roots
    )
    start, end = frame_range(owner, context.scene)
    key = (
        revisions,
        drivers_signature(context.scene),
        scene_signature(context.scene),
        start,
        end,
        owner.time_scale,
    )
    # 键变了才重新编译: 面板每次重绘都会进来
    if _SUGGESTION["key"] != key:
        _SUGGESTION["key"] = key
        _SUGGESTION["value"] = suggestion(context, roots, owner)
    return _SUGGESTION["value"]


def _draw_participants(layout, context, roots):
    """这次烘焙会碰到什么: 选中的资产数, 一起参与的环境刚体数, 以及要临时移出的其他资产数

    layout 要绘制到的布局
    context Blender 上下文
    roots 选中的资产根集合
    """
    world = context.scene.rigidbody_world
    # 没有刚体世界时没有环境可言
    if world is None or world.collection is None:
        return
    taking = {shards.asset_of(root) for root in roots}
    environment, others = 0, set()
    # 刚体世界里不带资产标识的是环境刚体; 带标识但不在选中资产里的是其他资产的碎块
    for obj in world.collection.objects:
        asset_id = shards.asset_of(obj)
        if asset_id is None:
            environment += 1
        elif asset_id not in taking:
            others.add(asset_id)
    if environment:
        text = tr(
            CONTEXT,
            "{assets} assets and {bodies} other rigid bodies take part",
            assets=len(roots),
            bodies=environment,
        )
    else:
        text = tr(CONTEXT, "{assets} assets take part", assets=len(roots))
    layout.label(text=text, translate=False, icon="OUTLINER_COLLECTION")
    if others:
        text = tr(
            CONTEXT,
            "{count} other assets are moved out of the rigid body world while baking",
            count=len(others),
        )
        layout.label(text=text, translate=False, icon="INFO")


def _draw_suggestion(layout, context, roots):
    """子步建议: 比设定值高时提示, 说明终稿质量会自动采用它

    layout 要绘制到的布局
    context Blender 上下文
    roots 选中的资产根集合
    """
    owner = scene_settings(context, TOOL_ID)
    wanted, capped = _suggested(context, roots, owner)
    # 建议值不高于设定值时不打扰用户
    if wanted <= owner.substeps:
        return
    text = tr(CONTEXT, "Suggested substeps: {count}", count=wanted)
    layout.label(text=text, translate=False, icon="ERROR" if capped else "INFO")
    # 预览质量不会自动采用建议值, 提示怎样才能用上
    if owner.quality != FINAL:
        layout.label(text="Set Quality to Final to use it", text_ctxt=CONTEXT)


def _draw_statuses(layout, context, roots):
    """逐个资产画出模拟状态; 资产很多时只写处于输入未变状态的个数

    layout 要绘制到的布局
    context Blender 上下文
    roots 选中的资产根集合
    """
    statuses = [simulation_status(root, context.scene) for root in roots]
    # 一个资产: 只写状态; 少量几个: 每个资产一行; 很多: 只写个数
    if len(roots) == 1:
        draw_stage_status(layout, tr(CONTEXT, "Status"), statuses[0])
    elif len(roots) <= STATUS_ROWS:
        for root, status in zip(roots, statuses, strict=True):
            draw_stage_status(layout, root.name, status)
    else:
        current = sum(status.state == CURRENT for status in statuses)
        text = tr(
            CONTEXT,
            "{current} of {count} assets have current simulations",
            current=current,
            count=len(roots),
        )
        layout.label(text=text, translate=False, icon="INFO")
    if any(_settings_changed(root, context) for root in roots):
        layout.label(text="Settings changed since the last bake", text_ctxt=CONTEXT, icon="INFO")


class QD_SIMULATION_PT_main(ToolPanel, Panel):
    """模拟烘焙的侧栏主面板 - 资产摘要, 状态, 烘焙与复位按钮; 参数在子面板里"""

    bl_label = "Simulation"
    bl_description = "Simulate fractured assets with their drivers and bake the motion"
    bl_translation_context = CONTEXT
    tool_id = TOOL_ID

    def draw(self, context):
        """资产与状态在上, 烘焙按钮加高, 复位与添加地面在下; 烘焙进行中时按钮置灰并提示取消方式

        context Blender 上下文
        """
        layout = self.layout
        roots = draw_assets(layout, context)
        if roots:
            _draw_statuses(layout, context, roots)
            _draw_participants(layout, context, roots)
            _draw_suggestion(layout, context, roots)
        running = busy.busy()
        # 烘焙进行中: 提示取消方式, 三个按钮置灰
        if running:
            layout.label(text="Baking, press Esc to cancel", text_ctxt=CONTEXT, icon="TIME")
        button = layout.column()
        button.enabled = bool(roots) and not running
        button.scale_y = 1.4
        button.operator(QD_SIMULATION_OT_bake.bl_idname, icon="RENDER_ANIMATION")
        row = layout.row()
        row.enabled = bool(roots) and not running
        row.operator(QD_SIMULATION_OT_reset.bl_idname, icon="LOOP_BACK")
        row = layout.row()
        row.enabled = bool(roots) and not running
        row.operator(QD_SIMULATION_OT_add_ground.bl_idname, icon="MESH_PLANE")


# 参数子面板: 挂在主面板下, 按分组顺序排列
SUBPANELS = parameter_panels(QD_SIMULATION_PT_main, SCHEMA, (), GROUPS, _param_active)
