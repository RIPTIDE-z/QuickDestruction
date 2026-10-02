"""参数规格 - 物理设置的场景参数, 驱动 RNA 定义, 界面文案, 校验范围与预设字段

material 必须排在各物理量之前: 预设载入与回滚都按规格顺序写入, 后写的物理量会覆盖材料联动写入的值
"""

from ...common.schema import Schema
from .constants import CONTEXT, MATERIAL_FIELDS, MATERIALS

# 判断物理量是否被手动改过时允许的误差
EDIT_TOLERANCE = 1e-6


def _load_material(owner, _context):
    """选择材料时把它的物理量写进各字段; 选择自定义时不改动"""
    values = MATERIALS.get(owner.material)
    if values is None:
        return
    for key, value in zip(MATERIAL_FIELDS, values, strict=True):
        setattr(owner, key, value)


def material_values(material):
    """材料对应的物理量字典; 自定义材料返回空字典"""
    values = MATERIALS.get(material)
    return dict(zip(MATERIAL_FIELDS, values, strict=True)) if values is not None else {}


def complete_config(config):
    """补全阶段 API 的配置: 默认值之上先叠材料的物理量, 再叠调用方显式给出的字段, 与界面上的联动一致"""
    material = config.get("material", PROPERTY_SPECS["material"][1]["default"])
    if not isinstance(material, str):
        return dict(config)
    return {**material_values(material), **config}


def material_edited(owner):
    """所选材料的物理量是否被手动改过; 自定义材料总是返回 False"""
    values = MATERIALS.get(owner.material)
    if values is None:
        return False
    return any(
        abs(getattr(owner, key) - value) > EDIT_TOLERANCE
        for key, value in zip(MATERIAL_FIELDS, values, strict=True)
    )


def _material_item(identifier, name, density, note):
    """材料枚举项: 说明里写明密度, 让用户知道选择后会写入什么"""
    return (identifier, name, f"{note} Density {density:g} kg/m3.")


# 参数规格: 字段名 -> (bpy.props 的类型名, 该类型的关键字参数)
PROPERTY_SPECS = {
    "material": (
        "EnumProperty",
        {
            "name": "Material",
            "items": (
                _material_item("CONCRETE", "Concrete", 2400, "Heavy, rough, hardly bounces."),
                _material_item("BRICK", "Brick", 1900, "Rough, hardly bounces."),
                _material_item("STONE", "Stone", 2700, "Heavy and rough."),
                _material_item("GLASS", "Glass", 2500, "Smooth and slightly bouncy."),
                _material_item("CERAMIC", "Ceramic", 2300, "Smooth, a little bouncy."),
                _material_item("WOOD", "Wood", 600, "Light, a little bouncy."),
                _material_item("METAL", "Metal", 7850, "Very heavy and bouncy."),
                ("CUSTOM", "Custom", "Keep the values below as they are."),
            ),
            "default": "CONCRETE",
            "update": _load_material,
            "description": "Choosing a material fills in the values below; they can still be "
            "edited afterwards.",
        },
    ),
    "density": (
        "FloatProperty",
        {
            "name": "Density",
            "description": "Mass of one cubic meter of the material in kg; the mass of a shard "
            "is its volume times this.",
            "min": 1.0,
            "max": 30000.0,
            "soft_max": 10000.0,
            "default": 2400.0,
            "precision": 0,
        },
    ),
    "friction": (
        "FloatProperty",
        {
            "name": "Friction",
            "description": "How strongly shards resist sliding on each other and on the ground.",
            "min": 0.0,
            "max": 1.0,
            "default": 0.8,
        },
    ),
    "restitution": (
        "FloatProperty",
        {
            "name": "Bounciness",
            "description": "How much speed shards keep when they collide; 0 does not bounce.",
            "min": 0.0,
            "max": 1.0,
            "default": 0.05,
        },
    ),
    "linear_damping": (
        "FloatProperty",
        {
            "name": "Linear Damping",
            "description": "Fraction of the moving speed lost over time, like air drag.",
            "min": 0.0,
            "max": 1.0,
            "default": 0.1,
        },
    ),
    "angular_damping": (
        "FloatProperty",
        {
            "name": "Angular Damping",
            "description": "Fraction of the spinning speed lost over time.",
            "min": 0.0,
            "max": 1.0,
            "default": 0.2,
        },
    ),
    "resistance": (
        "FloatProperty",
        {
            "name": "Penetration Resistance",
            "description": "How hard it is for a bullet to pierce the material, relative to "
            "concrete; a bullet spends this much of its penetration per unit of thickness.",
            "min": 0.01,
            "max": 100.0,
            "soft_max": 10.0,
            "default": 1.0,
        },
    ),
    "activation": (
        "EnumProperty",
        {
            "name": "Before Impact",
            "items": (
                (
                    "FROZEN",
                    "Frozen",
                    "Shards hold still until a driver starts them, and stop anything driven into them; "
                    "use Sleeping when a driver covers only part of the shards.",
                ),
                (
                    "SLEEPING",
                    "Sleeping",
                    "Shards sleep until something hits them; touching shards wake up "
                    "together, so a hit can loosen a whole stack.",
                ),
                ("FREE", "Free", "Shards fall under gravity from the first frame."),
            ),
            "default": "FROZEN",
            "description": "How shards behave before a driver reaches them.",
        },
    ),
    "use_anchor_bottom": (
        "BoolProperty",
        {
            "name": "Anchor Bottom",
            "description": "Pin the lowest shards in place so they never move, like a wall "
            "fixed to the ground.",
            "default": False,
        },
    ),
    "anchor_height": (
        "FloatProperty",
        {
            "name": "Anchor Height",
            "description": "Shards whose lowest point is within this height above the lowest "
            "point of the asset are anchored.",
            "min": 0.0,
            "soft_max": 10.0,
            "default": 0.05,
            "subtype": "DISTANCE",
            "unit": "LENGTH",
        },
    ),
    "margin_mode": (
        "EnumProperty",
        {
            "name": "Collision Margin",
            "items": (
                (
                    "AUTO",
                    "Auto",
                    "Let Blender embed its default margin inside each shard; very thin "
                    "shards get no margin.",
                ),
                (
                    "FIXED",
                    "Fixed",
                    "Use the margin below for every shard; the collision shape grows "
                    "outward by it.",
                ),
            ),
            "default": "AUTO",
            "description": "How the collision margin of the shards is chosen.",
        },
    ),
    "margin": (
        "FloatProperty",
        {
            "name": "Margin",
            "description": "Collision margin used in Fixed mode; keep it below half the gap "
            "between shards to avoid overlaps at the start.",
            "min": 0.0,
            "max": 1.0,
            "default": 0.001,
            "precision": 4,
            "subtype": "DISTANCE",
            "unit": "LENGTH",
        },
    ),
}


def _anchoring(values):
    """锚定高度只在开启底部锚定时生效"""
    return values.use_anchor_bottom


def _fixed_margin(values):
    """边距值只在固定边距时生效"""
    return values.margin_mode == "FIXED"


# 参数的生效条件: 字段名 -> 条件函数; 条件不成立时界面置灰但仍可编辑
ACTIVE_WHEN = {
    "anchor_height": _anchoring,
    "margin": _fixed_margin,
}


# 绑定翻译上下文的参数规格
SCHEMA = Schema(PROPERTY_SPECS, CONTEXT)
