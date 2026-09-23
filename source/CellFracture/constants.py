"""预切割领域标识和数值常量。"""

from ..constants import UI_CONTEXT

TOOL_ID = "cell_fracture"
# 保留现有翻译上下文和预设的英文消息键。
CONTEXT = UI_CONTEXT

CELL_SCALE_MIN = 0.001

RANDOM_SEED_DEFAULT = 0

# 尺度相关容差与下游可读取的内部顶点组标识
WELD_SCALE_RATIO = 1.0e-4

WELD_TO_JITTER_RATIO = 5.0

INTERIOR_VGROUP_NAME = "内部"
