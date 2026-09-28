"""领域异常 - MeshFractureError 的消息按本工具的翻译上下文翻译"""

from ..common.errors import DomainError
from .constants import CONTEXT

# 源几何含非有限坐标时的消息, 求值快照与种子采样两处共用
NON_FINITE_MESSAGE = "Source geometry contains non-finite coordinates"


class MeshFractureError(DomainError):
    """网格破碎的可预期错误"""

    # 消息所属的翻译上下文
    context = CONTEXT


class IntersectionBoundsError(MeshFractureError):
    """布尔交集超出源物体包围盒 - 允许开启自交处理后重试一次"""
