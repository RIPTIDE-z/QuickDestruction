"""交互层 - 游戏导出的侧栏面板与导出算子

绑定由绑定构建面板建立; 这里显示绑定与导出状态, 把绑定写成 FBX
"""

from typing import ClassVar

from bpy.props import BoolProperty, StringProperty
from bpy.types import Operator, Panel

from ...common import asset
from ...common.status import export_status, rig_status
from ...common.translate import tr, tr_report
from ...framework.contract import scene_settings
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
from .api import export_fbx, export_paths, needs_directory
from .constants import CONTEXT, TOOL_ID
from .settings import ACTIVE_WHEN, SCHEMA

# 导出设置第一层: 写到哪里, 给哪个引擎; 最常调整
MAIN_FIELDS = (
    "directory",
    "file_name",
    "engine",
    "anim_range",
    "break_lead",
    "range_start",
    "range_end",
    "export_final_pose",
)
# 轴向与缩放: 选择引擎时自动填好, 很少手动改, 收进默认收起的折叠区
AXIS_FIELDS = ("axis_forward", "axis_up", "apply_scale", "global_scale")
# 轴向与缩放折叠区的标识: Blender 按它记住展开状态
AXES_ID = "qd_game_export_axes"

# 参数的生效条件: 条件不成立时置灰但仍可编辑; 规则见 settings.ACTIVE_WHEN
_param_active = active_rule(ACTIVE_WHEN)


def _draw_settings(layout, owner):
    """导出设置: 目录, 文件名与引擎在上, 轴向与缩放收进默认收起的折叠区

    layout 子面板的布局
    owner 参数组
    """
    draw_fields(layout, owner, SCHEMA, MAIN_FIELDS, _param_active)
    header, body = layout.panel(AXES_ID, default_closed=True)
    header.label(text="Axes and Scale", text_ctxt=CONTEXT)
    # 折叠时 body 为 None, 不画内容
    if body is not None:
        draw_fields(body, owner, SCHEMA, AXIS_FIELDS, _param_active)


# 只有一个子面板: 它自己画出第一层字段与轴向缩放的折叠区
GROUPS = (ParamGroup("Export Settings", (*MAIN_FIELDS, *AXIS_FIELDS), draw=_draw_settings),)


class QD_GAME_EXPORT_OT_export(ToolOperator, Operator):
    """把选中资产的绑定导出成 FBX"""

    bl_idname = "qd_game_export.export"
    bl_label = "Export FBX"
    bl_description = "Write the rig of the selected assets to FBX files in the export directory"
    bl_translation_context = CONTEXT
    # UNDO 让导出记录的写入可以撤销; 磁盘上的文件不会随撤销删除
    bl_options: ClassVar[set[str]] = {"UNDO", "INTERNAL"}

    __annotations__ = {
        # 是否覆盖已存在的文件: 确认对话框通过后由 invoke 置为真, 不保存在 .blend 里
        "overwrite": BoolProperty(
            name="Overwrite",
            description="Replace files that already exist",
            default=False,
            options={"HIDDEN", "SKIP_SAVE"},
        ),
        # 文件浏览器选出的目录: 面板上的目录定不下来时由弹窗写入, 执行时存回面板参数
        "directory": StringProperty(
            name="Directory",
            description="Folder to write the FBX files to",
            subtype="DIR_PATH",
            options={"HIDDEN", "SKIP_SAVE"},
        ),
        # 让文件浏览器只显示文件夹
        "filter_folder": BoolProperty(default=True, options={"HIDDEN", "SKIP_SAVE"}),
    }

    def invoke(self, context, event):
        """目录定不下来时先选目录; 目标文件已存在时先弹确认对话框, 确认后覆盖

        context Blender 上下文
        event 触发事件
        返回 算子返回集合
        """
        # 准备输入时的错误也走失败报告
        try:
            roots = asset.resolve(picked_objects(context))
            config = SCHEMA.read(scene_settings(context, TOOL_ID))
            # 目录为空, 或 blend 文件未保存而目录是相对路径: 弹出文件浏览器让用户选一个绝对目录
            if needs_directory(config):
                context.window_manager.fileselect_add(self)
                return {"RUNNING_MODAL"}
            found = [path for path in export_paths(roots, config) if path.exists()]
        except Exception as error:
            return self.fail(error)
        # 没有会被替换的文件, 或已经确认过: 直接导出
        if not found or self.overwrite:
            return self.execute(context)
        # 确认对话框通过后会再次执行本算子, 此时需要允许覆盖
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
        """逐个导出并报告文件数

        context Blender 上下文
        返回 报告文字
        """
        roots = asset.resolve(picked_objects(context))
        owner = scene_settings(context, TOOL_ID)
        # 从文件浏览器选了目录: 存回面板参数, 之后的导出直接用它
        if self.directory:
            owner.directory = self.directory
        config = SCHEMA.read(owner)
        # 先核对文件名, 不同资产写到同一个文件时一个都不写
        export_paths(roots, config)
        files = []
        for root in roots:
            files.extend(export_fbx(context, root, config, self.overwrite))
        directory = files[0].parent if files else ""
        return tr_report(
            CONTEXT,
            "Exported {count} files to {directory}",
            count=len(files),
            directory=directory,
        )


def _draw_statuses(layout, context, roots):
    """绑定与导出状态; 多个资产时每个资产一行导出状态

    layout 要绘制到的布局
    context Blender 上下文
    roots 选中的资产根集合
    """
    # 一个资产: 绑定与导出状态都列出, 绑定过期时一眼能看到原因
    if len(roots) == 1:
        root = roots[0]
        draw_stage_status(layout, tr(CONTEXT, "Rig"), rig_status(root, context.scene))
        draw_stage_status(layout, tr(CONTEXT, "Export"), export_status(root))
        return
    for root in roots:
        draw_stage_status(layout, root.name, export_status(root))


class QD_GAME_EXPORT_PT_main(ToolPanel, Panel):
    """游戏导出的侧栏主面板 - 资产摘要, 绑定与导出状态, 导出按钮; 参数在子面板里"""

    bl_label = "Game Export"
    bl_description = "Export the rigs built in the Rig panel as FBX files for game engines"
    bl_translation_context = CONTEXT
    tool_id = TOOL_ID

    def draw(self, context):
        """资产与状态在上, 导出按钮突出

        context Blender 上下文
        """
        layout = self.layout
        roots = draw_assets(layout, context)
        if roots:
            _draw_statuses(layout, context, roots)
        primary_button(layout, QD_GAME_EXPORT_OT_export.bl_idname, "EXPORT", bool(roots))


# 参数子面板: 挂在主面板下
SUBPANELS = parameter_panels(QD_GAME_EXPORT_PT_main, SCHEMA, (), GROUPS, _param_active)
