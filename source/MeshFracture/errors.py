"""领域异常 - MeshFractureError 以及源几何非有限坐标共用的消息键"""

from ..common.errors import DomainError

# 源几何含非有限坐标时的消息键; 胞体构造与布尔诊断共用同一条
NON_FINITE_MESSAGE = "Source geometry contains non-finite coordinates"


class MeshFractureError(DomainError):
    """message 保存消息键 - values 保存翻译后填入的参数"""


class IntersectionBoundsError(MeshFractureError):
    """布尔交集超出操作数边界 - 允许使用保守求解路径重试"""
