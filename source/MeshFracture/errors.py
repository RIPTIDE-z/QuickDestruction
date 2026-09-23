"""领域异常 - MeshFractureError 只保存消息键与插值参数, 翻译交给界面层"""


class MeshFractureError(Exception):
    """message 保存消息键 - values 保存翻译后填入的参数"""

    def __init__(self, message, **values):
        """保存构造参数并初始化本实例状态"""
        # message 是英文消息键, 界面层先查译文再把 values 填进去
        super().__init__(message)
        self.message = message
        self.values = values
