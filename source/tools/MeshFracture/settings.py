"""参数规格 - PROPERTY_SPECS 是本工具参数的唯一来源, 同时驱动 RNA 定义, 界面文案, 校验范围与预设字段

参数之间的生效关系也在这里声明一次: 流水线与界面置灰都读同一组规则, 不各自复制
"""

from ...common.schema import Schema
from .constants import CELL_SCALE_MIN, CONTEXT, MAX_ROUNDS, SHARD_LIMIT

# 参数规格: 字段名 -> (bpy.props 的类型名, 该类型的关键字参数)
# name 与 description 是英文原文, 也是语言文件里的键; 其余关键字原样交给 bpy.props
PROPERTY_SPECS = {
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
            "default": "EVEN",
            "description": "How limited splitting is shared when the split fraction is below 1 "
            "or Shard Limit cannot fit a full round.",
        },
    ),
    "use_smooth_faces": (
        "BoolProperty",
        {
            "name": "Smooth Interior",
            "description": "Smooth-shade the new interior faces. Outer faces keep the shading "
            "of the source.",
            "default": False,
        },
    ),
    "use_sharp_edges": (
        "BoolProperty",
        {
            "name": "Sharp Edges",
            "description": "Mark the border between interior and outer faces as sharp, so "
            "smooth-shaded sources keep a clean rim.",
            "default": True,
        },
    ),
    "use_island_split": (
        "BoolProperty",
        {
            "name": "Split Islands",
            "description": "Turn parts that are not connected into separate shards; touching "
            "only at a vertex counts as not connected.",
            "default": True,
        },
    ),
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
    "use_interior_vgroup": (
        "BoolProperty",
        {
            "name": "Interior VGroup",
            "description": "Create an Interior vertex group with the vertices that belong "
            "only to interior faces.",
            "default": False,
        },
    ),
    "use_recenter": (
        "BoolProperty",
        {
            "name": "Recenter",
            "description": "Move the origin of each shard to the mean of its vertices; the "
            "geometry stays in place.",
            "default": True,
        },
    ),
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
    "collection_name": (
        "StringProperty",
        {
            "name": "Collection",
            "description": "Output collection; empty uses the current collection. Each source "
            "gets its own collection inside it, with all shards in <source>_Shards.",
            "default": "cell_collection",
        },
    ),
    "shard_suffix": (
        "StringProperty",
        {
            "name": "Shard Suffix",
            "description": "Suffix of the shard names, added after the source name and followed "
            "by one index per cut level, such as Cube_cell_003_002.",
            "default": "cell",
        },
    ),
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
    "use_debug_points": (
        "BoolProperty",
        {
            "name": "Show Seeds",
            "description": "Create a DebugPoints object for every cut showing its seeds after "
            "jitter. It is not a shard.",
            "default": False,
        },
    ),
    "use_debug_redraw": (
        "BoolProperty",
        {
            "name": "Live Progress",
            "description": "Redraw after every cell and show the cut object as wireframe. "
            "Much slower; no effect in background mode.",
            "default": False,
        },
    ),
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
    """是否应用布尔求交; 开启保留布尔修改器时不应用, 递归, 孤岛拆分, 面合并与断面收尾都随之跳过"""
    return not values.use_debug_bool


def recursion_rounds(values):
    """实际执行的递归轮数; values 可以是参数值或参数组"""
    return values.recursion if bakes_booleans(values) else 0


def uses_interior(values):
    """是否需要区分内部断面: 断面顶点组与锐边都要按面隐藏标记判定断面"""
    return values.use_interior_vgroup or values.use_sharp_edges


def finishes_interior(values):
    """是否在最后做断面收尾"""
    return bakes_booleans(values) and uses_interior(values)


def splits_islands(values):
    """是否拆分孤岛"""
    return bakes_booleans(values) and values.use_island_split


def first_cut_seed_limit(values):
    """首次切割的种子上限: 首次切割种子数为 0 时跟随碎块总数上限, 都为 0 表示不限

    首次切割种子数不会超过碎块总数上限, 这由 SCHEMA 的顺序约束保证
    """
    return values.first_cut_seeds or values.recursion_clamp


def _recursing(values):
    """参数只被递归阶段读取"""
    return recursion_rounds(values) > 0


# 参数的生效条件: 字段名 -> 条件函数; 条件不成立时界面把它置灰但仍可编辑, 没有登记的参数总是生效
# 流水线读同一批函数, 所以界面上可用的参数就是流水线里真正生效的参数
ACTIVE_WHEN = {
    "recursion": bakes_booleans,
    "use_sharp_edges": bakes_booleans,
    "use_interior_vgroup": bakes_booleans,
    "use_island_split": bakes_booleans,
    "sub_cut_pieces": _recursing,
    "recursion_chance": _recursing,
    "recursion_chance_select": _recursing,
    "use_keep_parents": _recursing,
}


# 绑定翻译上下文的参数规格: 生成参数组, 读取, 校验与写回都经由它
# 顺序约束: 首次切割种子数不得超过碎块总数上限, 总数上限为 0 表示不限
SCHEMA = Schema(PROPERTY_SPECS, CONTEXT, orders=(("first_cut_seeds", "recursion_clamp"),))
