"""工具契约 - ToolSpec 描述一个子工具, 只有 tool_id 是身份, 场景字段与翻译上下文都由它派生"""

from dataclasses import dataclass
from pathlib import Path

from ..common.schema import Schema
from .constants import CONTEXT


def tool_context(tool_id):
    """工具的翻译上下文: 各工具互不干扰, 同一英文可以有不同译法

    tool_id 工具标识
    返回 形如 QuickDestruction.<tool_id> 的上下文字符串
    """
    return f"{CONTEXT}.{tool_id}"


def scene_settings(context, tool_id):
    """取工具在当前场景的参数组

    context Blender 上下文
    tool_id 工具标识, 同时是场景参数根上的字段名
    返回 该工具的参数组
    """
    return getattr(context.scene.qd_settings, tool_id)


@dataclass(frozen=True)
class Attachment:
    """挂在某类数据块上的参数组, 例如每个驱动物体自己的参数; 注册层按规格生成属性组并挂上指针"""

    # 挂载目标, 是 Blender 的数据块类型, 例如 bpy.types.Object
    owner: type
    # 挂在目标上的属性名, 必须以 qd_ 开头
    attribute: str
    # 参数规格; 规格的翻译上下文决定界面文字的译文来源
    schema: Schema


@dataclass(frozen=True)
class ToolSpec:
    """一个子工具的声明 - 注册层据此生成参数组, 登记预设与翻译, 并注册交互类"""

    # 唯一身份: 小写下划线命名, 同时是场景参数组的字段名与预设子目录名
    tool_id: str
    # 工具自带的语言文件目录
    locales: Path
    # 要注册的算子, 面板与菜单类; 元组顺序即注册顺序, 也是侧栏里面板的上下顺序
    classes: tuple = ()
    # 参数规格; 为 None 表示没有场景参数组, 也没有预设
    schema: Schema | None = None
    # 为真时参数组附带预设名字段, 并获得通用预设界面
    presets: bool = False
    # 挂在其他数据块类型上的参数组, 例如驱动物体的参数
    attachments: tuple = ()
    # 注册时执行, 注销时撤销的钩子: (启动函数, 停止函数) 的元组, 例如视口绘制回调; 两者都不带参数
    hooks: tuple = ()

    @property
    def context(self):
        """本工具的翻译上下文

        返回 由 tool_id 派生的上下文字符串
        """
        return tool_context(self.tool_id)
