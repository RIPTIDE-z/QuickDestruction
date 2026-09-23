"""RNA 工厂 - 按参数规格生成 Blender 属性定义, 让算子与属性组共用同一份规格"""


def property_definitions(specs, context):
    """生成 RNA 属性;上下文用于标签,数据名的值不经过翻译函数"""
    # 延迟导入: 本模块在 Blender 之外也能被导入, 便于规格相关的静态检查
    from bpy import props

    definitions = {}
    for key, (kind, options) in specs.items():
        # 规格里的类型名直接对应 bpy.props 的构造函数, 规格即属性定义
        # translation_context 由调用方提供, 标签与提示交给 Blender 自动翻译
        definitions[key] = getattr(props, kind)(translation_context=context, **options)
    return definitions
