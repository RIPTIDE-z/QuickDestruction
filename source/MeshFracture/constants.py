"""领域常量 - 工具 ID 与翻译上下文, 胞体尺度下限, 默认随机种子与焊接和抖动比例"""

from ..constants import UI_CONTEXT

TOOL_ID = "mesh_fracture"
# 翻译上下文沿用扩展域; 工具 ID 独立用于预设归属
CONTEXT = UI_CONTEXT

CELL_SCALE_MIN = 0.001

RANDOM_SEED_DEFAULT = 0

# 尺度相关容差与下游可读取的内部顶点组标识
WELD_SCALE_RATIO = 1.0e-4

WELD_TO_JITTER_RATIO = 5.0

INTERIOR_VGROUP_NAME = "内部"
