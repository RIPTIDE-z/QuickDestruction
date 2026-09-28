"""工具契约 - ToolSpec 描述一个子工具, 只有 tool_id 是身份, 场景字段与翻译上下文都由它派生"""

from dataclasses import dataclass
from pathlib import Path

from ..common.schema import Schema
from .constants import CONTEXT


def tool_context(tool_id):
    """工具的翻译上下文: 各工具互不干扰, 同一英文可以有不同译法"""
    return f"{CONTEXT}.{tool_id}"


def scene_settings(context, tool_id):
    """取工具在当前场景的参数组"""
    return getattr(context.scene.qd_settings, tool_id)


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
    # Blender 应用挂钩: (bpy.app.handlers 中的列表名, 回调) 序列
    handlers: tuple = ()

    @property
    def context(self):
        """本工具的翻译上下文"""
        return tool_context(self.tool_id)
