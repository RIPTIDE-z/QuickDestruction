"""交互层 - 模拟烘焙的侧栏面板与算子; 界面上的烘焙是可取消的模态任务, 推进期间视口实时显示模拟

脚本经 execute 同步烘焙到底; 界面经 invoke 逐帧推进, 按 Esc 取消时场景回到烘焙之前
取消只在帧与帧之间生效: 单帧的推进不可打断
"""

import traceback
from typing import ClassVar

import bpy
from bpy.app.handlers import persistent
from bpy.types import Operator, Panel

from ...common import asset, shards
from ...common.errors import DomainError
from ...common.status import (
    CURRENT,
    drivers_signature,
    scene_signature,
    simulation_status,
)
from ...common.translate import tr, tr_report
from ...framework.contract import scene_settings
from ...framework.operators import ToolOperator, failure_text
from ...framework.ui import (
    ParamGroup,
    ToolPanel,
    draw_assets,
    draw_stage_status,
    parameter_panels,
    picked_objects,
)
from .api import bake, bake_steps, reset
from .constants import CONTEXT, NAVIGATION_EVENTS, SUBSTEP_LIMIT, TOOL_ID
from .core import task
from .core.session import suggestion
from .settings import ACTIVE_WHEN, FINAL, SCHEMA, frame_range

# 定时器间隔, 秒; 每次定时器事件推进一帧, 单帧的耗时远大于它
TIMER_INTERVAL = 0.001
# 多个资产时逐个列出状态的上限
STATUS_ROWS = 6

# 参数分组: 帧范围最常调整; 求解设置很少改动, 默认收起
GROUPS = (
    ParamGroup("Frame Range", ("use_scene_range", "frame_start", "frame_end")),
    ParamGroup("Solver", ("quality", "substeps", "solver_iterations", "time_scale"), closed=True),
)


def _param_active(owner, key):
    """参数的生效条件成立时才可用, 否则置灰但仍可编辑"""
    rule = ACTIVE_WHEN.get(key)
    return rule is None or rule(owner)


def _report_text(result):
    """烘焙完成的报告: 帧数, 碎块数, 耗时与动过的块数; 有被推迟的起动帧时附上说明"""
    text = tr_report(
        CONTEXT,
        "Baked {frames} frames for {count} shards in {seconds:.1f} s; {moved} of them moved",
        frames=result.frames,
        count=result.shards,
        seconds=result.seconds,
        moved=result.moved,
    )
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


@persistent
def _abort_on_load(_scene, _path=None):
    """载入文件前取消正在运行的烘焙, 它持有的数据即将被替换"""
    task.abort_all()


def _remove_load_handler():
    """移除载入回调; 不在列表里时什么也不做"""
    if _abort_on_load in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_abort_on_load)


class QD_SIMULATION_OT_bake(ToolOperator, Operator):
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

    @classmethod
    def unregister(cls):
        """停用插件时取消正在运行的烘焙

        Blender 注销算子类时直接释放模态处理器而不调用 cancel, 所以由运行锁的取消函数释放界面资源,
        载入回调在这里移除
        """
        task.abort_all()
        _remove_load_handler()

    def run(self, context):
        """脚本调用: 同步烘焙到底"""
        roots = asset.resolve(picked_objects(context))
        data = SCHEMA.read(scene_settings(context, TOOL_ID))
        return _report_text(bake(context, roots, data))

    def invoke(self, context, _event):
        """界面调用: 先推进第一帧, 校验错误立即报告; 之后由定时器逐帧推进"""
        self._steps = None
        self._timer = None
        # 取消可能来自载入回调或注销, 那时拿不到算子的上下文, 先记下要释放的界面资源的归属
        self._manager = context.window_manager
        self._workspace = context.workspace
        try:
            # 输入先读完再拿运行锁: 读取失败时没有锁需要释放
            roots = asset.resolve(picked_objects(context))
            data = SCHEMA.read(scene_settings(context, TOOL_ID))
            task.claim(self._abort)
        except Exception as error:
            return self._fail(error)
        self._steps = bake_steps(context, roots, data)
        try:
            progress = next(self._steps)
        except Exception as error:
            self._release()
            return self._fail(error)
        manager = self._manager
        self._timer = manager.event_timer_add(TIMER_INTERVAL, window=context.window)
        manager.modal_handler_add(self)
        manager.progress_begin(0, progress.total)
        bpy.app.handlers.load_pre.append(_abort_on_load)
        self._show(progress)
        return {"RUNNING_MODAL"}

    def modal(self, _context, event):
        """Esc 取消; 定时器推进一帧; 视图导航放行; 其余输入在烘焙结束前拦下"""
        if event.type == "ESC" and event.value == "PRESS":
            return self._cancel_task()
        if event.type == "TIMER":
            return self._advance()
        if event.type in NAVIGATION_EVENTS:
            return {"PASS_THROUGH"}
        return {"RUNNING_MODAL"}

    def cancel(self, _context):
        """Blender 从外部取消模态任务时同样撤回, 例如载入文件时移除旧窗口的处理器"""
        self._cancel_task()

    def _advance(self):
        """推进一帧; 结束时报告结果, 出错时报告原因, 场景已由生成器恢复"""
        try:
            progress = next(self._steps)
        except StopIteration as stop:
            self._release()
            if stop.value is None:
                # 生成器已被外部关闭, 例如载入文件
                return {"CANCELLED"}
            self.report({"INFO"}, _report_text(stop.value))
            return {"FINISHED"}
        except Exception as error:
            self._release()
            return self._fail(error)
        self._show(progress)
        return {"RUNNING_MODAL"}

    def _cancel_task(self):
        """关闭生成器触发撤回, 释放资源并报告"""
        if self._steps is not None:
            self._steps.close()
        self._release()
        self.report({"WARNING"}, tr_report(CONTEXT, "Bake cancelled; the scene was restored"))
        return {"CANCELLED"}

    def _abort(self):
        """由运行锁调用的取消, 用于载入文件与停用插件: 关闭生成器并释放计时器, 进度条, 状态栏与运行锁

        载入回调不在这里移除: 载入文件时它正在 load_pre 列表的遍历中执行, 删除自己会让下一个回调被跳过
        """
        if self._steps is not None:
            self._steps.close()
        self._release_interface()

    def _show(self, progress):
        """进度条与状态栏文字"""
        self._manager.progress_update(progress.done)
        text = tr(
            CONTEXT,
            "Baking frame {done} of {total}, press Esc to cancel",
            done=progress.done,
            total=progress.total,
        )
        if self._workspace is not None:
            self._workspace.status_text_set(text)

    def _release_interface(self):
        """释放定时器, 进度条, 状态栏与运行锁; 可以重复调用"""
        if self._timer is not None:
            self._manager.event_timer_remove(self._timer)
            self._timer = None
            self._manager.progress_end()
        if self._workspace is not None:
            self._workspace.status_text_set(None)
        task.release(self._abort)

    def _release(self):
        """释放全部资源, 包括载入回调; 可以重复调用"""
        self._release_interface()
        _remove_load_handler()

    def _fail(self, error):
        """报告失败, 与 ToolOperator.execute 的报告格式一致"""
        if not isinstance(error, DomainError):
            traceback.print_exc()
        self.report({"ERROR"}, failure_text(self, error))
        return {"CANCELLED"}


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
        """复位并报告碎块数"""
        count = reset(context, asset.resolve(picked_objects(context)))
        return tr_report(CONTEXT, "Reset {count} shards to their rest pose", count=count)


