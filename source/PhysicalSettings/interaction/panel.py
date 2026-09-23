"""侧栏面板 - 占位工具只显示一行用途说明, 参数与操作待实现"""

from bpy.types import Panel

from ...constants import SIDEBAR_CATEGORY
from ..constants import CONTEXT


class PHYSICAL_SETTINGS_PT_settings(Panel):
    """物理设置的侧栏面板 - 与其它工具并列的顶层面板"""

    bl_idname = "PHYSICAL_SETTINGS_PT_settings"
    bl_label = "Physical Settings"
    bl_translation_context = CONTEXT
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    # 分类名决定 3D 视图侧栏的标签页; 同分类的顶层面板按注册顺序排列
    bl_category = SIDEBAR_CATEGORY

    def draw(self, _context):
        """绘制占位说明"""
        # 参数与算子还没实现, 这里只给出这块面板的用途
        self.layout.label(
            text="Physics settings for destruction animation",
            text_ctxt=CONTEXT,
            icon="INFO",
        )
