"""预切割领域异常 - 仅保存消息键和参数"""

class FractureError(Exception):
    """message 保存消息键 - values 保存翻译后填入的参数"""

    def __init__(self, message, **values):
        """保存构造参数并初始化本实例状态"""
        super().__init__(message)
        self.message = message
        self.values = values
