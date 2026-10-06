"""领域异常 - RigError 的消息按本工具的翻译上下文翻译"""

from ...common.errors import DomainError
from .constants import CONTEXT


class RigError(DomainError):
    """绑定构建的可预期错误"""

    # 消息所属的翻译上下文
    context = CONTEXT
