"""工具声明 - 游戏导出的 ToolSpec, 参数组与预设界面由注册层按它生成"""

from pathlib import Path

from ...framework.contract import ToolSpec
from .constants import TOOL_ID
from .interaction import SUBPANELS, QD_GAME_EXPORT_OT_export, QD_GAME_EXPORT_PT_main
from .settings import SCHEMA

# 工具声明: 类的顺序即注册顺序, 也是侧栏里面板的上下顺序; 主面板在前, 子面板按分组顺序跟在后面
TOOL = ToolSpec(
    tool_id=TOOL_ID,
    locales=Path(__file__).with_name("locales"),
    classes=(QD_GAME_EXPORT_OT_export, QD_GAME_EXPORT_PT_main, *SUBPANELS),
    schema=SCHEMA,
    presets=True,
)
