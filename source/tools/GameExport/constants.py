"""领域常量 - 工具身份与翻译上下文, FBX 的固定参数, 引擎预设, 骨架节点名与文件命名"""

from ...framework.contract import tool_context

# 工具身份: 场景字段名, 预设目录与翻译上下文都由它派生
TOOL_ID = "game_export"
# 本工具的翻译上下文
CONTEXT = tool_context(TOOL_ID)

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
