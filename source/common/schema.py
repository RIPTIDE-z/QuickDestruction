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
    """判断是否是多选枚举: 值是一组字符串标识

    kind bpy.props 的类型名
    options 该类型的关键字参数
    返回 是多选枚举为真
    """
    return kind == "EnumProperty" and "ENUM_FLAG" in options.get("options", ())


def _to_data(value, kind, options):
    """RNA 形态转数据形态: 多选枚举转有序列表, 向量转列表

    value RNA 形态的值
    kind bpy.props 的类型名
    options 该类型的关键字参数
    返回 数据形态的值
    """
    # 集合没有顺序, 排序后 JSON 与预设文件的内容才稳定
    if _is_flag_enum(kind, options):
        return sorted(value)
    if kind == "FloatVectorProperty":
        return list(value)
    return value


def _to_rna(value, kind, options):
    """数据形态转 RNA 形态: 多选枚举转集合, 向量转元组

    value 数据形态的值
    kind bpy.props 的类型名
    options 该类型的关键字参数
    返回 RNA 形态的值
    """
    if _is_flag_enum(kind, options):
        return set(value)
    if kind == "FloatVectorProperty":
        return tuple(value)
    return value


def _in_range(number, kind, options):
    """数值的类型, 有限性与范围检查

    number 要检查的数
    kind bpy.props 的类型名, 整数字段不接受浮点数
    options 该类型的关键字参数, 可含 min 与 max
    返回 合法为真
    """
    # 用 type 而不是 isinstance: 布尔值不能冒充数值
    if type(number) not in ((int,) if kind == "IntProperty" else (int, float)):
        return False
    # 整数不做 isfinite: 超大整数转 float 会抛 OverflowError, 直接比较范围即可
    if type(number) is float and not math.isfinite(number):
        return False
    low, high = (INT_MIN, INT_MAX) if kind == "IntProperty" else (-math.inf, math.inf)
    return options.get("min", low) <= number <= options.get("max", high)


def _valid(kind, options, value):
    """按属性类型检查单个值

    kind bpy.props 的类型名
    options 该类型的关键字参数
    value 要检查的值
    返回 合法为真
    """
    if kind == "BoolProperty":
        # 拒绝 0 与 1, 它们在布尔字段上会被当成开关
        return type(value) is bool
    if kind == "StringProperty":
        # NUL 会截断 Blender 内部的 C 字符串
        return isinstance(value, str) and "\x00" not in value
    if kind == "EnumProperty":
        choices = {item[0] for item in options["items"]}
        # 多选枚举的值是一组标识, 单选枚举是单个标识
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


def _keep_order(small, large, edited):
    """生成 update 回调: 改动 edited 使 小 <= 大 不成立时, 让另一方跟着调整到刚好成立

    small 应当较小的字段名
    large 应当较大的字段名, 取值 0 表示不限
    edited 本回调挂在哪个字段上, 即用户刚改动的字段
    返回 Blender 属性的 update 回调
    """

    def update(owner, _context):
        """属性被改动后调用: 大为 0 表示不限, 此时没有约束

        owner 持有这两个字段的参数组
        _context Blender 传入的上下文, 不使用
        """
        low, high = getattr(owner, small), getattr(owner, large)
        if not high or low <= high:
            return
        # 改的是小的一方就抬高大的一方, 改的是大的一方就压低小的一方
        if edited == small:
            setattr(owner, large, low)
        else:
            setattr(owner, small, high)

    return update


class Values:
    """一次操作用的只读参数值 - 字段按属性读取, 与参数组的读法一致, 拼错字段名会立刻抛 AttributeError"""

    # 只有一个内部字典, 用 slots 防止对象上挂其他属性
    __slots__ = ("_data",)

    def __init__(self, data):
        """保存已经校验过的 RNA 形态的值

        data 字段名到值的映射
        """
        # 绕过只读的 __setattr__ 写入内部字典
        object.__setattr__(self, "_data", dict(data))

    def __getattr__(self, name):
        """按字段名取值, 未知字段抛 AttributeError

        name 字段名
        返回 字段的值
        """
        try:
            return self._data[name]
        except KeyError:
            # 去掉 KeyError 的链: 调用方只关心字段名拼错了
            raise AttributeError(name) from None

    def __setattr__(self, name, value):
        """只读: 一次操作中途改参数会让各阶段看到不一致的值, 任何赋值都抛 AttributeError

        name 字段名
        value 要写入的值, 不会被使用
        """
        raise AttributeError(f"Values is read-only: {name}")


