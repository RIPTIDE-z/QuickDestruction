"""领域常量 - 工具身份与翻译上下文, 导出集合与产物的命名, 蒙皮网格上的标记属性, 引擎预设, 校验参数"""

from ...framework.contract import tool_context

# 工具身份: 场景字段名, 预设目录与翻译上下文都由它派生
TOOL_ID = "game_export"
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

# 导出 FBX 的固定参数: 只导出骨架与网格, 不加叶骨骼, 只导出形变骨骼, 逐帧烘焙动画但不精简
FBX_FIXED = {
    # 只导出骨架与网格, 不带灯光, 相机等
    "object_types": {"ARMATURE", "MESH"},
    # 不给每根骨骼末端加 _end 叶骨骼: 引擎里会多出无用的骨骼
    "add_leaf_bones": False,
    # 只导出参与形变的骨骼
    "use_armature_deform_only": True,
    # 烘焙动画, 只导出骨架当前绑定的那一个动作
    "bake_anim": True,
    "bake_anim_use_all_actions": False,
    "bake_anim_use_nla_strips": False,
    # 只烘焙有关键帧的骨骼
    "bake_anim_use_all_bones": False,
    # 不精简曲线: 与碎块动画逐帧一致, 精简属于后续的优化
    "bake_anim_simplify_factor": 0.0,
    # 按面平滑: 碎块的棱角保持锐利
    "mesh_smooth_type": "FACE",
    # 贴图路径由导出插件自动决定
    "path_mode": "AUTO",
}

# 引擎预设: 标识 -> (前向轴, 上方向轴, 缩放选项, 全局缩放)
# 这些取值是各引擎常用的导出设置, 还没有在目标引擎里逐项验证; 验证清单见 doc/使用说明/GameExport.md
ENGINE_PRESETS = {
    "GENERIC": ("-Z", "Y", "FBX_SCALE_NONE", 1.0),
    "UNREAL": ("-Y", "Z", "FBX_SCALE_UNITS", 1.0),
    "UNITY": ("-Z", "Y", "FBX_SCALE_ALL", 1.0),
}
# 引擎预设写入的字段, 顺序与上表每一行的数值顺序一致
ENGINE_FIELDS = ("axis_forward", "axis_up", "apply_scale", "global_scale")

# 导出时骨架物体临时使用的名字: Blender 把骨架物体写成 FBX 里骨骼之上的顶层节点,
# 虚幻引擎导入时会把这个节点当成多出来的根骨骼, 只有它名为 Armature 时才会跳过,
# 这样骨骼层级的根才是 root
ARMATURE_NODE_NAME = "Armature"
# 场景里已有物体占用 Armature 这个名字时, 导出期间先把它临时改成这个名字
ARMATURE_HOLD_NAME = "QD_Armature_Hold"

# 导出文件名里被源物体名替换的占位符
ASSET_TOKEN = "{asset}"
# 导出文件的扩展名
FBX_EXTENSION = ".fbx"
