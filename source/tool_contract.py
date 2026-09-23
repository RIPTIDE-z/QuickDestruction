"""根装配读取的工具声明, 不包含具体工具的导入。"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    """描述工具的持久化、翻译、交互类和 Blender 挂钩。"""

    tool_id: str
    translation_module: str
    translation_context: str
    property_specs: dict
    classes: Callable[[], tuple[type, ...]]
    settings_attr: str | None = None
    settings_class: type | None = None
    menu_hooks: tuple[tuple[type, Callable], ...] = ()
    load_pre: Callable | None = None
    clear_sessions: Callable[[], None] | None = None
