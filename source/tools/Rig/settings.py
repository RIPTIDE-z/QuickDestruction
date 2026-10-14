"""参数规格 - 绑定构建的场景参数: 绑定方式与原物体切换, 不动判定的阈值, 骨骼上限, 断面 UV, 名字限制"""

from ...common.schema import Schema
from .constants import CONTEXT

# 绑定方式的取值: 只导出碎块
SHARDS = "SHARDS"
# 绑定方式的取值: 另外导出原物体, 分成待机与破碎两段动画
SWAP = "SWAP"

# 参数规格: 字段名 -> (bpy.props 的类型名, 该类型的关键字参数)
PROPERTY_SPECS = {
    # 绑定方式: 仅碎块, 或先显示原物体再换成碎块
    "rig_mode": (
        "EnumProperty",
        {
            "name": "Rig Type",
            "items": (
                (SHARDS, "Shards Only", "Export only the shards, visible from the first frame."),
                (
                    SWAP,
                    "Swap from Intact",
                    "Also export the source object, in two files: Idle, a still loop showing "
                    "only the source object, and Destruction, where the source object hides at "
                    "its hide frame and the shards show from their show frame.",
                ),
            ),
            "default": SHARDS,
            "description": "What the exported animation shows before the shards start moving.",
        },
    ),
    # 原物体切换时被隐藏的一方的缩放: 出现帧之前的碎块与隐藏帧之后的原物体
    "hidden_scale": (
        "FloatProperty",
        {
            "name": "Hidden Scale",
            "description": "Scale of the hidden part in Swap from Intact: the shards before "
            "their show frame and the source object from its hide frame. Some engines, such as "
            "Unreal, do not accept a scale of zero.",
            "min": 0.0,
            "max": 0.5,
            "default": 0.001,
            "precision": 3,
        },
    ),
    # 待机动画的长度, 帧
    "idle_length": (
        "IntProperty",
        {
            "name": "Idle Length",
            "description": "Length in frames of the Idle animation, a still loop that shows "
            "only the source object while the shards stay hidden.",
            "min": 1,
            "default": 2,
        },
    ),
    # 破碎动画里原物体从这一帧起缩到隐藏缩放
    "intact_hide_frame": (
        "IntProperty",
        {
            "name": "Intact Hide Frame",
            "description": "Frame of the Destruction animation from which the source object is "
            "scaled down to the hidden scale. At or before the start of the bake it is hidden "
            "from the first frame.",
            "default": 0,
        },
    ),
    # 破碎动画里碎块从这一帧起恢复原尺寸
    "shards_show_frame": (
        "IntProperty",
        {
            "name": "Shards Show Frame",
            "description": "Frame of the Destruction animation from which the shards are shown "
            "at full size. At or before the start of the bake they are shown from the first "
            "frame.",
            "default": 0,
        },
    ),
    # 是否给不动的碎块也绑骨骼, 骨架只由碎块决定, 不同模拟可以共用
    "bind_still": (
        "BoolProperty",
        {
            "name": "Bind Still Shards",
            "description": "Give the shards that never move, such as anchored ones, a bone "
            "too, so the skeleton has one bone per shard whichever shards move in the "
            "simulation, and animations of different simulations can share it. Still bones "
            "carry no keys and stay at their rest pose. Off gives them no bone: they are "
            "merged into the root, or into the still bone in Swap from Intact, which makes a "
            "smaller skeleton.",
            "default": True,
        },
    ),
    # 是否把设计上静止的碎块合并成与源物体一致的静止整体
    "merge_anchored": (
        "BoolProperty",
        {
            "name": "Merge Anchored Shards",
            "description": "Merge the anchored and excluded shards, the ones the physics "
            "settings or driver zones keep in place, into one piece that is the unbroken "
            "source object itself, without seams, bound to the root bone (the still bone in "
            "Swap from Intact). Needs a simulation baked with this version.",
            "default": False,
        },
    ),
    # 不动判定的位移阈值, Blender 单位
    "static_distance": (
        "FloatProperty",
        {
            "name": "Still Distance",
            "description": "Shards that never move farther than this and never turn more than "
            "Still Angle count as still: they get no animation keys, and no bone either when "
            "Bind Still Shards is off.",
            "min": 0.0,
            "soft_max": 0.1,
            "default": 0.001,
            "precision": 4,
            "subtype": "DISTANCE",
            "unit": "LENGTH",
        },
    ),
    # 不动判定的转角阈值, 弧度, 默认约 0.5 度
    "static_angle": (
        "FloatProperty",
        {
            "name": "Still Angle",
            "description": "Largest turn of a shard that still counts as standing still.",
            "min": 0.0,
            "soft_max": 0.0872665,
            "default": 0.0087266,
            "subtype": "ANGLE",
        },
    ),
    # 一段蒙皮网格最多使用的骨骼数, 0 表示不限
    "max_bones_per_mesh": (
        "IntProperty",
        {
            "name": "Bones per Mesh",
            "description": "Most bones, including the root, one skinned mesh may use; the "
            "mesh is split into several when needed. 0 is no limit.",
            "min": 0,
            "default": 0,
        },
    ),
    # 一个骨架最多的骨骼数, 0 表示不限
    "max_bones_total": (
        "IntProperty",
        {
            "name": "Bones per Skeleton",
            "description": "Most bones, including the root, one skeleton may have; shards "
            "beyond it go to extra skeletons exported to separate files. 0 is no limit.",
            "min": 0,
            "default": 0,
        },
    ),
    # 断面 UV 的处理方式
    "interior_uv": (
        "EnumProperty",
        {
            "name": "Interior UV",
            "items": (
                ("KEEP", "Keep", "Leave the UVs of the interior faces as they are."),
                (
                    "BOX",
                    "Box Projection",
                    "Project the interior faces onto the axis their normal points along.",
                ),
            ),
            "default": "BOX",
            "description": "UVs for the faces created by cutting, so a material can be put on "
            "them in the engine.",
        },
    ),
    # 盒投影时每单位距离对应的 UV 长度
    "interior_uv_scale": (
        "FloatProperty",
        {
            "name": "UV Scale",
            "description": "UV length per unit of distance for the box projection.",
            "min": 0.001,
            "soft_max": 10.0,
            "default": 1.0,
        },
    ),
    # 是否只允许 ASCII 名称: 开启时资产名或材质名含非 ASCII 字符就拒绝构建, 导出时再查文件名与物体名
    "ascii_names": (
        "BoolProperty",
        {
            "name": "ASCII Names Only",
            "description": "Refuse to build when the asset or material names contain characters "
            "outside ASCII, which some game engines cannot import; the export checks the file "
            "and object names again.",
            "default": True,
        },
    ),
}


