"""侧栏面板与参数对话框共用的参数布局。"""

import bpy
from bpy.types import Panel

from .qd_properties import CONTEXT, PROPERTY_SPECS
from .qd_presets import draw_presets
from .qd_settings import operator_keywords

# 面板排布的唯一数据源: 分组 -> 每行的参数标识, 行内顺序就是显示顺序。
#
# 之所以只保留这一份, 是因为以前同时存在"分组字段清单"和"分行方案"两份手写列表,
# 加一个参数要改两处, 漏改一处参数就会从界面上消失且不报错。
# 非紧凑排布需要的分组字段顺序由 _group_fields() 从这里推导, 不再单独维护。
#
# 分组按切割流程先后排列; 物理整组当前无任何代码读取, 因此放到末尾。
# use_remove_original 属于内部收尾阶段, 所以放在网格数据组,
# 它只在 recursion > 0 时有意义, 由置灰逻辑体现。
ROW_LAYOUT = (
    ("Point Source", (
        ('source',),
        ('source_limit', 'source_noise'),
        ('cell_scale',),
    )),
    ("Recursive Shatter", (
        # 递归开关单独成行, 这样它在递归为 0 时依然可点, 用户才有入口开启递归;
        # 下面两行在递归为 0 时会整行置灰
        ('recursion',),
        ('recursion_source_limit', 'recursion_clamp'),
        # 递归方式与参与比例同处一行; 枚举用普通 prop 绘制, 因此是下拉而不是
        # 之前那排横铺按钮 (横铺来自 prop(..., expand=True), 这里刻意不再使用)
        ('recursion_chance', 'recursion_chance_select'),
    )),
    ("Mesh Data", (
        ('use_smooth_faces', 'use_sharp_edges', 'use_sharp_edges_apply', 'use_data_match'),
        ('material_index', 'use_interior_vgroup'),
        ('margin', 'use_island_split'),
        ('use_remove_original',),
    )),
    ("Object", (
        ('use_recenter',),
    )),
    # 集合这一组只有两栏: 第 1 栏选输出集合, 第 2 栏是碎块前缀加分组勾选。
    # 序号补零位数属于命名格式细节, 放到调试组, 不占这里的版面
    ("Collection", (
        ('collection_name',),
        ('shard_prefix', 'use_shard_collections'),
    )),
    ("Debug", (
        ('use_debug_redraw', 'use_debug_points', 'use_debug_bool'),
        ('shard_index_padding',),
    )),
    ("Physics", (
        ('mass_mode', 'mass'),
    )),
)


def _group_fields(title):
    """按显示顺序取出一个分组包含的全部参数标识。"""
    for name, rows in ROW_LAYOUT:
        if name == title:
            return tuple(key for row in rows for key in row)
    return ()


def _compact_rows(title):
    """取一个分组的分行方案。

    分行只按"是否需要一起操作"决定, 不把置灰关系绑在行上:
    同一行内各项的启用状态仍然各自独立判定。
    """
    for name, rows in ROW_LAYOUT:
        if name == title:
            return rows
    return ()


# 调试布尔模式会跳过这些参数对应的处理阶段, 因此它们此时完全不起作用。
# 逐项实测结论 (use_debug_bool=True 时):
#   use_sharp_edges / use_sharp_edges_apply   内部收尾被整段跳过, 锐边不会标记
#   use_interior_vgroup                       同上, 不会创建"内部"顶点组
#   use_island_split                          孤岛拆分被跳过, 胞体保持未求交
# 反例: use_smooth_faces / margin / use_recenter / use_debug_points 在两种模式下都有效,
#       所以它们不能一并置灰 —— 这也是旧逻辑里最误导人的地方。
DEBUG_INACTIVE = frozenset({'use_sharp_edges', 'use_sharp_edges_apply',
                            'use_interior_vgroup', 'use_island_split'})
# 递归为 0 时无人读取的参数
RECURSION_DEPENDENT = frozenset({'recursion_source_limit', 'recursion_clamp',
                                 'recursion_chance', 'recursion_chance_select'})
# 本工具不创建刚体, 质量相关参数没有任何代码读取
PHYSICS_KEYS = frozenset({'mass_mode', 'mass'})


