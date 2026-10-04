"""工具声明 - 模拟烘焙的 ToolSpec, 参数组与预设界面由注册层按它生成"""

from pathlib import Path

from ...framework.contract import ToolSpec
from .constants import TOOL_ID
from .interaction import (
    SUBPANELS,
    QD_SIMULATION_OT_add_ground,
    QD_SIMULATION_OT_bake,
    QD_SIMULATION_OT_reset,
    QD_SIMULATION_PT_main,
)
from .settings import SCHEMA

TOOL = ToolSpec(
    tool_id=TOOL_ID,
    locales=Path(__file__).with_name("locales"),
    classes=(
        QD_SIMULATION_OT_bake,
        QD_SIMULATION_OT_reset,
        QD_SIMULATION_OT_add_ground,
        QD_SIMULATION_PT_main,
        *SUBPANELS,
    ),
    schema=SCHEMA,
    presets=True,
)
