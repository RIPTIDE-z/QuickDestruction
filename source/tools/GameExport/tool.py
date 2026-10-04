"""工具声明 - 游戏导出的 ToolSpec, 参数组与预设界面由注册层按它生成"""

from pathlib import Path

from ...framework.contract import ToolSpec
from .constants import TOOL_ID
from .interaction import (
    SUBPANELS,
    QD_GAME_EXPORT_OT_build,
    QD_GAME_EXPORT_OT_check,
    QD_GAME_EXPORT_OT_export,
    QD_GAME_EXPORT_PT_main,
)
from .settings import SCHEMA

TOOL = ToolSpec(
    tool_id=TOOL_ID,
    locales=Path(__file__).with_name("locales"),
    classes=(
        QD_GAME_EXPORT_OT_build,
        QD_GAME_EXPORT_OT_check,
        QD_GAME_EXPORT_OT_export,
        QD_GAME_EXPORT_PT_main,
        *SUBPANELS,
    ),
    schema=SCHEMA,
    presets=True,
)