def _param_enabled(owner, key):
    """判断单个参数此刻是否会起作用, 置灰即表示改了也没有效果。

    依赖关系全部来自代码里的实际读取条件:
      递归系列        仅在 recursion > 0 时被 main_object 读取
      移除上级碎块    仅在 recursion > 0 时被读取 (与调试布尔无关)
      锐边拆分        仅在 use_sharp_edges 打开时才有对象可拆
      材质索引        仅在 use_data_match 打开时索引才与源物体槽位对应
      调试布尔        跳过内部收尾与孤岛拆分, 见 DEBUG_INACTIVE
      物理            没有任何代码读取, 见 PHYSICS_KEYS
    """
    if key in PHYSICS_KEYS:
        return False
    if key == 'use_sharp_edges_apply':
        # 拆分是锐边标记的后续动作, 没有标记就没有可拆的边;
        # 同时调试布尔会跳过整段内部收尾, 所以两个条件要同时成立。
        # 注意这里不能并进下面的 DEBUG_INACTIVE 分支, 那会覆盖掉对锐边的依赖
        return owner.use_sharp_edges and not owner.use_debug_bool
    if key in DEBUG_INACTIVE:
        return not owner.use_debug_bool
    if key in RECURSION_DEPENDENT or key == 'use_remove_original':
        return owner.recursion > 0
    if key == 'material_index':
        # 关掉匹配数据后, 布尔会自行带入刀刃物体的材质槽,
        # 碎块槽位与源物体槽位不再一一对应, 同一个索引会指到别的材质,
        # 越界时更是静默无效, 因此此时不提供调节
        return owner.use_data_match
    return True


def draw_parameters(layout, owner, compact=False):
    """绘制全部参数。

    compact 为真时使用侧栏那种横向紧凑排布, 与上游 Cell Fracture 一致;
    为假时使用纵向分栏排布, 每行一个参数 (保留给需要单列的场景)。
    """
    layout.use_property_split = not compact
    layout.use_property_decorate = False
    for title, _rows in ROW_LAYOUT:
        box = layout.box()
        box.label(text=title, text_ctxt=CONTEXT)
        if compact:
            _draw_compact_group(box, owner, title)
        else:
            for key in _group_fields(title):
                if key == 'source':
                    choices = box.grid_flow(columns=2, align=True)
                    choices.use_property_split = False
                    for identifier, name, _description in PROPERTY_SPECS[key][1]['items']:
                        choices.prop_enum(owner, key, identifier, text=name, text_ctxt=CONTEXT)
                    continue
                row = box.row()
                row.enabled = _param_enabled(owner, key)
                if key in COMPONENT_AXES:
                    # 单列排布下也按分量画, 保证属性名与轴名和紧凑排布一致
                    _draw_component_row(row, owner, key)
                    continue
                row.prop(owner, key)
        if title == "Physics":
            # 说明为什么这一组是灰的, 以及它为何还留着
            box.label(text="Reserved for Rigid Body; this tool never creates one",
                      text_ctxt=CONTEXT, icon='INFO')


# 数组型参数的分量轴名。Blender 原生向量的 X/Y/Z 标签是绘制时在 C 端生成的,
# Python 读不到 (内置的 object.scale 同样没有分量名), 所以这里自己画。
#
# 实测出来的标签规则 (三次试错都记录在此, 免得再犯):
#   prop(owner, key)                 -> Blender 自动用属性名当标签, 无轴名
#   prop(owner, key, text="X")       -> text 完全接管标签, 属性名被顶掉
#   prop(owner, key, text=" ")       -> 空格视为空文本, 标签被抹掉, 什么都不显示
# 而且 row.use_property_split = False 时不会自动画左侧标签列,
# 所以属性名必须自己用 label 画, 轴名则显式传给每个分量。
COMPONENT_AXES = {'cell_scale': ('X', 'Y', 'Z')}


