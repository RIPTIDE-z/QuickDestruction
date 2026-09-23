"""工具声明结构 - ToolSpec 描述工具身份, 参数规格, 交互类与 Blender 挂钩, 本身不导入任何工具"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PresetSpec:
    """描述工具的预设归属 - 声明它的工具才获得通用的预设界面"""

    # 工具标识: 用于预设归属校验, 同时作为侧栏预设会话的令牌
    key: str
    # 相对用户脚本目录的预设目录, 每个工具独占一层, 互不可见
    subdir: Path
    # 参数规格: 预设字段的范围校验, 写回与翻译字段路径都按它进行
    specs: dict


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
    # 返回本工具要注册的算子与面板类; 元组顺序就是注册顺序, 元素必须是类
    classes: Callable[[], tuple[type, ...]]
    # 场景参数组的字段名与类型: 两者必须同时提供或同时省略
    settings_attr: str | None = None
    settings_class: type | None = None
    # 预设归属; 为 None 时该工具不提供预设界面, 也不会有预设目录
    preset: PresetSpec | None = None
    # 菜单挂钩, 每项为 (菜单类型, 绘制回调)
    menu_hooks: tuple[tuple[type, Callable], ...] = ()
    # 加载新文件前的回调, 用于丢弃与旧文件绑定的状态
    load_pre: Callable | None = None
    # 加载新文件后的回调, 用于按新场景重建状态
    load_post: Callable | None = None
    # 只在卸载时执行的回调, 用于清空对话框会话
    clear_sessions: Callable[[], None] | None = None
    # 工具顺序权重: 数值小的排在前面, 权重相同则按 TOOLS 中的声明顺序
    weight: int = 0
    # 初始折叠: True 表示这是次要工具, 它的侧栏面板默认收起;
    # 注册层会据此设置面板类的 bl_options, 因此以本字段为唯一来源
    default_closed: bool = False
