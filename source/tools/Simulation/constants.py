"""领域常量 - 工具身份与翻译上下文, 模拟成果的阶段标记与动作名前缀, 静止判定阈值, 长任务放行的视图事件"""

from ...framework.contract import tool_context

# 工具身份: 场景字段名, 预设目录与翻译上下文都由它派生
TOOL_ID = "simulation"
# 本工具的翻译上下文
CONTEXT = tool_context(TOOL_ID)

# 模拟成果动作的阶段标记, 删除旧成果前按资产标识与它一起核对归属
STAGE = "simulate"
# 推进期间使用的临时动作的阶段标记
PENDING_STAGE = "simulate_pending"
# 成果动作与临时动作的名字前缀, 后接资产名; 名字只用于显示
RESULT_PREFIX = "QD_Sim_"
PENDING_PREFIX = "QD_Pending_"

# 每帧子步的上限, 与参数规格里的最大值一致
SUBSTEP_LIMIT = 200

# 整段位移与转角都不超过它的碎块视为没有动, 不写关键帧, 停在静止姿态
STATIC_EPSILON = 1e-6

# 烘焙期间仍然放行的事件: 只允许旋转, 平移与缩放视图, 其余输入在烘焙结束前被拦下
NAVIGATION_EVENTS = frozenset(
    {
        "MIDDLEMOUSE",
        "WHEELUPMOUSE",
        "WHEELDOWNMOUSE",
        "MOUSEMOVE",
        "INBETWEEN_MOUSEMOVE",
        "TRACKPADPAN",
        "TRACKPADZOOM",
        "MOUSEROTATE",
        "NDOF_MOTION",
    }
)