def _draw_component_row(row, owner, key):
    """把数组参数按分量拆开画在同一行: 先是属性名, 然后是带轴名的三个分量。

    属性名与轴名都显式绘制, 不依赖 Blender 的自动标签 —— 见上面记录的规则。
    """
    row.use_property_split = False
    row.alignment = 'EXPAND'
    # 属性名占最左一格; 传翻译上下文, 中文界面下会显示为「胞体缩放」
    row.label(text=PROPERTY_SPECS[key][1]['name'], text_ctxt=CONTEXT)
    for index, axis in enumerate(COMPONENT_AXES[key]):
        if index:
            # 分量之间留一点间距, 三个数字框不至于糊成一片
            row.separator()
        row.prop(owner, key, index=index, text=axis, text_ctxt=CONTEXT)


def _draw_compact_group(box, owner, title):
    """按上游 Cell Fracture 的分组排布绘制单个分组, 每行尽量并排放多个参数。"""
    col = box.column()
    for row_spec in _compact_rows(title):
        # 单独占一行的项用对齐行, 便于和其它分组保持一致的缩进
        row = col.row(align=True) if len(row_spec) == 1 else col.row()
        # 整行统一置灰: 行内只要有一项无效, 同行其它项也一并变灰,
        # 断掉"开一个再开另一个"的路径, 也避免半灰半亮的怪排版。
        # 但行内每一项的判定都独立进行, 不把无关参数牵连进来
        # (例如 recursion 单独成行, 就不会被递归系列整体置灰连累)。
        if not all(_param_enabled(owner, key) for key in row_spec):
            row.enabled = False
        for key in row_spec:
            if key == 'source':
                row.use_property_split = False
                for identifier, name, _description in PROPERTY_SPECS[key][1]['items']:
                    row.prop_enum(owner, key, identifier, text=name, text_ctxt=CONTEXT)
                continue
            if key in COMPONENT_AXES:
                _draw_component_row(row, owner, key)
                continue
            # 普通 prop: 枚举因此显示为下拉菜单。
            # 不要传 expand=True, 那会把枚举横铺成一排按钮并挤掉同行其它控件。
            row.prop(owner, key)


def configure_operator(operator, owner):
    """把面板上的当前值写进算子, 使点击时立刻生效。"""
    for key, value in operator_keywords(owner).items():
        setattr(operator, key, value)


def can_apply(context):
    """侧栏「应用」此刻是否可用: 需要在物体模式下选中至少一个网格物体。

    只用于侧栏按钮的置灰, 不参与算子自身的 poll()。
    算子刻意不定义 poll(), 因为菜单入口要任何时候都可点; 两者需求不同,
    所以这里用 UI 层的 enabled 控制按钮, 而不是去改算子。
    """
    return (context.mode == 'OBJECT' and context.scene is not None
            and any(obj.type == 'MESH' for obj in context.selected_editable_objects))


class QD_PT_settings(Panel):
    bl_idname = "QD_PT_settings"
    bl_label = "Quick Destruction Effect"
    bl_translation_context = CONTEXT
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "QuickDestruction"

    def draw(self, context):
        layout = self.layout
        owner = context.scene.qd_settings.fracture
        draw_presets(layout, context, owner)
        draw_parameters(layout, owner, compact=True)
        # 应用按钮固定在全部参数之后, 也就是面板最底部。
        # 没有可切割的网格时直接置灰, 而不是让人按下去才看到报错;
        # 置灰用 UI 层的 enabled, 与算子的 poll() 无关 (菜单入口仍随时可点)。
        ready = can_apply(context)
        actions = layout.column(align=True)
        actions.enabled = ready
        actions.operator_context = 'EXEC_DEFAULT'
        apply = actions.operator("object.add_quick_destruction_objects", text="切割",
                                 text_ctxt=CONTEXT, icon='MOD_EXPLODE')
        # 传面板当前值, 不依赖算子默认值
        configure_operator(apply, owner)
        # 置灰时给出原因, 否则光看到一个灰按钮会不知道差什么
        if not ready:
            layout.label(text="Select editable mesh objects in Object Mode",
                         text_ctxt=CONTEXT, icon='INFO')
        # 这里不再放「打开对话框」按钮: 同一个对话框已经由
        # 物体 -> 快速效果 菜单提供, 面板里再放一个是多余的入口。
        # 两边参数互通 —— 对话框打开时复制当前参数, 确认后同步回面板。


def register():
    bpy.utils.register_class(QD_PT_settings)


def unregister():
    bpy.utils.unregister_class(QD_PT_settings)
