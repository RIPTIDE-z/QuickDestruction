"""接收参数规格的校验与序列化 - 不依赖 Blender"""
import math
from .errors import InvalidParameterError

def settings_to_dict(owner, specs):
    """把参数读成纯数据字典; 算子与属性组两种持有者都能用"""
    data = {}
    for key, (kind, _options) in specs.items():
        value = getattr(owner, key)
        # 枚举集合与向量要转成可 JSON 化的类型
        if kind == 'EnumProperty' and isinstance(value, set):
            value = sorted(value)
        elif kind == 'FloatVectorProperty':
            value = list(value)
        data[key] = value
    return data

def validated_values(data, specs):
    """先整体校验再返回可赋值的数据; 旧预设缺字段时仍可正常载入"""
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    values = {}
    for key, (kind, options) in specs.items():
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
            if valid and is_vector:
                value = tuple(value)
        if not valid:
            # 抛出字段标识, 由调用方翻译成人话再显示
            raise InvalidParameterError(key)
        values[key] = value
    return values

def dict_to_settings(owner, data, specs):
    """把校验后的数据写入属性组; 中途失败则回滚已改动的字段"""
    values = validated_values(data, specs)
    previous = {key: getattr(owner, key) for key in values}
    previous = {key: tuple(value) if specs[key][0] == 'FloatVectorProperty' else value
                for key, value in previous.items()}
    try:
        for key, value in values.items():
            setattr(owner, key, value)
    except (TypeError, ValueError, RuntimeError):
        for key, value in previous.items():
            setattr(owner, key, value)
        raise
    return len(values)

def operator_keywords(owner, specs):
    """取可传给算子的关键字参数, 枚举集合转成 set"""
    data = settings_to_dict(owner, specs)
    for key, (kind, options) in specs.items():
        if kind == 'EnumProperty' and 'ENUM_FLAG' in options.get('options', ()):
            data[key] = set(data[key])
    return data
