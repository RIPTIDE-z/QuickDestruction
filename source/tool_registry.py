"""工具清单 - 按界面顺序列出已启用的业务工具, 是根装配与翻译目录唯一的顺序来源"""

from .MeshFracture.tool import TOOL as MESH_FRACTURE

# 元组顺序即声明顺序; 新增工具时在此追加, 并补齐语言 JSON 的模块分组
TOOLS = (MESH_FRACTURE,)


def ordered_tools():
    """按 weight 升序返回工具, 供注册, 父面板与翻译目录共用

    sorted 是稳定排序, 因此权重相同的工具保持 TOOLS 里的声明顺序
    """
    return tuple(sorted(TOOLS, key=lambda tool: tool.weight))
