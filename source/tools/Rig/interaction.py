"""交互层 - 绑定构建的侧栏面板与算子; 界面上的构建是可取消的模态任务

构建绑定与检查绑定两个按钮; 脚本经 execute 同步执行
检查结果按绑定标识缓存, 面板只显示当前绑定上一次检查的结果
"""

from typing import ClassVar

from bpy.types import Operator, Panel

from ...common import asset, busy
from ...common.status import CURRENT, rig_status
from ...common.translate import tr, tr_report
from ...framework.contract import scene_settings
from ...framework.longtask import LongTaskOperator
from ...framework.operators import ToolOperator
from ...framework.ui import (
    ParamGroup,
    ToolPanel,
    active_rule,
    draw_assets,
    draw_fields,
    draw_stage_status,
    parameter_panels,
    picked_objects,
    primary_button,
)
from .api import build_rig_steps, check
from .constants import CONTEXT, TOOL_ID
from .core.build import build_digest
from .core.check import ERROR, issue_text
from .settings import ACTIVE_WHEN, SCHEMA

# 多个资产时逐个列出状态的上限
STATUS_ROWS = 6
# 面板里最多列出的问题条数
ISSUE_ROWS = 5
# 绑定设置第一层的字段: 决定导出动画是什么样子, 最常调整
MAIN_FIELDS = ("rig_mode", "hidden_scale", "intact_hide_frame", "shards_show_frame")
# 详细设置里的字段, 按用途分成几组, 组间留空: 不动判定, 骨骼上限, 断面 UV, 名字限制
DETAIL_GROUPS = (
    ("static_distance", "static_angle"),
    ("max_bones_per_mesh", "max_bones_total"),
    ("interior_uv", "interior_uv_scale"),
    ("ascii_names",),
)
# 详细设置的折叠区标识: Blender 按它记住展开状态
DETAILS_ID = "qd_rig_details"

# 检查结果缓存: 绑定标识 -> Issue 元组; 重建绑定后标识变了, 旧结果自然不再显示
_ISSUES = {}


# 参数的生效条件: 条件不成立时置灰但仍可编辑; 规则与计算层共用, 见 settings.ACTIVE_WHEN
_param_active = active_rule(ACTIVE_WHEN)


def _draw_settings(layout, owner):
    """绑定设置: 绑定方式与原物体切换的三项在上, 其余收进默认收起的详细设置

    layout 子面板的布局
    owner 参数组
    """
    draw_fields(layout, owner, SCHEMA, MAIN_FIELDS, _param_active)
    header, body = layout.panel(DETAILS_ID, default_closed=True)
    header.label(text="Details", text_ctxt=CONTEXT)
    # 折叠时 body 为 None, 不画内容
    if body is None:
        return
    for index, group in enumerate(DETAIL_GROUPS):
        if index:
            body.separator()
        draw_fields(body, owner, SCHEMA, group, _param_active)


# 只有一个子面板: 它自己画出第一层字段与详细设置的折叠区
GROUPS = (
    ParamGroup(
        "Rig Settings",
        (*MAIN_FIELDS, *(key for group in DETAIL_GROUPS for key in group)),
        draw=_draw_settings,
    ),
)


def _rig_id(root):
    """资产当前绑定的标识

    root 资产根集合
    返回 标识; 没有构建过时为 None
    """
    record = asset.rig_record(root)
    return None if record is None else record.get("id")


def _remember(root, issues):
    """缓存一个资产当前绑定的检查结果

    root 资产根集合
    issues 检查发现的问题
    """
    rig_id = _rig_id(root)
    # 没有绑定的资产没有可缓存的标识
    if rig_id is not None:
        _ISSUES[rig_id] = issues


def _issue_summary(issues):
    """检查结果的汇总文字: 错误数与警告数

    issues Issue 序列
    返回 已翻译的文字
    """
    errors = sum(issue.level == ERROR for issue in issues)
    return tr_report(
        CONTEXT,
        "{errors} errors, {warnings} warnings",
        errors=errors,
        warnings=len(issues) - errors,
    )


