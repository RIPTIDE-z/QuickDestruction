"""参数规格 - PROPERTY_SPECS 是本工具参数的唯一来源, 同时驱动 RNA 定义, 界面文案, 校验范围与预设字段

参数之间的生效关系也在这里声明一次: 流水线与界面置灰都读同一组规则, 不各自复制
"""

from ...common.schema import Schema
from .constants import CELL_SCALE_MIN, CONTEXT, MAX_ROUNDS, SHARD_LIMIT

# 参数规格: 字段名 -> (bpy.props 的类型名, 该类型的关键字参数)
# name 与 description 是英文原文, 也是语言文件里的键; 其余关键字原样交给 bpy.props
PROPERTY_SPECS = {
    # 种子来源: 顶点, 粒子与注解铅笔, 可多选取并集
    "source": (
        "EnumProperty",
        {
            "name": "Seed Source",
            "items": (
                (
                    "VERT_OWN",
                    "Own Verts",
                    "Use the original vertices of the source mesh, before modifiers.",
                ),
                (
                    "VERT_CHILD",
                    "Child Verts",
                    "Use the vertices of direct child objects; curves and similar types use "
                    "their evaluated geometry.",
                ),
                (
                    "PARTICLE_OWN",
                    "Own Particles",
                    "Use every particle position of the source object, including unborn and "
                    "dead ones; child particles are not included.",
                ),
                (
                    "PARTICLE_CHILD",
                    "Child Particles",
                    "Use every particle position of direct child objects, including unborn and "
                    "dead ones; child particles are not included.",
                ),
                (
                    "PENCIL",
                    "Annotation Pencil",
                    "Use the points of the active annotation layer at the current frame.",
                ),
            ),
            "options": {"ENUM_FLAG"},
            "default": {"VERT_OWN"},
            "description": "Where the cut seeds come from; several sources can be combined. "
            "Without any point, the source mesh vertices are used.",
        },
    ),
    # 首次切割的种子数上限, 0 表示跟随碎块总数上限
    "first_cut_seeds": (
        "IntProperty",
        {
            "name": "First Cut Seeds",
            "description": "Most seeds the first cut uses, picked at random from the seed source. "
            "Each seed makes at most one shard, so there can be fewer shards, or more after "
            "island splitting. It cannot exceed Shard Limit. 0 follows Shard Limit, or uses "
            "all seeds when that is 0 too. Recursive cuts use Sub-Cut Pieces.",
            "min": 0,
            "max": 5000,
            "soft_max": 500,
            "default": 100,
        },
    ),
    # 种子扰动强度, 0 到 1
    "source_noise": (
        "FloatProperty",
        {
            "name": "Seed Jitter",
            "description": "Move each seed randomly by up to this fraction of half the "
            "bounding-box diagonal of the object being cut. 0 disables it.",
            "min": 0.0,
            "max": 1.0,
            "default": 0.5,
            "precision": 3,
        },
    ),
    # 随机种子, 相同种子与相同输入得到相同结果
    "random_seed": (
        "IntProperty",
        {
            "name": "Random Seed",
            "description": "Random seed for seed picking, seed jitter, recursive selection and "
            "cell vertex jitter. Every value gives one fixed result in the same Blender "
            "version; change it for a different pattern.",
            "min": 0,
            "max": 2147483647,
            "default": 0,
        },
    ),
    # 三轴胞体权重, 不等时胞体被拉伸
    "cell_scale": (
        "FloatVectorProperty",
        {
            "name": "Axis Weights",
            "description": "Per-axis weights of the cell planes; only their ratios matter, and a "
            "lower weight stretches the cells along that axis. Equal weights give a plain "
            "Voronoi cut.",
            "size": 3,
            "min": CELL_SCALE_MIN,
            "max": 1.0,
            "default": (1.0, 1.0, 1.0),
        },
    ),
    # 递归细分的轮数
    "recursion": (
        "IntProperty",
        {
            "name": "Rounds",
            "description": "Extra rounds that cut the shards again after the first cut. 0 "
            "disables recursion.",
            "min": 0,
            "max": MAX_ROUNDS,
            "soft_max": 5,
            "default": 1,
        },
    ),
    # 递归时每个父块切成的块数上限
    "sub_cut_pieces": (
        "IntProperty",
        {
            "name": "Sub-Cut Pieces",
            "description": "Most pieces each shard is cut into per round; the real count can be "
            "lower, and island splitting can add more. At least 2, because one seed only "
            "recuts a shard into itself. Values above the vertex count of a shard have no "
            "further effect.",
            "min": 2,
            "max": 5000,
            "soft_max": 32,
            "default": 8,
        },
    ),
    # 碎块总数上限, 0 表示不限, 受硬上限 SHARD_LIMIT 约束
    "recursion_clamp": (
        "IntProperty",
        {
            "name": "Shard Limit",
            "description": "Most shards kept per source object, counting the first cut, so it "
            "cannot be below First Cut Seeds. 0 disables the limit. Island splitting can "
            f"exceed it slightly; more than {SHARD_LIMIT} shards in total stops with an error.",
            "min": 0,
            "max": SHARD_LIMIT,
            "soft_max": 1000,
            "default": 250,
        },
    ),
    # 每轮被细分的碎块比例, 0 到 1
    "recursion_chance": (
        "FloatProperty",
        {
            "name": "Split Fraction",
            "description": "Fraction of the shards cut again each round, rounded down.",
            "min": 0.0,
            "max": 1.0,
            "default": 1.0,
        },
    ),
    # 按比例挑选碎块的方式: 随机, 均匀, 按大小或按到游标的距离
    "recursion_chance_select": (
        "EnumProperty",
        {
            "name": "Split Priority",
            "items": (
                (
                    "EVEN",
                    "None - Even Split",
                    "Give every shard a few pieces so the allowed total is shared evenly; "
                    "bigger shards get the remainder first.",
                ),
                ("RANDOM", "Random", "Pick shards at random."),
                (
                    "SIZE_MIN",
                    "Small Shards",
                    "Smaller shards first, by bounding-box diagonal.",
                ),
                (
                    "SIZE_MAX",
                    "Big Shards",
                    "Bigger shards first, by bounding-box diagonal.",
                ),
                (
                    "CURSOR_MIN",
                    "Cursor Close",
                    "Shards whose origin is nearest the 3D cursor first.",
                ),
                (
                    "CURSOR_MAX",
                    "Cursor Far",
                    "Shards whose origin is farthest from the 3D cursor first.",
                ),
            ),
            "default": "SIZE_MAX",
            "description": "How limited splitting is shared when the split fraction is below 1 "
            "or Shard Limit cannot fit a full round.",
        },
    ),
    # 是否把源网格当作实体填充, 关闭时只保留嵌套曲面之间的壳层
    "use_fill": (
        "BoolProperty",
        {
            "name": "Fill Solid",
            "description": "Treat the source as a solid, so the space inside nested closed "
            "surfaces is filled too. Turn it off for a hollow shell made of an outer and an "
            "inner surface: shards then keep only the wall between them and never reach into "
            "the cavity. It makes no difference for meshes without nested surfaces.",
            "default": True,
        },
    ),
    # 是否把胞体的面设为平滑着色
    "use_smooth_faces": (
        "BoolProperty",
        {
            "name": "Smooth Interior",
            "description": "Smooth-shade the new interior faces. Outer faces keep the shading "
            "of the source.",
            "default": False,
        },
    ),
    # 是否把内外交界的边标记为锐边
    "use_sharp_edges": (
        "BoolProperty",
        {
            "name": "Sharp Edges",
            "description": "Mark the border between interior and outer faces as sharp, so "
            "smooth-shaded sources keep a clean rim.",
            "default": True,
        },
    ),
    # 是否把含多个孤岛的碎块拆成独立物体
    "use_island_split": (
        "BoolProperty",
        {
            "name": "Split Islands",
            "description": "Turn parts that are not connected into separate shards; touching "
            "only at a vertex counts as not connected.",
            "default": True,
        },
    ),
    # 切面内缩量, 相邻碎块之间的缝隙是它的两倍
    "margin": (
        "FloatProperty",
        {
            "name": "Cut Inset",
            "description": "Push each cutting plane inward by this distance, leaving a gap of "
            "about twice this between shards. Too large removes cells.",
            "min": 0.0,
            "max": 1.0,
            "default": 0.001,
            "precision": 4,
            "subtype": "DISTANCE",
            "unit": "LENGTH",
        },
    ),
    # 断面使用的材质槽编号
    "material_index": (
        "IntProperty",
        {
            "name": "Cut Material",
            "description": "Material slot of the interior faces, also in recursive cuts. 0 is "
            "the first slot.",
            "default": 0,
            "min": 0,
        },
    ),
    # 是否建立内部断面顶点组
    "use_interior_vgroup": (
        "BoolProperty",
        {
            "name": "Interior VGroup",
            "description": "Create an Interior vertex group with the vertices that belong "
            "only to interior faces.",
            "default": False,
        },
    ),
    # 部件转碎块时是否让部件互不重叠
    "use_parts_disjoint": (
        "BoolProperty",
        {
            "name": "Separate Overlaps",
            "description": "Only for Use Parts as Shards: where parts overlap, the bigger part "
            "keeps the shared volume and the smaller one is cut back, so the shards no longer "
            "push each other apart in the simulation. A part buried completely inside a bigger "
            "one is dropped.",
            "default": True,
        },
    ),
    # 是否把碎块原点移到顶点平均位置
    "use_recenter": (
        "BoolProperty",
        {
            "name": "Recenter",
            "description": "Move the origin of each shard to the mean of its vertices; the "
            "geometry stays in place.",
            "default": True,
        },
    ),
    # 是否保留被细分的父碎块
    "use_keep_parents": (
        "BoolProperty",
        {
            "name": "Keep Parents",
            "description": "Keep the shards that were cut again, in <source>_Parents_L<level> "
            "collections, instead of deleting them. They are not cut again and are not part "
            "of the result.",
            "default": False,
        },
    ),
    # 输出集合名, 空名表示当前集合
    "collection_name": (
        "StringProperty",
        {
            "name": "Collection",
            "description": "Output collection; empty uses the current collection. Each source "
            "gets its own collection inside it, with all shards in <source>_Shards.",
            "default": "cell_collection",
        },
    ),
    # 生成成功后是否隐藏源物体
    "hide_source": (
        "BoolProperty",
        {
            "name": "Hide Source",
            "description": "Hide each source object in the viewport and in renders once its "
            "shards are created. The source is kept and can be shown again.",
            "default": True,
        },
    ),
    # 碎块名后缀
    "shard_suffix": (
        "StringProperty",
        {
            "name": "Shard Suffix",
            "description": "Suffix of the shard names, added after the source name and followed "
            "by one index per cut level, such as Cube_cell_003_002.",
            "default": "cell",
        },
    ),
    # 碎块名里序号的最小位数
    "shard_index_padding": (
        "IntProperty",
        {
            "name": "Index Digits",
            "description": "Minimum digits of each index in the shard names, zero padded.",
            "min": 0,
            "max": 8,
            "default": 3,
        },
    ),
    # 调试: 显示扰动后的种子点
    "use_debug_points": (
        "BoolProperty",
        {
            "name": "Show Seeds",
            "description": "Create a DebugPoints object for every cut showing its seeds after "
            "jitter. It is not a shard.",
            "default": False,
        },
    ),
    # 调试: 每建一个胞体就重绘并以线框显示被切物体, 明显变慢
    "use_debug_redraw": (
        "BoolProperty",
        {
            "name": "Live Progress",
            "description": "Redraw after every cell and show the cut object as wireframe. "
            "Much slower; no effect in background mode.",
            "default": False,
        },
    ),
    # 调试: 保留布尔修改器而不应用
    "use_debug_bool": (
        "BoolProperty",
        {
            "name": "Keep Boolean Modifier",
            "description": "Keep the Boolean modifiers on the convex cells instead of applying "
            "them. Skips recursion, island splitting, face merging and interior finishing.",
            "default": False,
        },
    ),
}


