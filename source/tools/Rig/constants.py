"""领域常量 - 工具身份与翻译上下文, 导出集合与产物的命名, 蒙皮网格上的标记属性, 校验参数"""

from ...framework.contract import tool_context

# 工具身份: 场景字段名, 预设目录与翻译上下文都由它派生
TOOL_ID = "rig"
# 本工具的翻译上下文
CONTEXT = tool_context(TOOL_ID)

# 导出集合名 = 源名 + 后缀
EXPORT_SUFFIX = "_Export"
# 构建期间先用带前缀的临时名, 校验通过后才换上最终名字
BUILDING_PREFIX = "QD_Building_"
# 骨架物体的名字后缀; 骨架不止一个时再加序号
RIG_SUFFIX = "_Rig"
# 蒙皮网格物体的名字后缀; 网格不止一个时再加序号
MESH_SUFFIX = "_RigMesh"
# 原物体切换时原物体网格的名字后缀
INTACT_SUFFIX = "_Intact"
# 骨架动作名前缀, 后接骨架名
ACTION_PREFIX = "QD_Rig_"

# 蒙皮网格上记录来源的整数属性: 顶点所属碎块在资产里的序号, 点域
# 属性不会进入 FBX; 检查与测试靠它们把蒙皮网格逐块对回碎块, 下游脚本也可以读
SHARD_ATTRIBUTE = "qd_shard"
# 蒙皮网格上记录来源的整数属性: 顶点在碎块里的原序号, 点域
VERTEX_ATTRIBUTE = "qd_vertex"
# 蒙皮网格上记录来源的整数属性: 面所属的碎块序号, 面域
FACE_SHARD_ATTRIBUTE = "qd_shard_face"

# 构建时抽样校验蒙皮顶点的帧数上限: 首尾加上均匀取的若干帧; 测试里按全部帧校验
VERIFY_FRAMES = 6
# 蒙皮顶点与碎块动画给出的世界顶点之间的容差下限, Blender 单位; 实际容差还要加上不动阈值带来的误差
VERIFY_TOLERANCE = 1e-4
# 判断碎块"从哪一帧开始动"用的位移, Blender 单位, 用于结算时间提示
MOTION_START_EPSILON = 1e-4
# 最后一次起动之后至少要留的秒数, 不足时提示碎块可能还没落定
SETTLE_SECONDS = 1.0
