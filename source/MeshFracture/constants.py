"""领域常量 - 工具 ID 与翻译上下文, 胞体尺度下限, 默认随机种子与焊接和抖动比例"""

from ..constants import UI_CONTEXT

TOOL_ID = "mesh_fracture"
# QDSettings 上的字段名; 由 TOOL_ID 派生, 因此工具改名只需要改上面一行
SETTINGS_ATTR = TOOL_ID
# 翻译上下文沿用扩展域; 工具 ID 独立用于预设归属
CONTEXT = UI_CONTEXT

# cell_scale 每个分量的下限, 避免退化成零权重的切割平面
CELL_SCALE_MIN = 0.001

# 未提供随机流时使用的默认种子; 0 也表示固定种子, 不是"每次随机"
RANDOM_SEED_DEFAULT = 0

# 尺度相关容差与下游可读取的内部顶点组标识
# 焊接距离 = 源物体世界包围盒对角线乘以该比例
WELD_SCALE_RATIO = 1.0e-4

# 胞体顶点抖动半幅 = 焊接距离 / 该比例
WELD_TO_JITTER_RATIO = 5.0

# 内部断面顶点组的名字, 下游脚本按这个名字读取权重
INTERIOR_VGROUP_NAME = "内部"
