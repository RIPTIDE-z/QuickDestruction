"""侧栏父面板 - 只提供 QuickDestruction 分类和层级, 参数与操作由各工具子面板绘制"""

from bpy.types import Panel

from .constants import SIDEBAR_CATEGORY


class QD_PT_main(Panel):
    """容纳各套工具的侧栏父面板"""

    bl_idname = "QD_PT_main"
    bl_label = "QuickDestruction"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY

    def draw(self, _context):
        """父面板只提供层级, 操作由各子面板绘制"""
