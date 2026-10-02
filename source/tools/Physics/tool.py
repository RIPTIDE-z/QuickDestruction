"""工具声明 - 物理设置的 ToolSpec, 参数组与预设界面由注册层按它生成"""

from pathlib import Path

from ...framework.contract import ToolSpec
from .constants import TOOL_ID
from .interaction import (
    SUBPANELS,
    QD_PHYSICS_OT_apply,
    QD_PHYSICS_OT_load_applied,
    QD_PHYSICS_OT_remove,
    QD_PHYSICS_OT_reset_rest,
    QD_PHYSICS_PT_main,
)
from .settings import SCHEMA

TOOL = ToolSpec(
    tool_id=TOOL_ID,
    locales=Path(__file__).with_name("locales"),
    classes=(
        QD_PHYSICS_OT_apply,
        QD_PHYSICS_OT_load_applied,
        QD_PHYSICS_OT_remove,
        QD_PHYSICS_OT_reset_rest,
        QD_PHYSICS_PT_main,
        *SUBPANELS,
    ),
    schema=SCHEMA,
    presets=True,
)
