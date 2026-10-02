"""工具声明 - 破坏驱动的 ToolSpec; 驱动参数作为挂载参数组挂到物体上, 场景参数只有预览设置"""

from pathlib import Path

import bpy

from ...common.drivers import ATTRIBUTE, DRIVER_SCHEMA
from ...framework.contract import Attachment, ToolSpec
from .constants import TOOL_ID
from .interaction import CLASSES
from .preview import start, stop
from .settings import SCHEMA

TOOL = ToolSpec(
    tool_id=TOOL_ID,
    locales=Path(__file__).with_name("locales"),
    classes=CLASSES,
    schema=SCHEMA,
    attachments=(Attachment(bpy.types.Object, ATTRIBUTE, DRIVER_SCHEMA),),
    hooks=((start, stop),),
)
