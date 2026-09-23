"""规格引擎 - 按规格读取, 校验与写回字段, 供算子与属性组共用, 不依赖 Blender

值的形态只有三种, 转换全部集中在本模块:
    RNA 形态      Blender 属性上的原值: 多选枚举是 set, 向量是 mathutils 数组
    数据形态      可 JSON 化的纯数据: 多选枚举是有序 list, 向量是 list
    冻结形态      只读配置里的值: 多选枚举是升序 tuple, 向量是 tuple
读取与写回各只走一步转换, 不再来回倒手
"""

import math

from .errors import InvalidParameterError


def _is_flag_enum(kind, options):
    """多选枚举: 值是一组字符串标识"""
    return kind == "EnumProperty" and "ENUM_FLAG" in options.get("options", ())


def _is_vector(kind):
    """向量: 值是固定长度的数值序列"""
    return kind == "FloatVectorProperty"


def to_data_value(value, kind, options):
    """RNA 形态 -> 数据形态: 多选枚举转有序列表, 向量转列表"""
    if _is_flag_enum(kind, options) and isinstance(value, set):
        return sorted(value)
    if _is_vector(kind):
        return list(value)
    return value


def to_rna_value(value, kind, options):
    """数据形态 -> RNA 形态: 多选枚举转集合, 向量转元组"""
    if _is_flag_enum(kind, options):
        return set(value)
    if _is_vector(kind):
        return tuple(value)
    return value


def settings_to_dict(owner, specs):
    """把参数读成数据形态; 算子与属性组两种持有者都能用"""
    data = {}
    # 只读规格里声明的字段, 因此算子实例与场景属性组可以共用同一份规格
    for key, (kind, options) in specs.items():
        data[key] = to_data_value(getattr(owner, key), kind, options)
    return data


def operator_keywords(owner, specs):
    """把参数读成 RNA 形态, 直接作为关键字参数传给算子"""
    keywords = {}
    for key, (kind, options) in specs.items():
        keywords[key] = to_rna_value(getattr(owner, key), kind, options)
    return keywords


def validated_values(data, specs):
    """先整体校验再返回 RNA 形态的数据; 预设缺字段时保留界面当前值"""
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
            if _is_flag_enum(kind, options):
                # 多选枚举: 元素必须是合法标识, 允许 list / tuple / set 三种输入
                valid = isinstance(value, (list, tuple, set)) and all(
                    isinstance(item, str) and item in choices for item in value
                )
            else:
                # 单选枚举: 必须是字符串且落在候选项内
                valid = isinstance(value, str) and value in choices
        else:
            # 数值项统一检查类型, 有限性与范围; 向量还要检查长度
            is_vector = _is_vector(kind)
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
        if not valid:
            # 只抛出字段标识, 界面文案由调用方翻译
            raise InvalidParameterError(key)
        values[key] = to_rna_value(value, kind, options)
    return values


def frozen_values(data, specs):
    """校验全部字段并转成冻结形态, 供只读运行配置使用

    多选枚举在这里转成升序元组而不是集合: 冻结的配置必须真的不可变
    """
    values = validated_values(data, specs)
    return {
        key: tuple(sorted(value)) if isinstance(value, set) else value
        for key, value in values.items()
    }


def _rna_snapshot(owner, keys, specs):
    """记录若干字段的 RNA 形态现值, 供写回失败时回滚"""
    # 向量在这里就转成元组, 避免快照持有已经失效的临时数组
    return {key: to_rna_value(getattr(owner, key), specs[key][0], specs[key][1]) for key in keys}


def dict_to_settings(owner, data, specs):
    """把校验后的数据写入属性组; 中途失败则回滚已改动的字段"""
    # 先整体校验: 避免写了一半才发现后面的字段非法
    values = validated_values(data, specs)
    previous = _rna_snapshot(owner, values, specs)
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
