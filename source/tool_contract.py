"""工具声明结构 - ToolSpec 描述工具身份, 参数规格, 交互类与 Blender 挂钩, 本身不导入任何工具"""

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ToolSpec:
    """描述工具的持久化, 翻译, 交互类和 Blender 挂钩"""

    # 工具身份: 同时用于注册时的唯一性检查与预设文件的归属校验
    tool_id: str
    # 该工具在语言 JSON 的 modules 下使用的分组名
    translation_module: str
    # 该工具的消息进入 Blender 翻译字典时使用的上下文
    translation_context: str
    # 该工具的 27 类参数规格; 供翻译目录与 RNA 生成共用, 无参数时为空字典
    property_specs: dict
    # 返回本工具要注册的算子与面板类; 用函数延迟导入, 避免根翻译目录建立前形成导入环
    classes: Callable[[], tuple[type, ...]]
    # 场景参数组的字段名与类型: 两者必须同时提供或同时省略
    settings_attr: str | None = None
    settings_class: type | None = None
    # 菜单挂钩, 每项为 (菜单类型, 绘制回调)
    menu_hooks: tuple[tuple[type, Callable], ...] = ()
    # 加载新文件前的回调, 用于丢弃与旧文件绑定的状态
    load_pre: Callable | None = None
    # 只在卸载时执行的回调, 用于清空对话框会话
    clear_sessions: Callable[[], None] | None = None
