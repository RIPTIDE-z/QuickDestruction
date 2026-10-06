"""领域常量 - 工具身份与翻译上下文, 模拟成果的阶段标记与动作名前缀, 静止判定阈值, 每帧子步上限"""

from ...framework.contract import tool_context

# 工具身份: 场景字段名, 预设目录与翻译上下文都由它派生
TOOL_ID = "simulation"
# 本工具的翻译上下文
CONTEXT = tool_context(TOOL_ID)

# 模拟成果动作的阶段标记, 删除旧成果前按资产标识与它一起核对归属
STAGE = "simulate"
# 推进期间使用的临时动作的阶段标记
PENDING_STAGE = "simulate_pending"
# 成果动作的名字前缀, 后接资产名; 名字只用于显示
RESULT_PREFIX = "QD_Sim_"
# 临时动作的名字前缀, 后接资产名
PENDING_PREFIX = "QD_Pending_"

# 每帧子步的上限, 与参数规格里的最大值一致
SUBSTEP_LIMIT = 200

# 整段位移与转角都不超过它的碎块视为没有动, 不写关键帧, 停在静止姿态
STATIC_EPSILON = 1e-6

# 预设地面的物体名
GROUND_NAME = "QD_Ground"
# 预设地面的厚度, 米, 按场景单位换算
GROUND_THICKNESS = 1.0
# 预设地面水平半边长的下限, 米, 按场景单位换算
GROUND_MIN_HALF = 100.0
# 预设地面水平半边长相对资产最大水平尺寸的倍数
GROUND_EXTENT_RATIO = 30.0
