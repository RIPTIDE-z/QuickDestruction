"""界面约定 - 工具面板与算子的混入类, 失败报告的翻译, 以及按分组表绘制参数"""

import traceback
from typing import NamedTuple

from ..common.errors import DomainError, InvalidParameterError
from ..common.translate import tr, tr_report
from .constants import CONTEXT, SIDEBAR_CATEGORY


class ToolPanel:
    """工具侧栏面板的公共设置 - 固定在 3D 视图侧栏的插件标签页, 与其它工具面板并列"""

    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = SIDEBAR_CATEGORY


def failure_text(operator, error):
    """把异常换成报告文本: 领域错误按自己的上下文翻译, 参数错误先翻译参数名, 其余异常原样展示"""
    schema = operator.schema
    if isinstance(error, InvalidParameterError) and schema is not None:
        name = tr(schema.context, schema.label(error.field))
        detail = tr_report(CONTEXT, "Invalid parameter: {name}", name=name)
    elif isinstance(error, DomainError):
        detail = tr_report(error.context, error.message, **error.values)
    else:
        detail = str(error)
    operation = tr_report(operator.bl_translation_context, operator.bl_label)
    return tr_report(CONTEXT, "{operation} failed: {detail}", operation=operation, detail=detail)


class ToolOperator:
    """工具算子的公共执行流程 - 子类实现 run, 这里把结果与异常转成可翻译的报告"""

    # 参数规格: 把参数错误里的字段名换成界面参数名, 没有参数的算子保持 None
    schema = None

    def execute(self, context):
        """执行 run 并报告结果, 失败时取消本次操作"""
        try:
            message = self.run(context)
        except Exception as error:
            # 领域错误与参数错误是可预期的, 其余异常打印堆栈便于排查
            if not isinstance(error, (DomainError, InvalidParameterError)):
                traceback.print_exc()
            self.report({"ERROR"}, failure_text(self, error))
            return {"CANCELLED"}
        if message:
            self.report({"INFO"}, message)
        return {"FINISHED"}


class ParamGroup(NamedTuple):
    """参数面板里的一个分组"""

    # 分组标题, 按工具上下文翻译
    title: str
    # 每行是若干字段名, 一行内的字段各自判断启用状态
    rows: tuple
    # 分组末尾的提示行, 写 RNA 说明里没有但用户一定会踩的前提
    notes: tuple = ()


def _draw_field(layout, owner, schema, key):
    """多选枚举画成一排按钮, 向量逐个分量标注轴名, 其余用默认控件"""
    kind, options = schema.specs[key]
    if "ENUM_FLAG" in options.get("options", ()):
        layout.use_property_split = False
        for identifier, name, _description in options["items"]:
            layout.prop_enum(owner, key, identifier, text=name, text_ctxt=schema.context)
    elif kind == "FloatVectorProperty":
        # 显式写出属性名与轴名, 避免 Blender 用属性名顶替第一个分量的标签
        layout.use_property_split = False
        layout.alignment = "EXPAND"
        layout.label(text=options["name"], text_ctxt=schema.context)
        for index in range(options["size"]):
            if index:
                layout.separator()
            layout.prop(owner, key, index=index, text="XYZW"[index], translate=False)
    else:
        layout.prop(owner, key)


def draw_parameters(layout, owner, schema, groups, enabled):
    """按分组表绘制参数, enabled(owner, key) 为假的控件置灰"""
    # 紧凑排布由分组表决定, 不按属性分栏, 侧栏窄时也不会挤掉标签
    layout.use_property_split = False
    layout.use_property_decorate = False
    for group in groups:
        box = layout.box()
        box.label(text=group.title, text_ctxt=schema.context)
        column = box.column()
        for keys in group.rows:
            # 单独占一行的项用对齐行, 与多列行保持相同的缩进
            row = column.row(align=True) if len(keys) == 1 else column.row()
            for key in keys:
                # 每个控件套一层子布局: 置灰从属项时不会连带置灰它的控制开关
                item = row.row(align=True)
                item.enabled = enabled(owner, key)
                _draw_field(item, owner, schema, key)
        for note in group.notes:
            box.label(text=note, text_ctxt=schema.context, icon="INFO")
