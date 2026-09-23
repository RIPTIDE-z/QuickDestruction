"""侧栏父面板 - 只提供 QuickDestruction 分类和层级, 参数与操作由各工具子面板绘制"""

from bpy.types import Panel

from .constants import SIDEBAR_CATEGORY


class QD_PT_main(Panel):
    """容纳各套工具的侧栏父面板"""

    bl_idname = "QD_PT_main"
    bl_label = "QuickDestruction"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    # 分类名决定 3D 视图侧栏的标签页, 与父面板自身的标题无关
    bl_category = SIDEBAR_CATEGORY

    def draw(self, _context):
        """父面板只提供层级, 操作由各子面板绘制"""
        # 子面板通过 bl_parent_id 挂到本面板下, 父面板自身不绘制任何控件
