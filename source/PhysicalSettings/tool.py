"""工具声明 - 占位工具: 只有一块说明面板, 参数与逻辑待实现"""

from ..tool_contract import ToolSpec
from .constants import CONTEXT, TOOL_ID
from .interaction import panel


def classes():
    """返回本工具要注册的算子与面板类, 元组顺序就是注册顺序"""
    # 还没有算子: 只有侧栏面板, 因此面板里也没有算子入口
    return (panel.PHYSICAL_SETTINGS_PT_settings,)


TOOL = ToolSpec(
    tool_id=TOOL_ID,
    translation_module="PhysicalSettings",
    translation_context=CONTEXT,
    # 尚未实现参数, 空规格表示这个工具不参与场景持久化
    property_specs={},
    classes=classes,
    # 次要工具: 面板默认收起, 由注册层把 DEFAULT_CLOSED 落到面板类上
    default_closed=True,
)
