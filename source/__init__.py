"""QuickDestruction 扩展入口 - 延迟加载注册装配"""


def register():
    """启用扩展时加载并注册各模块"""
    from . import registration
    registration.register()


def unregister():
    """停用扩展时按装配顺序的逆序注销"""
    from . import registration
    registration.unregister()