def _build_report(results):
    """构建完成的报告: 资产数, 骨骼总数, 耗时与问题汇总

    results RigResult 列表
    返回 已翻译的文字
    """
    bones = sum(result.bones for result in results)
    seconds = sum(result.seconds for result in results)
    issues = tuple(issue for result in results for issue in result.issues)
    return tr_report(
        CONTEXT,
        "Built {count} rigs with {bones} bones in {seconds:.1f} s; {summary}",
        count=len(results),
        bones=bones,
        seconds=seconds,
        summary=_issue_summary(issues),
    )


class QD_RIG_OT_build(LongTaskOperator, Operator):
    """为选中的资产构建骨架与蒙皮网格"""

    bl_idname = "qd_rig.build"
    bl_label = "Build Rig"
    bl_description = (
        "Build a skeleton and skinned meshes from the baked simulation of the selected "
        "assets; press Esc to cancel and restore the scene"
    )
    bl_translation_context = CONTEXT
    # UNDO 让完成的构建进入撤销栈; 取消时场景已经恢复, 不推送撤销
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def begin(self, context):
        """读完选中的资产与面板参数, 返回构建的生成器

        context Blender 上下文
        返回 构建生成器
        """
        roots = asset.resolve(picked_objects(context))
        data = SCHEMA.read(scene_settings(context, TOOL_ID))
        return build_rig_steps(context, roots, data)

    def report_text(self, result):
        """构建完成的报告; 顺便缓存各资产的检查结果

        result RigResult 列表
        返回 已翻译的报告文字
        """
        for item in result:
            _ISSUES[item.rig_id] = item.issues
        return _build_report(result)

    def progress_text(self, progress):
        """状态栏的进度文字

        progress 当前进度
        返回 已翻译的文字
        """
        return tr(
            CONTEXT,
            "Building rig, stage {done} of {total}, press Esc to cancel",
            done=progress.done,
            total=progress.total,
        )

    def cancelled_text(self):
        """取消后的报告

        返回 已翻译的报告文字
        """
        return tr_report(CONTEXT, "Build cancelled; the scene was restored")


class QD_RIG_OT_check(ToolOperator, Operator):
    """检查选中资产的绑定"""

    bl_idname = "qd_rig.check"
    bl_label = "Check Rig"
    bl_description = "Check that the rig matches the shards frame by frame and fits the bone limits"
    bl_translation_context = CONTEXT
    # 检查只读数据, 不进入撤销栈
    bl_options: ClassVar[set[str]] = {"INTERNAL"}

    def run(self, context):
        """检查每个资产并缓存结果, 报告问题汇总

        context Blender 上下文
        返回 报告文字
        """
        roots = asset.resolve(picked_objects(context))
        config = SCHEMA.read(scene_settings(context, TOOL_ID))
        found = []
        for root in roots:
            issues = check(context, root, config)
            _remember(root, issues)
            found.extend(issues)
        return tr_report(
            CONTEXT,
            "Checked {count} rigs: {summary}",
            count=len(roots),
            summary=_issue_summary(found),
        )


def _settings_changed(root, context):
    """面板上影响绑定的设置与这个资产上次构建时实际使用的设置是否不同

    root 资产根集合
    context Blender 上下文
    返回 不同为真; 没有构建过时为假
    """
    record = asset.rig_record(root)
    if record is None:
        return False
    return record.get("digest") != build_digest(scene_settings(context, TOOL_ID))


def _draw_summary(layout, root):
    """单个资产的上次构建摘要: 骨架, 网格, 骨骼数与不动的碎块数; 原物体切换时另写两个开关帧

    layout 要绘制到的布局
    root 资产根集合
    """
    record = asset.rig_record(root)
    if record is None:
        return
    text = tr(
        CONTEXT,
        "{armatures} skeletons, {meshes} meshes, {bones} bones, {static} still shards",
        armatures=record["armatures"],
        meshes=record["meshes"],
        bones=record["bones"],
        static=record["static"],
    )
    layout.label(text=text, translate=False, icon="ARMATURE_DATA")
    # 原物体切换: 写明原物体哪一帧隐藏, 碎块哪一帧出现
    if record.get("show_frame") is not None:
        text = tr(
            CONTEXT,
            "Intact hides at frame {hide}, shards show at frame {show}",
            hide=record["hide_frame"],
            show=record["show_frame"],
        )
        layout.label(text=text, translate=False, icon="MOD_EXPLODE")


