"""领域常量 - 工具身份与翻译上下文, 材料预设表, 刚体质量下限"""

from ...framework.contract import tool_context

# 工具身份: 场景字段名, 预设目录与翻译上下文都由它派生
TOOL_ID = "physics"
# 本工具的翻译上下文
CONTEXT = tool_context(TOOL_ID)

# 材料预设写入的字段, 顺序与下表每一行的数值顺序一致
MATERIAL_FIELDS = ("density", "friction", "restitution", "linear_damping", "angular_damping")
# 材料预设: 标识 -> (密度 kg/m3, 摩擦, 弹性, 线阻尼, 角阻尼); 密度取常见工程值, 其余是可再调整的起点
MATERIALS = {
    "CONCRETE": (2400.0, 0.8, 0.05, 0.1, 0.2),
    "BRICK": (1900.0, 0.8, 0.05, 0.1, 0.2),
    "STONE": (2700.0, 0.7, 0.1, 0.1, 0.2),
    "GLASS": (2500.0, 0.4, 0.2, 0.05, 0.1),
    "CERAMIC": (2300.0, 0.5, 0.15, 0.05, 0.1),
    "WOOD": (600.0, 0.6, 0.2, 0.1, 0.2),
    "METAL": (7850.0, 0.5, 0.3, 0.05, 0.1),
}

# Blender 刚体质量的下限, 体积极小的碎块按它取值
MASS_MIN = 0.001
