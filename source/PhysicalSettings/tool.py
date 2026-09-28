"""物理设置 - 占位工具: 只有一块默认收起的说明面板, 参数与逻辑待实现"""

from pathlib import Path
from typing import ClassVar

from bpy.types import Panel

from ..framework.contract import ToolSpec, tool_context
from ..framework.ui import ToolPanel

# 工具身份: 场景字段名, 预设目录与翻译上下文都由它派生
TOOL_ID = "physical_settings"
# 本工具的翻译上下文
CONTEXT = tool_context(TOOL_ID)


class QD_PHYSICAL_SETTINGS_PT_main(ToolPanel, Panel):
    """物理设置的侧栏面板, 暂时只显示用途说明"""

    bl_label = "Physical Settings"
    bl_description = "Physics settings for destruction animation (not implemented yet)"
    bl_translation_context = CONTEXT
    # 次要工具默认收起
    bl_options: ClassVar[set[str]] = {"DEFAULT_CLOSED"}

    def draw(self, _context):
        """绘制占位说明"""
        self.layout.label(
            text="Physics settings for destruction animation", text_ctxt=CONTEXT, icon="INFO"
        )


TOOL = ToolSpec(
    tool_id=TOOL_ID,
    locales=Path(__file__).with_name("locales"),
    classes=(QD_PHYSICAL_SETTINGS_PT_main,),
)
