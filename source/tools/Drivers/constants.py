"""领域常量 - 工具身份与翻译上下文, 驱动物体所在的集合, 各角色的空物体外观与名字"""

from ...common import drivers
from ...framework.contract import tool_context

# 工具身份: 场景字段名, 预设目录与翻译上下文都由它派生
TOOL_ID = "drivers"
# 本工具的翻译上下文; 驱动参数规格写在 common 里, 用的是同一个上下文, 单元测试核对两者一致
CONTEXT = tool_context(TOOL_ID)

# 新建的驱动物体放进场景根集合下的这个集合
DRIVER_COLLECTION = "QD_Drivers"
# 各角色的空物体显示类型: 爆炸是球, 推力是单箭头, 显示出来的形状就是作用范围
DISPLAY_TYPES = {drivers.EXPLOSION: "SPHERE", drivers.PUSH: "SINGLE_ARROW"}
# 新建驱动物体的名字, 属于数据名, 不随界面语言变化
OBJECT_NAMES = {drivers.EXPLOSION: "Explosion", drivers.PUSH: "Push"}
# 各角色在界面上的图标
ROLE_ICONS = {drivers.EXPLOSION: "FORCE_FORCE", drivers.PUSH: "FORCE_WIND"}
