"""QuickDestruction 扩展入口 - 只暴露 register 与 unregister, 首次调用时才导入装配模块"""


def register():
    """启用扩展时加载并注册各模块"""
    # 延迟导入: 单元测试导入子模块时不会连带导入依赖 Blender 的装配模块
    from . import registration

    registration.register()


def unregister():
    """停用扩展时按注册的逆序注销"""
    from . import registration

    registration.unregister()
