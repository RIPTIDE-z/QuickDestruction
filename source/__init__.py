"""QuickDestruction 扩展入口 - 只暴露 register 与 unregister, 首次调用时才导入装配模块"""


def register():
    """启用扩展时加载并注册各模块"""
    from . import registration

    registration.register()


def unregister():
    """停用扩展时按装配顺序的逆序注销"""
    from . import registration

    registration.unregister()
