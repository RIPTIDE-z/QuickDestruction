"""领域常量 - 工具身份与翻译上下文, 导出集合与产物的命名, 蒙皮网格上的标记属性, 引擎预设, 校验参数"""

from ...framework.contract import tool_context

# 工具身份: 场景字段名, 预设目录与翻译上下文都由它派生
TOOL_ID = "game_export"
# 本工具的翻译上下文
CONTEXT = tool_context(TOOL_ID)

# 导出集合名 = 源名 + 后缀; 构建期间先用带前缀的临时名, 校验通过后才换上最终名字
EXPORT_SUFFIX = "_Export"
BUILDING_PREFIX = "QD_Building_"
# 骨架与蒙皮网格的名字后缀; 骨架或网格不止一个时再加序号
RIG_SUFFIX = "_Rig"
MESH_SUFFIX = "_RigMesh"
# 骨架动作名前缀, 后接骨架名
ACTION_PREFIX = "QD_Rig_"

# 蒙皮网格上记录来源的整数属性: 顶点所属碎块在资产里的序号, 顶点在碎块里的原序号, 面所属的碎块序号
# 属性不会进入 FBX; 检查与测试靠它们把蒙皮网格逐块对回碎块, 下游脚本也可以读
SHARD_ATTRIBUTE = "qd_shard"
VERTEX_ATTRIBUTE = "qd_vertex"
FACE_SHARD_ATTRIBUTE = "qd_shard_face"

# 构建时抽样校验蒙皮顶点的帧数上限: 首尾加上均匀取的若干帧; 测试里按全部帧校验
VERIFY_FRAMES = 6
# 蒙皮顶点与碎块动画给出的世界顶点之间的容差下限, Blender 单位; 实际容差还要加上不动阈值带来的误差
VERIFY_TOLERANCE = 1e-4
# 判断碎块"从哪一帧开始动"用的位移, Blender 单位, 用于结算时间提示
MOTION_START_EPSILON = 1e-4
# 最后一次起动之后至少要留的秒数, 不足时提示碎块可能还没落定
SETTLE_SECONDS = 1.0

# 导出 FBX 的固定参数: 只导出骨架与网格, 不加叶骨骼, 只导出形变骨骼, 逐帧烘焙动画但不精简
FBX_FIXED = {
    "object_types": {"ARMATURE", "MESH"},
    "add_leaf_bones": False,
    "use_armature_deform_only": True,
    "bake_anim": True,
    "bake_anim_use_all_actions": False,
    "bake_anim_use_nla_strips": False,
    "bake_anim_use_all_bones": False,
    "bake_anim_simplify_factor": 0.0,
    "mesh_smooth_type": "FACE",
    "path_mode": "AUTO",
}
# FBX 导出插件的模块名
FBX_ADDON = "io_scene_fbx"

# 引擎预设: 标识 -> (前向轴, 上方向轴, 缩放选项, 全局缩放)
# 这些取值是各引擎常用的导出设置, 还没有在目标引擎里逐项验证; 验证清单见 doc/使用说明/GameExport.md
ENGINE_PRESETS = {
    "GENERIC": ("-Z", "Y", "FBX_SCALE_NONE", 1.0),
    "UNREAL": ("-Y", "Z", "FBX_SCALE_UNITS", 1.0),
    "UNITY": ("-Z", "Y", "FBX_SCALE_ALL", 1.0),
}
# 引擎预设写入的字段, 顺序与上表每一行的数值顺序一致
ENGINE_FIELDS = ("axis_forward", "axis_up", "apply_scale", "global_scale")

# 导出文件名里被源物体名替换的占位符与默认文件名
ASSET_TOKEN = "{asset}"
FBX_EXTENSION = ".fbx"
