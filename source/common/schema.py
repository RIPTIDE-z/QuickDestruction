"""规格引擎 - 按规格读取, 校验与写回字段, 供算子与属性组共用, 不依赖 Blender"""

import math

from .errors import InvalidParameterError


def settings_to_dict(owner, specs):
    """把参数读成纯数据字典; 算子与属性组两种持有者都能用"""
    data = {}
    # 只读规格里声明的字段, 因此算子实例与场景属性组可以共用同一份规格
    for key, (kind, _options) in specs.items():
        value = getattr(owner, key)
        # 枚举集合是 set, 向量是 mathutils 数组, 两者都要转成可 JSON 化的类型
        if kind == "EnumProperty" and isinstance(value, set):
            value = sorted(value)
        elif kind == "FloatVectorProperty":
            value = list(value)
        data[key] = value
    return data


def validated_values(data, specs):
    """先整体校验再返回可赋值的数据; 预设缺字段时保留界面当前值"""
    if not isinstance(data, dict):
        raise ValueError("Preset must be a JSON object")
    values = {}
    # 遍历规格而不是遍历 data: 缺字段自然跳过, 规格外的键也不会被带出去
    for key, (kind, options) in specs.items():
        if key not in data:
            # 缺字段直接跳过, 保留界面上的当前值
            continue
        value = data[key]
        valid = True
        if kind == "BoolProperty":
            # 用 type 而不是 isinstance: 拒绝 0/1, 它们在布尔字段上会被当成开关
            valid = type(value) is bool
        elif kind == "StringProperty":
            # NUL 会截断 Blender 内部的 C 字符串, 必须拒绝
            valid = isinstance(value, str) and "\x00" not in value
        elif kind == "EnumProperty":
            choices = {item[0] for item in options["items"]}
            if "ENUM_FLAG" in options.get("options", ()):
                # 多选枚举: 元素必须是合法标识, 允许 list / tuple / set 三种输入
                valid = isinstance(value, (list, tuple, set)) and all(
                    isinstance(item, str) and item in choices for item in value
                )
                if valid:
                    # 统一成 set, 与 ENUM_FLAG 在 RNA 上的期望类型一致
                    value = set(value)
            else:
                # 单选枚举: 必须是字符串且落在候选项内
                valid = isinstance(value, str) and value in choices
        else:
            # 数值项统一检查类型, 有限性与范围; 向量还要检查长度
            is_vector = kind == "FloatVectorProperty"
            # 向量按分量检查, 标量包成单元素列表走同一段判断
            numbers = value if is_vector and isinstance(value, (list, tuple)) else [value]
            # 向量长度必须等于规格声明的 size
            valid = not is_vector or (
                isinstance(value, (list, tuple)) and len(value) == options["size"]
            )
            # 整数项拒绝 float; 所有数值都要求有限且落在 min / max 内
            valid = valid and all(
                type(number) in ((int,) if kind == "IntProperty" else (int, float))
                and math.isfinite(number)
                and options.get("min", -math.inf) <= number <= options.get("max", math.inf)
                for number in numbers
            )
            if valid and is_vector:
                # 统一成元组, 便于冻结与比较
                value = tuple(value)
        if not valid:
            # 只抛出字段标识, 界面文案由调用方翻译
            raise InvalidParameterError(key)
        values[key] = value
    return values


def dict_to_settings(owner, data, specs):
    """把校验后的数据写入属性组; 中途失败则回滚已改动的字段"""
    # 先整体校验: 避免写了一半才发现后面的字段非法
    values = validated_values(data, specs)
    previous = {key: getattr(owner, key) for key in values}
    # 向量读回来是 mathutils 数组, 先转元组, 否则回滚时会把临时对象写回 RNA
    previous = {
        key: tuple(value) if specs[key][0] == "FloatVectorProperty" else value
        for key, value in previous.items()
    }
    try:
        for key, value in values.items():
            setattr(owner, key, value)
    except (TypeError, ValueError, RuntimeError):
        # RNA 赋值可能因类型或范围失败, 此时把已改动的字段逐个恢复成原值
        for key, value in previous.items():
            setattr(owner, key, value)
        raise
    # 返回实际写入的字段数, 供界面报告"载入了几个参数"
    return len(values)


def operator_keywords(owner, specs):
    """取可传给算子的关键字参数, 枚举集合转成 set"""
    data = settings_to_dict(owner, specs)
    for key, (kind, options) in specs.items():
        # settings_to_dict 把多选枚举转成了有序列表, 传给算子时要还原成 set
        if kind == "EnumProperty" and "ENUM_FLAG" in options.get("options", ()):
            data[key] = set(data[key])
    return data
