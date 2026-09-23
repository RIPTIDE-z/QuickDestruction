"""RNA 工厂 - 按参数规格生成 Blender 属性定义, 让算子与属性组共用同一份规格"""


def property_definitions(specs, context):
    """生成 RNA 属性;上下文用于标签,数据名的值不经过翻译函数"""
    from bpy import props

    definitions = {}
    for key, (kind, options) in specs.items():
        definitions[key] = getattr(props, kind)(translation_context=context, **options)
    return definitions
