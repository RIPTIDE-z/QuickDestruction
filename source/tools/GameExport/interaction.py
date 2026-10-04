"""交互层 - 游戏导出的侧栏面板与算子; 界面上的构建是可取消的模态任务

构建绑定, 检查与导出 FBX 三个按钮依次对应流程的三步; 脚本经 execute 同步执行
检查结果按绑定标识缓存, 面板只显示当前绑定上一次检查的结果
"""

from typing import ClassVar

from bpy.props import BoolProperty
from bpy.types import Operator, Panel

from ...common import asset, busy
from ...common.status import CURRENT, export_status, rig_status
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
from .api import build_rig_steps, check, export_fbx, export_paths
from .constants import CONTEXT, TOOL_ID
from .core.build import build_digest
from .core.check import ERROR, issue_text
from .settings import ACTIVE_WHEN, SCHEMA

# 多个资产时逐个列出状态的上限
STATUS_ROWS = 6
# 面板里最多列出的问题条数
ISSUE_ROWS = 5

# 参数分组: 绑定构建的参数很少改动, 默认收起; 导出的目录与引擎常用, 默认展开
GROUPS = (
    ParamGroup(
        "Rig",
        (
            "static_distance",
            "static_angle",
            "max_bones_per_mesh",
            "max_bones_total",
            "interior_uv",
            "interior_uv_scale",
        ),
        closed=True,
    ),
    ParamGroup(
        "Export",
        (
            "engine",
            "axis_forward",
            "axis_up",
            "apply_scale",
            "global_scale",
            "directory",
            "file_name",
        ),
    ),
)

# 检查结果缓存: 绑定标识 -> Issue 元组; 重建绑定后标识变了, 旧结果自然不再显示
_ISSUES = {}


def _param_active(owner, key):
    """参数的生效条件成立时才可用, 否则置灰但仍可编辑"""
    rule = ACTIVE_WHEN.get(key)
    return rule is None or rule(owner)


def _rig_id(root):
    """资产当前绑定的标识, 没有构建过时为 None"""
    record = asset.rig_record(root)
    return None if record is None else record.get("id")


def _remember(root, issues):
    """缓存一个资产当前绑定的检查结果"""
    rig_id = _rig_id(root)
    if rig_id is not None:
        _ISSUES[rig_id] = issues


def _issue_summary(issues):
    """检查结果的汇总文字: 错误数与警告数"""
    errors = sum(issue.level == ERROR for issue in issues)
    return tr_report(
        CONTEXT,
        "{errors} errors, {warnings} warnings",
        errors=errors,
        warnings=len(issues) - errors,
    )


def _build_report(results):
    """构建完成的报告: 资产数, 骨骼与网格数, 耗时与问题汇总"""
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


class QD_GAME_EXPORT_OT_build(LongTaskOperator, Operator):
    """为选中的资产构建骨架与蒙皮网格"""

    bl_idname = "qd_game_export.build"
    bl_label = "Build Rig"
    bl_description = (
        "Build a skeleton and skinned meshes from the baked simulation of the selected "
        "assets; press Esc to cancel and restore the scene"
    )
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    def begin(self, context):
        """读完选中的资产与面板参数, 返回构建的生成器"""
        roots = asset.resolve(picked_objects(context))
        data = SCHEMA.read(scene_settings(context, TOOL_ID))
        return build_rig_steps(context, roots, data)

    def report_text(self, result):
        """构建完成的报告; 顺便缓存各资产的检查结果"""
        for item in result:
            _ISSUES[item.rig_id] = item.issues
        return _build_report(result)

    def progress_text(self, progress):
        """状态栏的进度文字"""
        return tr(
            CONTEXT,
            "Building rig, stage {done} of {total}, press Esc to cancel",
            done=progress.done,
            total=progress.total,
        )

    def cancelled_text(self):
        """取消后的报告"""
        return tr_report(CONTEXT, "Build cancelled; the scene was restored")


class QD_GAME_EXPORT_OT_check(ToolOperator, Operator):
    """检查选中资产的绑定"""

    bl_idname = "qd_game_export.check"
    bl_label = "Check Rig"
    bl_description = "Check that the rig matches the shards frame by frame and fits the bone limits"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"INTERNAL"}

    def run(self, context):
        """检查每个资产并缓存结果, 报告问题汇总"""
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