def bakes_booleans(values):
    """是否应用布尔求交; 开启保留布尔修改器时不应用, 递归, 孤岛拆分, 面合并与断面收尾都随之跳过

    values 参数值或参数组
    返回 应用为真
    """
    return not values.use_debug_bool


def recursion_rounds(values):
    """实际执行的递归轮数

    values 参数值或参数组
    返回 轮数; 保留布尔修改器时为 0
    """
    return values.recursion if bakes_booleans(values) else 0


def hollows_source(values):
    """是否把源网格里嵌套的内层曲面当作空腔: 只在应用了布尔结果且关闭填充时才处理

    保留布尔修改器时修改器引用的是源物体本身, 没有地方放修正后的临时操作数, 所以不处理
    values 参数值或参数组
    返回 当作空腔为真
    """
    return bakes_booleans(values) and not values.use_fill


def uses_interior(values):
    """是否需要区分内部断面: 断面顶点组与锐边都要按面隐藏标记判定断面

    values 参数值或参数组
    返回 需要为真
    """
    return values.use_interior_vgroup or values.use_sharp_edges


def finishes_interior(values):
    """是否在最后做断面收尾

    values 参数值或参数组
    返回 需要为真
    """
    return bakes_booleans(values) and uses_interior(values)


def splits_islands(values):
    """是否拆分孤岛

    values 参数值或参数组
    返回 拆分为真
    """
    return bakes_booleans(values) and values.use_island_split