def _settings_changed(root, context):
    """面板上的设置与这个资产上次烘焙时实际使用的设置是否不同"""
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
    if owner.quality != used.get("quality", owner.quality):
        return True
    return any(abs(current[key] - used.get(key, current[key])) > 1e-6 for key in current)


# 子步建议的缓存: 键是 (各资产的物理修订号, 驱动摘要, 场景签名, 帧范围, 时间缩放), 值是 (建议值, 是否截断)
# 输入没变时绘制不重复编译
_SUGGESTION = {"key": None, "value": (0, False)}


def _suggested(context, roots, owner):
    """选中资产的子步建议值; 只在输入变化后重新计算, 因为要读全部碎块的网格"""
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
    if _SUGGESTION["key"] != key:
        _SUGGESTION["key"] = key
        _SUGGESTION["value"] = suggestion(context, roots, owner)
    return _SUGGESTION["value"]


def _draw_participants(layout, context, roots):
    """这次烘焙会碰到什么: 选中的资产数, 一起参与的环境刚体数, 以及要临时移出的其他资产数"""
    world = context.scene.rigidbody_world
    if world is None or world.collection is None:
        return
    taking = {shards.asset_of(root) for root in roots}
    environment, others = 0, set()
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
    """子步建议: 比设定值高时提示, 说明终稿质量会自动采用它"""
    owner = scene_settings(context, TOOL_ID)
    wanted, capped = _suggested(context, roots, owner)
    if wanted <= owner.substeps:
        return
    text = tr(CONTEXT, "Suggested substeps: {count}", count=wanted)
    layout.label(text=text, translate=False, icon="ERROR" if capped else "INFO")
    if owner.quality != FINAL:
        layout.label(text="Set Quality to Final to use it", text_ctxt=CONTEXT)


def _draw_statuses(layout, context, roots):
    """逐个资产画出模拟状态; 资产很多时只写处于输入未变状态的个数"""
    statuses = [simulation_status(root, context.scene) for root in roots]
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
        """资产与状态在上, 烘焙按钮加高, 复位在下; 烘焙进行中时按钮置灰并提示取消方式"""
        layout = self.layout
        roots = draw_assets(layout, context)
        if roots:
            _draw_statuses(layout, context, roots)
            _draw_participants(layout, context, roots)
            _draw_suggestion(layout, context, roots)
        running = task.busy()
        if running:
            layout.label(text="Baking, press Esc to cancel", text_ctxt=CONTEXT, icon="TIME")
        button = layout.column()
        button.enabled = bool(roots) and not running
        button.scale_y = 1.4
        button.operator(QD_SIMULATION_OT_bake.bl_idname, icon="RENDER_ANIMATION")
        row = layout.row()
        row.enabled = bool(roots) and not running
        row.operator(QD_SIMULATION_OT_reset.bl_idname, icon="LOOP_BACK")


# 参数子面板: 挂在主面板下, 按分组顺序排列
SUBPANELS = parameter_panels(QD_SIMULATION_PT_main, SCHEMA, (), GROUPS, _param_active)