class QD_GAME_EXPORT_OT_export(ToolOperator, Operator):
    """把选中资产的绑定导出成 FBX"""

    bl_idname = "qd_game_export.export"
    bl_label = "Export FBX"
    bl_description = "Write the rig of the selected assets to FBX files in the export directory"
    bl_translation_context = CONTEXT
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    __annotations__ = {
        "overwrite": BoolProperty(
            name="Overwrite",
            description="Replace files that already exist",
            default=False,
            options={"HIDDEN", "SKIP_SAVE"},
        )
    }

    def invoke(self, context, event):
        """目标文件已存在时先弹确认对话框, 确认后覆盖"""
        roots = asset.resolve(picked_objects(context))
        config = SCHEMA.read(scene_settings(context, TOOL_ID))
        found = [path for path in export_paths(roots, config) if path.exists()]
        if not found or self.overwrite:
            return self.execute(context)
        self.overwrite = True
        message = tr(CONTEXT, "{count} files already exist and will be replaced", count=len(found))
        return context.window_manager.invoke_confirm(
            self,
            event,
            title=tr(CONTEXT, "Replace Files"),
            message=message,
            icon="WARNING",
        )

    def run(self, context):
        """逐个导出并报告文件数与问题汇总"""
        roots = asset.resolve(picked_objects(context))
        config = SCHEMA.read(scene_settings(context, TOOL_ID))
        # 先核对文件名, 不同资产写到同一个文件时一个都不写
        export_paths(roots, config)
        files, found = [], []
        for root in roots:
            paths, issues = export_fbx(context, root, config, self.overwrite)
            _remember(root, issues)
            files.extend(paths)
            found.extend(issues)
        directory = files[0].parent if files else ""
        return tr_report(
            CONTEXT,
            "Exported {count} files to {directory}; {summary}",
            count=len(files),
            directory=directory,
            summary=_issue_summary(found),
        )


def _settings_changed(root, context):
    """面板上影响绑定的设置与这个资产上次构建时实际使用的设置是否不同"""
    record = asset.rig_record(root)
    if record is None:
        return False
    return record.get("digest") != build_digest(scene_settings(context, TOOL_ID))


def _draw_summary(layout, root):
    """单个资产的上次构建摘要: 骨架, 网格, 骨骼数与不动的碎块数"""
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


def _draw_issues(layout, root):
    """单个资产当前绑定上一次检查的问题; 没检查过时不画"""
    issues = _ISSUES.get(_rig_id(root))
    if issues is None:
        return
    if not issues:
        layout.label(text="No problems found", text_ctxt=CONTEXT, icon="CHECKMARK")
        return
    for issue in issues[:ISSUE_ROWS]:
        icon = "ERROR" if issue.level == ERROR else "INFO"
        layout.label(text=issue_text(issue), translate=False, icon=icon)
    if len(issues) > ISSUE_ROWS:
        text = tr(CONTEXT, "{count} more problems", count=len(issues) - ISSUE_ROWS)
        layout.label(text=text, translate=False)


def _draw_statuses(layout, context, roots):
    """绑定与导出状态; 资产很多时只写绑定处于输入未变状态的个数"""
    scene = context.scene
    if len(roots) == 1:
        root = roots[0]
        draw_stage_status(layout, tr(CONTEXT, "Rig"), rig_status(root, scene))
        draw_stage_status(layout, tr(CONTEXT, "Export"), export_status(root))
        _draw_summary(layout, root)
        _draw_issues(layout, root)
    elif len(roots) <= STATUS_ROWS:
        for root in roots:
            draw_stage_status(layout, root.name, rig_status(root, scene))
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


class QD_GAME_EXPORT_PT_main(ToolPanel, Panel):
    """游戏导出的侧栏主面板 - 资产摘要, 状态, 构建, 检查与导出按钮; 参数在子面板里"""

    bl_label = "Game Export"
    bl_description = (
        "Build a skeleton from the baked simulation and export FBX files for game engines"
    )
    bl_translation_context = CONTEXT
    tool_id = TOOL_ID

    def draw(self, context):
        """资产与状态在上, 构建按钮加高, 检查与导出在下; 构建进行中时按钮置灰并提示取消方式"""
        layout = self.layout
        roots = draw_assets(layout, context)
        if roots:
            _draw_statuses(layout, context, roots)
        running = busy.busy()
        if running:
            layout.label(text="Building, press Esc to cancel", text_ctxt=CONTEXT, icon="TIME")
        ready = bool(roots) and not running
        button = layout.column()
        button.enabled = ready
        button.scale_y = 1.4
        button.operator(QD_GAME_EXPORT_OT_build.bl_idname, icon="ARMATURE_DATA")
        row = layout.row(align=True)
        row.enabled = ready
        row.operator(QD_GAME_EXPORT_OT_check.bl_idname, icon="CHECKMARK")
        row.operator(QD_GAME_EXPORT_OT_export.bl_idname, icon="EXPORT")


# 参数子面板: 挂在主面板下, 按分组顺序排列
SUBPANELS = parameter_panels(QD_GAME_EXPORT_PT_main, SCHEMA, (), GROUPS, _param_active)
