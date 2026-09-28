"""规格引擎 - Schema 绑定一份参数规格与翻译上下文, 负责 RNA 定义, 读取, 校验与写回

值只有两种形态, 转换全部集中在本模块:
    数据形态    可 JSON 化的纯数据: 多选枚举是有序 list, 向量是 list
    RNA 形态    可直接赋给 RNA 属性的值: 多选枚举是 set, 向量是 tuple
"""

import math

from .errors import InvalidParameterError

# 未声明范围的整数字段按 C int 的范围校验, 超出时 RNA 赋值会失败
INT_MIN = -(2**31)
INT_MAX = 2**31 - 1


def _is_flag_enum(kind, options):
    """多选枚举: 值是一组字符串标识"""
    return kind == "EnumProperty" and "ENUM_FLAG" in options.get("options", ())


def _to_data(value, kind, options):
    """RNA 形态转数据形态: 多选枚举转有序列表, 向量转列表"""
    if _is_flag_enum(kind, options):
        return sorted(value)
    if kind == "FloatVectorProperty":
        return list(value)
    return value


def _to_rna(value, kind, options):
    """数据形态转 RNA 形态: 多选枚举转集合, 向量转元组"""
    if _is_flag_enum(kind, options):
        return set(value)
    if kind == "FloatVectorProperty":
        return tuple(value)
    return value


def _in_range(number, kind, options):
    """数值的类型, 有限性与范围检查"""
    # 用 type 而不是 isinstance: 布尔值不能冒充数值
    if type(number) not in ((int,) if kind == "IntProperty" else (int, float)):
        return False
    # 整数不做 isfinite: 超大整数转 float 会抛 OverflowError, 直接比较范围即可
    if type(number) is float and not math.isfinite(number):
        return False
    low, high = (INT_MIN, INT_MAX) if kind == "IntProperty" else (-math.inf, math.inf)
    return options.get("min", low) <= number <= options.get("max", high)


def _valid(kind, options, value):
    """按属性类型检查单个值"""
    if kind == "BoolProperty":
        # 拒绝 0 与 1, 它们在布尔字段上会被当成开关
        return type(value) is bool
    if kind == "StringProperty":
        # NUL 会截断 Blender 内部的 C 字符串
        return isinstance(value, str) and "\x00" not in value
    if kind == "EnumProperty":
        choices = {item[0] for item in options["items"]}
        if _is_flag_enum(kind, options):
            return isinstance(value, (list, tuple, set)) and all(
                isinstance(item, str) and item in choices for item in value
            )
        return isinstance(value, str) and value in choices
    if kind == "FloatVectorProperty":
        # 向量先核对长度, 再逐个分量检查
        return (
            isinstance(value, (list, tuple))
            and len(value) == options["size"]
            and all(_in_range(number, kind, options) for number in value)
        )
    return _in_range(value, kind, options)


class Schema:
    """一份参数规格及其翻译上下文 - 工具的参数组, 预设与阶段 API 都经由它读写参数"""

    def __init__(self, specs, context):
        """绑定参数规格与翻译上下文"""
        # 字段名 -> (bpy.props 类型名, 关键字参数), 顺序即界面与预设的字段顺序
        self.specs = specs
        # 参数标签与枚举项名称使用的翻译上下文
        self.context = context

    def definitions(self):
        """生成一套新的 RNA 属性定义, 供参数组类使用"""
        # 延迟导入: 本模块在 Blender 之外也要能被单元测试导入
        from bpy import props

        # 规格里的类型名直接对应 bpy.props 的构造函数, 标签与提示交给 Blender 自动翻译
        return {
            key: getattr(props, kind)(translation_context=self.context, **options)
            for key, (kind, options) in self.specs.items()
        }

    def defaults(self):
        """全部字段的默认值, 每个规格都必须声明 default"""
        return {key: options["default"] for key, (_kind, options) in self.specs.items()}

    def label(self, key):
        """字段的英文参数名, 未知字段退回字段名本身"""
        return self.specs[key][1]["name"] if key in self.specs else key

    def read(self, owner):
        """把参数组或算子上的参数读成数据形态"""
        return {
            key: _to_data(getattr(owner, key), kind, options)
            for key, (kind, options) in self.specs.items()
        }

    def validate(self, data):
        """整体校验并返回 RNA 形态的值; 拒绝未知字段, 缺失字段不出现在结果里"""
        unknown = set(data) - set(self.specs)
        if unknown:
            # 只抛出字段名, 界面文案由调用方翻译
            raise InvalidParameterError(min(unknown))
        values = {}
        # 按规格顺序遍历, 结果的字段顺序与规格一致
        for key, (kind, options) in self.specs.items():
            if key not in data:
                continue
            if not _valid(kind, options, data[key]):
                raise InvalidParameterError(key)
            values[key] = _to_rna(data[key], kind, options)
        return values

    def write(self, owner, data):
        """校验后整体写入参数组, 任一字段赋值失败时恢复全部原值, 返回写入的字段数"""
        values = self.validate(data)
        # 原值按 RNA 形态保存: 向量在这里就转成元组, 不持有会失效的临时数组
        previous = {key: _to_rna(getattr(owner, key), *self.specs[key]) for key in values}
        try:
            for key, value in values.items():
                setattr(owner, key, value)
        except (TypeError, ValueError, RuntimeError):
            for key, value in previous.items():
                setattr(owner, key, value)
            raise
        return len(values)