class Schema:
    """一份参数规格及其翻译上下文 - 工具的参数组, 预设与阶段 API 都经由它读写参数"""

    def __init__(self, specs, context, orders=()):
        """绑定参数规格, 翻译上下文与字段之间的顺序约束

        specs 字段名到 (bpy.props 类型名, 关键字参数) 的有序映射
        context 参数标签与枚举项名称使用的翻译上下文
        orders 顺序约束, 每项 (小, 大) 要求 小 <= 大, 大为 0 表示不限
        """
        # 字段名 -> (bpy.props 类型名, 关键字参数), 顺序即界面与预设的字段顺序
        self.specs = specs
        # 参数标签与枚举项名称使用的翻译上下文
        self.context = context
        # 顺序约束: 每项 (小, 大) 要求 小 <= 大, 大为 0 表示不限; 每个字段最多出现在一项里
        self.orders = orders
        # 约束只能引用规格里的字段, 且一个字段只能在一项里: 否则两条回调会互相拉扯
        keys = [key for pair in orders for key in pair]
        if len(set(keys)) != len(keys) or not set(keys) <= set(specs):
            raise ValueError("Order constraints must name known fields, each at most once")

    def definitions(self):
        """生成一套新的 RNA 属性定义, 供参数组类使用

        返回 字段名到 bpy.props 属性定义的字典, 每次调用都是全新的对象
        """
        # 延迟导入: 本模块在 Blender 之外也要能被单元测试导入
        from bpy import props

        # 有顺序约束的字段在界面上被改动时, 让另一方跟着调整
        callbacks = {}
        for small, large in self.orders:
            callbacks[small] = _keep_order(small, large, small)
            callbacks[large] = _keep_order(small, large, large)
        # 规格里的类型名直接对应 bpy.props 的构造函数, 标签与提示交给 Blender 自动翻译
        return {
            key: getattr(props, kind)(
                translation_context=self.context,
                **options,
                **({"update": callbacks[key]} if key in callbacks else {}),
            )
            for key, (kind, options) in self.specs.items()
        }

    def defaults(self):
        """全部字段的默认值, 每个规格都必须声明 default

        返回 字段名到默认值的字典, 缺少 default 时抛 KeyError
        """
        return {key: options["default"] for key, (_kind, options) in self.specs.items()}

    def invalid(self, key):
        """构造字段 key 的参数错误; 参数名取规格里的英文名, 未知字段退回字段名本身

        key 字段名
        返回 InvalidParameterError, 由调用方抛出
        """
        label = self.specs[key][1]["name"] if key in self.specs else key
        return InvalidParameterError(key, label, self.context)

    def read(self, owner):
        """把参数组或算子上的参数读成数据形态

        owner 持有这些字段的参数组或算子
        返回 字段名到数据形态值的字典
        """
        return {
            key: _to_data(getattr(owner, key), kind, options)
            for key, (kind, options) in self.specs.items()
        }

    def _check_orders(self, merged):
        """检查顺序约束, 违反时按较小的一方报参数错误

        merged 字段名到值的映射, 缺少的字段按满足约束处理
        """
        for small, large in self.orders:
            # 大为 0 表示不限, 此时没有约束
            if merged.get(large) and merged.get(small, 0) > merged[large]:
                raise self.invalid(small)

    def validate(self, data, base=None):
        """整体校验并返回 RNA 形态的值; 拒绝未知字段, 缺失字段不出现在结果里

        data 要校验的字段字典, 数据形态或 RNA 形态都可以
        base 缺失字段的现有值, 只用来检查顺序约束, 不进入结果
        返回 字段名到 RNA 形态值的字典, 字段顺序与规格一致
        """
        # 未知字段直接拒绝: 拼错的字段名不能被静默忽略
        unknown = set(data) - set(self.specs)
        if unknown:
            raise self.invalid(min(unknown))
        values = {}
        # 按规格顺序遍历, 结果的字段顺序与规格一致
        for key, (kind, options) in self.specs.items():
            if key not in data:
                continue
            if not _valid(kind, options, data[key]):
                raise self.invalid(key)
            values[key] = _to_rna(data[key], kind, options)
        self._check_orders({**(base or {}), **values})
        return values

    def resolve(self, data):
        """缺失字段取默认值, 整体校验后得到只读的参数值; 未知字段直接拒绝

        data 调用方给出的字段字典, 可以只含部分字段
        返回 Values
        """
        return Values(self.validate({**self.defaults(), **data}))

    def write(self, owner, data):
        """校验后整体写入参数组, 任一字段赋值失败时恢复全部原值

        owner 要写入的参数组
        data 字段字典, 可以只含部分字段
        返回 写入的字段数
        """
        # 只给了一部分字段时, 顺序约束要和参数组上的现有值一起检查
        current = {key: getattr(owner, key) for pair in self.orders for key in pair}
        values = self.validate(data, current)
        # 原值按 RNA 形态保存: 向量在这里就转成元组, 不持有会失效的临时数组
        previous = {key: _to_rna(getattr(owner, key), *self.specs[key]) for key in values}
        try:
            for key, value in values.items():
                setattr(owner, key, value)
        except (TypeError, ValueError, RuntimeError):
            # 写到一半失败: 全部恢复, 避免参数组停在半新半旧的状态
            for key, value in previous.items():
                setattr(owner, key, value)
            raise
        return len(values)
