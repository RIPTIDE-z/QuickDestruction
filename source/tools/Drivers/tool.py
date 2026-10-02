"""工具声明 - 破坏驱动的 ToolSpec; 没有场景参数, 驱动参数作为挂载参数组挂到物体上"""

from pathlib import Path

import bpy

from ...common.drivers import ATTRIBUTE, DRIVER_SCHEMA
from ...framework.contract import Attachment, ToolSpec
from .constants import TOOL_ID
from .interaction import CLASSES

TOOL = ToolSpec(
    tool_id=TOOL_ID,
    locales=Path(__file__).with_name("locales"),
    classes=CLASSES,
    attachments=(Attachment(bpy.types.Object, ATTRIBUTE, DRIVER_SCHEMA),),
)