def _draw_issues(layout, root):
    """单个资产当前绑定上一次检查的问题; 没检查过时不画

    layout 要绘制到的布局
    root 资产根集合
    """
    issues = _ISSUES.get(_rig_id(root))
    # 缓存里没有: 这个绑定还没检查过, 不显示"没有问题"以免误导
    if issues is None:
        return
    if not issues:
        layout.label(text="No problems found", text_ctxt=CONTEXT, icon="CHECKMARK")
        return
    # 只列前几条, 其余汇总成个数, 避免窄侧栏被问题占满
    for issue in issues[:ISSUE_ROWS]:
        icon = "ERROR" if issue.level == ERROR else "INFO"
        layout.label(text=issue_text(issue), translate=False, icon=icon)
    if len(issues) > ISSUE_ROWS:
        text = tr(CONTEXT, "{count} more problems", count=len(issues) - ISSUE_ROWS)
        layout.label(text=text, translate=False)


def _draw_statuses(layout, context, roots):
    """绑定状态; 资产很多时只写绑定处于输入未变状态的个数

    layout 要绘制到的布局
    context Blender 上下文
    roots 选中的资产根集合
    """
    scene = context.scene
    # 一个资产: 绑定状态, 摘要与检查结果都列出
    if len(roots) == 1:
        root = roots[0]
        draw_stage_status(layout, tr(CONTEXT, "Rig"), rig_status(root, scene))
        _draw_summary(layout, root)
        _draw_issues(layout, root)
    # 少量几个资产: 每个资产一行绑定状态
    elif len(roots) <= STATUS_ROWS:
        for root in roots:
            draw_stage_status(layout, root.name, rig_status(root, scene))
    # 很多资产: 逐个列出会占满侧栏, 只写个数
    else:
        current = sum(rig_status(root, scene).state == CURRENT for root in roots)
        text = tr(
            CONTEXT,
            "{current} of {count} assets have current rigs",
            current=current,
            count=len(roots),
        )
        layout.label(text=text, translate=False, icon="INFO")
    if any(_settings_changed(root, context) for root in roots):
        layout.label(text="Settings changed since the last build", text_ctxt=CONTEXT, icon="INFO")


class QD_RIG_PT_main(ToolPanel, Panel):
    """绑定构建的侧栏主面板 - 资产摘要, 状态, 构建与检查按钮; 参数在子面板里"""

    bl_label = "Rigging"
    bl_description = "Build a skeleton and skinned meshes from the baked simulation"
    bl_translation_context = CONTEXT
    tool_id = TOOL_ID

    def draw(self, context):
        """资产与状态在上, 构建按钮突出, 检查在下; 构建进行中时按钮置灰并提示取消方式

        context Blender 上下文
        """
        layout = self.layout
        roots = draw_assets(layout, context)
        if roots:
            _draw_statuses(layout, context, roots)
        running = busy.busy()
        # 有长任务在运行时按钮都置灰, 并提示取消方式
        if running:
            layout.label(text="Building, press Esc to cancel", text_ctxt=CONTEXT, icon="TIME")
        ready = bool(roots) and not running
        primary_button(layout, QD_RIG_OT_build.bl_idname, "ARMATURE_DATA", ready)
        row = layout.row()
        row.enabled = ready
        row.operator(QD_RIG_OT_check.bl_idname, icon="CHECKMARK")


# 参数子面板: 挂在主面板下
SUBPANELS = parameter_panels(QD_RIG_PT_main, SCHEMA, (), GROUPS, _param_active)