def first_cut_seed_limit(values):
    """首次切割的种子上限: 首次切割种子数为 0 时跟随碎块总数上限, 都为 0 表示不限

    首次切割种子数不会超过碎块总数上限, 这由 SCHEMA 的顺序约束保证
    values 参数值或参数组
    返回 种子上限, 0 表示不限
    """
    return values.first_cut_seeds or values.recursion_clamp


def _recursing(values):
    """参数只被递归阶段读取, 递归轮数为 0 时它们不生效

    values 参数值或参数组
    返回 在递归为真
    """
    return recursion_rounds(values) > 0


# 参数的生效条件: 字段名 -> 条件函数; 条件不成立时界面把它置灰但仍可编辑, 没有登记的参数总是生效
# 流水线读同一批函数, 所以界面上可用的参数就是流水线里真正生效的参数
ACTIVE_WHEN = {
    "recursion": bakes_booleans,
    "use_sharp_edges": bakes_booleans,
    "use_interior_vgroup": bakes_booleans,
    "use_island_split": bakes_booleans,
    "use_fill": bakes_booleans,
    "sub_cut_pieces": _recursing,
    "recursion_chance": _recursing,
    "recursion_chance_select": _recursing,
    "use_keep_parents": _recursing,
}


# 绑定翻译上下文的参数规格: 生成参数组, 读取, 校验与写回都经由它
# 顺序约束: 首次切割种子数不得超过碎块总数上限, 总数上限为 0 表示不限
SCHEMA = Schema(PROPERTY_SPECS, CONTEXT, orders=(("first_cut_seeds", "recursion_clamp"),))
