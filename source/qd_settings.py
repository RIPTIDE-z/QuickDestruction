"""持久化设置, 以及界面与算子之间的纯数据参数交换。"""

import math

import bpy
from bpy.props import PointerProperty, StringProperty
from bpy.types import PropertyGroup

from .qd_properties import CONTEXT, PROPERTY_SPECS, property_definitions
from .qd_errors import InvalidParameterError


class CellFractureSettings(PropertyGroup):
    """与算子字段逐一对应的参数组, 用于侧栏常驻保存。"""

    __annotations__ = property_definitions()


class QDSettings(PropertyGroup):
    """挂在场景上的总设置, 目前只包含参数组和最近使用的预设名。"""

    fracture: PointerProperty(type=CellFractureSettings)
    preset_name: StringProperty(name="Preset", translation_context=CONTEXT)


def settings_to_dict(owner):
    """把参数读成纯数据字典; 算子与属性组两种持有者都能用。"""
    data = {}
    for key, (kind, _options) in PROPERTY_SPECS.items():
        value = getattr(owner, key)
        # 枚举集合与向量要转成可 JSON 化的类型
        if kind == 'EnumProperty' and isinstance(value, set):
            value = sorted(value)
        elif kind == 'FloatVectorProperty':
            value = list(value)
        data[key] = value
    return data


def validated_values(data):
    """先整体校验再返回可赋值的数据; 旧预设缺字段时仍可正常载入。"""
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    values = {}
    for key, (kind, options) in PROPERTY_SPECS.items():
        if key not in data:
            # 缺字段直接跳过, 保留界面上的当前值
            continue
        value = data[key]
        valid = True
        if kind == 'BoolProperty':
            # 只接受真正的布尔, 拒绝 0/1 这类会被误认为开关的值
            valid = type(value) is bool
        elif kind == 'StringProperty':
            valid = isinstance(value, str) and '\x00' not in value
        elif kind == 'EnumProperty':
            choices = {item[0] for item in options['items']}
            if 'ENUM_FLAG' in options.get('options', ()):
                valid = isinstance(value, (list, tuple, set)) and all(
                    isinstance(item, str) and item in choices for item in value)
                if valid:
                    value = set(value)
            else:
                valid = isinstance(value, str) and value in choices
        else:
            # 数值项统一检查类型, 有限性与范围; 向量还要检查长度
            is_vector = kind == 'FloatVectorProperty'
            numbers = value if is_vector and isinstance(value, (list, tuple)) else [value]
            valid = not is_vector or (
                isinstance(value, (list, tuple)) and len(value) == options['size'])
            valid = valid and all(
                type(number) in ((int,) if kind == 'IntProperty' else (int, float))
                and math.isfinite(number)
                and options.get('min', -math.inf) <= number <= options.get('max', math.inf)
                for number in numbers)
            # 胞体缩放必须为正, 否则分割平面方向无意义
            if key == 'cell_scale':
                valid = valid and all(number > 0 for number in numbers)
            if valid and is_vector:
                value = tuple(value)
        if not valid:
            # 抛出字段标识, 由调用方翻译成人话再显示
            raise InvalidParameterError(key)
        values[key] = value
    return values


def dict_to_settings(owner, data):
    """把校验后的数据写入属性组; 中途失败则回滚已改动的字段。"""
    values = validated_values(data)
    previous = {key: getattr(owner, key) for key in values}
    previous = {key: tuple(value) if key == 'cell_scale' else value
                for key, value in previous.items()}
    try:
        for key, value in values.items():
            setattr(owner, key, value)
    except (TypeError, ValueError, RuntimeError):
        for key, value in previous.items():
            setattr(owner, key, value)
        raise
    return len(values)


def operator_keywords(owner):
    """取可传给算子的关键字参数, 枚举集合转成 set。"""
    data = settings_to_dict(owner)
    for key, (kind, options) in PROPERTY_SPECS.items():
        if kind == 'EnumProperty' and 'ENUM_FLAG' in options.get('options', ()):
            data[key] = set(data[key])
    return data


def register():
    bpy.utils.register_class(CellFractureSettings)
    bpy.utils.register_class(QDSettings)
    bpy.types.Scene.qd_settings = PointerProperty(type=QDSettings)


def unregister():
    del bpy.types.Scene.qd_settings
    bpy.utils.unregister_class(QDSettings)
    bpy.utils.unregister_class(CellFractureSettings)
