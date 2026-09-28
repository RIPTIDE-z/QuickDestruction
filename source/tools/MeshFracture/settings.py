"""参数规格 - PROPERTY_SPECS 是本工具参数的唯一来源, 同时驱动 RNA 定义, 界面文案, 校验范围与预设字段"""

from ...common.schema import Schema
from .constants import CELL_SCALE_MIN, CONTEXT, SHARD_LIMIT

# 参数规格: 字段名 -> (bpy.props 的类型名, 该类型的关键字参数)
# name 与 description 是英文原文, 也是语言文件里的键; 其余关键字原样交给 bpy.props
PROPERTY_SPECS = {
    "source": (
        "EnumProperty",
        {
            "name": "Source",
            "items": (
                (
                    "VERT_OWN",
                    "Own Verts",
                    "Use original source mesh vertices in world space, before modifiers.",
                ),
                (
                    "VERT_CHILD",
                    "Child Verts",
                    "Use vertices of direct child objects; mesh children use original vertices and "
                    "convertible children use evaluated geometry.",
                ),
                (
                    "PARTICLE_OWN",
                    "Own Particles",
                    "Use current positions of all particles, including unborn and dead ones, from "
                    "all particle systems on the source object; child particles are not included.",
                ),
                (
                    "PARTICLE_CHILD",
                    "Child Particles",
                    "Use particle systems on direct child objects, not child particles of the "
                    "source particle system.",
                ),
                (
                    "PENCIL",
                    "Annotation Pencil",
                    "Use points from the active annotation layer and frame, not Grease Pencil "
                    "object geometry.",
                ),
            ),
            "options": {"ENUM_FLAG"},
            "default": {"PARTICLE_OWN"},
            "description": "Combine selected seed sources. Child means direct child objects. If no "
            "points are found, use the source mesh vertices.",
        },
    ),
    "source_limit": (
        "IntProperty",
        {
            "name": "Seed Limit",
            "description": "Maximum sampled seed points before deduplication; 0 keeps all "
            "points. Does not add points or guarantee a shard count.",
            "min": 0,
            "max": 5000,
            "default": 100,
        },
    ),
    "source_noise": (
        "FloatProperty",
        {
            "name": "Noise",
            "description": "Random seed displacement, from 0 to this fraction of half the "
            "world-space bounding-box diagonal. 0 disables displacement.",
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
            "description": "Seed for sampling, noise, recursive selection and numerical jitter. "
            "The same inputs and seed reproduce the cut within the same Blender "
            "version. Change this value for a different pattern; 0 is a fixed seed.",
            "min": 0,
            "max": 2147483647,
            "default": 0,
        },
    ),
    "cell_scale": (
        "FloatVectorProperty",
        {
            "name": "Axis Weights",
            "description": "Axis weights from 0.001 to 1 for cell dividing planes; only their ratios "
            "matter, and a lower weight stretches cells along that axis. Does not scale the "
            "object. Equal weights give the unweighted partition.",
            "size": 3,
            "min": CELL_SCALE_MIN,
            "max": 1.0,
            "default": (1.0, 1.0, 1.0),
        },
    ),
    "recursion": (
        "IntProperty",
        {
            "name": "Recursion",
            "description": "Additional shatter rounds after the first cut. 0 disables recursion; "
            "growth also depends on the fraction and stopping threshold.",
            "min": 0,
            "max": 5000,
            "default": 1,
        },
    ),
    "recursion_source_limit": (
        "IntProperty",
        {
            "name": "Seeds per Shard",
            "description": "Maximum seed points sampled for each recursively cut shard, before "
            "deduplication; 0 keeps all available points. Parent shards have no particles or "
            "children, so unless Annotation Pencil is selected their seeds are their own "
            "vertices and values above the vertex count have no further effect.",
            "min": 0,
            "max": 5000,
            "default": 8,
        },
    ),
    "recursion_clamp": (
        "IntProperty",
        {
            "name": "Stop Threshold",
            "description": "Stop further recursion when the current shard count per source "
            "object reaches this threshold. One cut or island split may exceed "
            "it. 0 disables the threshold; the first cut is not limited. Generation stops "
            f"with an error beyond {SHARD_LIMIT} shards in total.",
            "min": 0,
            "max": 10000,
            "default": 250,
        },
    ),
    "recursion_chance": (
        "FloatProperty",
        {
            "name": "Fraction",
            "description": "Fraction of current shards selected each round, rounded down. 0 "
            "selects none; 1 selects all. The selection mode chooses which "
            "shards.",
            "min": 0.0,
            "max": 1.0,
            "default": 1.0,
        },
    ),
    "recursion_chance_select": (
        "EnumProperty",
        {
            "name": "Recurse Over",
            "items": (
                ("RANDOM", "Random", "Randomly choose the required fraction of shards."),
                (
                    "SIZE_MIN",
                    "Small Shards",
                    "Choose shards with smaller world-space bounding-box diagonals first.",
                ),
                (
                    "SIZE_MAX",
                    "Big Shards",
                    "Choose shards with larger world-space bounding-box diagonals first.",
                ),
                (
                    "CURSOR_MIN",
                    "Cursor Close",
                    "Choose shard origins closest to the 3D cursor first.",
                ),
                (
                    "CURSOR_MAX",
                    "Cursor Far",
                    "Choose shard origins farthest from the 3D cursor first.",
                ),
            ),
            "default": "RANDOM",
            "description": "Choose which shards participate when the recursion "
            "fraction is below 1. Size uses the world-space "
            "bounding-box diagonal; cursor distance uses object "
            "origins.",
        },
    ),
    "use_smooth_faces": (
        "BoolProperty",
        {
            "name": "Smooth Interior",
            "description": "Smooth-shade newly created interior faces; exterior shading "
            "comes from the source mesh.",
            "default": False,
        },
    ),
    "use_sharp_edges": (
        "BoolProperty",
        {
            "name": "Sharp Edges",
            "description": "Mark edges between interior and exterior faces as sharp, "
            "independently of smooth interior shading.",
            "default": True,
        },
    ),
    "use_island_split": (
        "BoolProperty",
        {
            "name": "Split Islands",
            "description": "Separate disconnected parts of each Boolean result into objects. "
            "This can produce more shards than seed points.",
            "default": True,
        },
    ),
    "margin": (
        "FloatProperty",
        {
            "name": "Margin",
            "description": "Inset each seed-dividing plane by this world-space length in Blender "
            "units. Opposing faces have about twice this gap; large values can erase "
            "cells.",
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
            "name": "Interior Slot",
            "description": "Zero-based material slot index for interior faces; 0 selects the first "
            "slot. Shards copy the source material slots and UV/color layers; interior UVs "
            "are not unwrapped.",
            "default": 0,
            "min": 0,
        },
    ),
    "use_interior_vgroup": (
        "BoolProperty",
        {
            "name": "Interior VGroup",
            "description": "Create the vertex group Interior for interior-only vertices. Boundary "
            "vertices shared with exterior faces are excluded.",
            "default": False,
        },
    ),
    "use_recenter": (
        "BoolProperty",
        {
            "name": "Recenter",
            "description": "Move each shard origin to its geometry center using MEDIAN, without moving its geometry.",
            "default": True,
        },
    ),
    "use_remove_original": (
        "BoolProperty",
        {
            "name": "Remove Parents",
            "description": "Delete generated parent shards after successful recursive "
            "replacement. When off, cut parents move into the <source>_Parents collection, "
            "are not cut again and are not part of the result. The originally selected "
            "source objects are always kept.",
            "default": True,
        },
    ),
    "collection_name": (
        "StringProperty",
        {
            "name": "Collection",
            "description": "Output collection name. Empty uses the current collection. A named collection must be editable and visible in this view layer; create it only if no collection has that name. All shards are placed here or in its group collections.",
            "default": "cell_collection",
        },
    ),
    "shard_prefix": (
        "StringProperty",
        {
            "name": "Shard Prefix",
            "description": "Prefix of shard names, followed by the source object name, for "
            "example cell_Cube_001, or cell_Cube_L1G1_001 with grouping. It is never "
            "used for collection names.",
            "default": "cell",
        },
    ),
    "use_shard_collections": (
        "BoolProperty",
        {
            "name": "Group Shards",
            "description": "Put the shards of each recursively cut shard into its own "
            "collection named <source>_L<level>_G<group>, inside the output "
            "collection. When off, every shard is linked directly into "
            "the output collection.",
            "default": True,
        },
    ),
    "shard_index_padding": (
        "IntProperty",
        {
            "name": "Index Digits",
            "description": "Minimum digit width of the shard sequence number, padded with zeros. "
            "Longer numbers are not cut. Does not pad level/group numbers or guarantee "
            "global creation-order sorting.",
            "min": 0,
            "max": 8,
            "default": 3,
        },
    ),
    "use_debug_points": (
        "BoolProperty",
        {
            "name": "Seed Points",
            "description": "Create a DebugPoints object for every cut, including recursive "
            "cuts, showing its seed points after noise. It is diagnostic geometry, not a shard.",
            "default": False,
        },
    ),
    "use_debug_redraw": (
        "BoolProperty",
        {
            "name": "Live Progress",
            "description": "Refresh the viewport after every cell to show progress, drawing the "
            "object being cut as wireframe until its cut ends. Makes cutting several times "
            "slower; has no effect in background mode.",
            "default": False,
        },
    ),
    "use_debug_bool": (
        "BoolProperty",
        {
            "name": "Unapplied Boolean",
            "description": "Keep enabled Boolean modifiers on convex base meshes; evaluated geometry still shows the intersection. Skip recursion, island splitting and interior finishing.",
            "default": False,
        },
    ),
}


# 绑定翻译上下文的参数规格: 生成参数组, 读取, 校验与写回都经由它
SCHEMA = Schema(PROPERTY_SPECS, CONTEXT)
