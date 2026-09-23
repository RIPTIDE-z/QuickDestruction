"""通用字段校验异常 - InvalidParameterError 携带出错字段名, 交给界面层翻译"""


class InvalidParameterError(ValueError):
    """保留 ValueError 兼容性,同时明确指出出错字段"""

    def __init__(self, field):
        """保存构造参数并初始化本实例状态"""
        super().__init__(field)
        self.field = field
