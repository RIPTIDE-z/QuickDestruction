"""领域常量 - 工具身份与翻译上下文, 胞体权重下限, 碎块硬上限, 焊接与抖动比例, 内部顶点组名"""

from ...framework.contract import tool_context

# 工具身份: 场景字段名, 预设目录与翻译上下文都由它派生
TOOL_ID = "mesh_fracture"
# 本工具的翻译上下文
CONTEXT = tool_context(TOOL_ID)

# cell_scale 每个分量的下限, 避免退化成零权重的切割平面
CELL_SCALE_MIN = 0.001

# 一次生成的碎块总数硬上限: 递归与种子数都可以设为不限, 而执行是阻塞的, 超出时报错并回滚
SHARD_LIMIT = 10000

# 焊接距离 = 源物体世界包围盒对角线乘以该比例
WELD_SCALE_RATIO = 1.0e-4

# 胞体顶点抖动半幅 = 焊接距离除以该比例
WELD_TO_JITTER_RATIO = 5.0

# 内部断面顶点组名: 属于数据标识, 下游脚本与导出器按它读取, 因此用 ASCII 且不随界面语言变化
INTERIOR_VGROUP_NAME = "Interior"
