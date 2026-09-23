"""工具清单 - 按界面顺序列出已启用的业务工具, 是根装配与翻译目录唯一的顺序来源"""

from .MeshFracture.tool import TOOL as MESH_FRACTURE

# 元组顺序即注册顺序与侧栏显示顺序; 新增工具时在此追加, 并补齐语言 JSON 的模块分组
TOOLS = (MESH_FRACTURE,)
