"""工具声明 - 网格破碎的 ToolSpec, 参数组与预设界面由注册层按它生成"""

from pathlib import Path

from ...framework.contract import ToolSpec
from .constants import TOOL_ID
from .interaction import QD_MESH_FRACTURE_OT_generate, QD_MESH_FRACTURE_PT_main
from .settings import SCHEMA

TOOL = ToolSpec(
    tool_id=TOOL_ID,
    locales=Path(__file__).with_name("locales"),
    classes=(QD_MESH_FRACTURE_OT_generate, QD_MESH_FRACTURE_PT_main),
    schema=SCHEMA,
    presets=True,
)
