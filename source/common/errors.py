"""通用字段校验异常 - InvalidParameterError 携带出错字段名, 交给界面层翻译"""


class InvalidParameterError(ValueError):
    """保留 ValueError 兼容性,同时明确指出出错字段"""

    def __init__(self, field):
        """记录出错字段名, 同时保留 ValueError 的字符串内容"""
        # 继承 ValueError, 让既有的 except ValueError 分支继续生效
        super().__init__(field)
        # field 是规格里的字段名, 界面层据此取出参数标签再展示
        self.field = field
