"""参数规格 - 破坏驱动没有场景参数; 驱动参数是挂在物体上的规格, 写在 common 的驱动契约里供模拟共用

OBJECT_SCHEMAS 列出本工具挂到其他数据块上的规格, 语言文件检查据此核对译文
"""

from ...common.drivers import DRIVER_SCHEMA

# 本工具挂到物体上的参数规格
OBJECT_SCHEMAS = (DRIVER_SCHEMA,)
