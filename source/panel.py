"""侧栏父面板 - 只提供 QuickDestruction 分类和层级, 参数与操作由各工具子面板绘制"""

from bpy.types import Panel

from .constants import SIDEBAR_CATEGORY
from .tool_registry import ordered_tools


class QD_PT_main(Panel):
    """容纳各套工具的侧栏父面板"""

    bl_idname = "QD_PT_main"
    bl_label = "QuickDestruction"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    # 分类名决定 3D 视图侧栏的标签页, 与父面板自身的标题无关
    bl_category = SIDEBAR_CATEGORY

    def draw(self, context):
        """父面板只提供层级, 操作由各子面板绘制

        工具可以提供 panel_row 在父面板里加一行自己的入口, 例如图标或开关;
        没有提供的工具不占版面, 控件仍由它自己的子面板承载
        """
        for tool in ordered_tools():
            if tool.panel_row is not None:
                tool.panel_row(self.layout, context)