def _swapping(values):
    """隐藏缩放, 待机长度与两个开关帧只在原物体切换时生效

    values 参数组
    返回 生效为真
    """
    return values.rig_mode == SWAP


def _box_projection(values):
    """UV 缩放只在盒投影时生效

    values 参数组
    返回 生效为真
    """
    return values.interior_uv == "BOX"


# 参数的生效条件: 字段名 -> 条件函数; 条件不成立时界面置灰但仍可编辑
ACTIVE_WHEN = {
    "hidden_scale": _swapping,
    "idle_length": _swapping,
    "intact_hide_frame": _swapping,
    "shards_show_frame": _swapping,
    "interior_uv_scale": _box_projection,
}

# 改变产物的字段: 它们变了绑定就该重建, 记在绑定记录里与面板比较; 名字限制只决定拒绝与否, 不在其中
BUILD_FIELDS = (
    "rig_mode",
    "hidden_scale",
    "idle_length",
    "intact_hide_frame",
    "shards_show_frame",
    "bind_still",
    "merge_anchored",
    "static_distance",
    "static_angle",
    "max_bones_per_mesh",
    "max_bones_total",
    "interior_uv",
    "interior_uv_scale",
)

# 绑定构建的参数规格
SCHEMA = Schema(PROPERTY_SPECS, CONTEXT)
