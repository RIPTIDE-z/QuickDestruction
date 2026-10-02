"""领域常量 - 工具身份与翻译上下文, 驱动物体所在的集合, 各角色的空物体外观与名字"""

from ...common import drivers
from ...framework.contract import tool_context

# 工具身份: 场景字段名, 预设目录与翻译上下文都由它派生
TOOL_ID = "drivers"
# 本工具的翻译上下文; 驱动参数规格写在 common 里, 用的是同一个上下文, 单元测试核对两者一致
CONTEXT = tool_context(TOOL_ID)

# 新建的驱动物体放进场景根集合下的这个集合
DRIVER_COLLECTION = "QD_Drivers"
# 各角色的空物体显示类型: 爆炸是球, 推力与弹道是单箭头, 旋涡是绕轴的圆环, 区域是立方体
# 显示出来的形状就是作用范围; 曲线方向没有空物体形态, 用曲线物体
DISPLAY_TYPES = {
    drivers.EXPLOSION: "SPHERE",
    drivers.PUSH: "SINGLE_ARROW",
    drivers.VORTEX: "CIRCLE",
    drivers.ZONE: "CUBE",
    drivers.PROJECTILE: "SINGLE_ARROW",
}
# 可以设在曲线物体上的角色: 曲线方向只能用曲线, 弹道空物体与曲线都行
CURVE_ROLES = (drivers.CURVE_FLOW, drivers.PROJECTILE)
# 新建驱动物体的名字, 属于数据名, 不随界面语言变化
OBJECT_NAMES = {
    drivers.EXPLOSION: "Explosion",
    drivers.PUSH: "Push",
    drivers.VORTEX: "Vortex",
    drivers.ZONE: "Zone",
    drivers.CURVE_FLOW: "CurveFlow",
    drivers.PROJECTILE: "Projectile",
}
# 各角色在界面上的图标
ROLE_ICONS = {
    drivers.EXPLOSION: "FORCE_FORCE",
    drivers.PUSH: "FORCE_WIND",
    drivers.VORTEX: "FORCE_VORTEX",
    drivers.ZONE: "MESH_CUBE",
    drivers.CURVE_FLOW: "FORCE_CURVE",
    drivers.PROJECTILE: "TRACKING_FORWARDS_SINGLE",
}
# 新建的曲线方向驱动的控制点, 曲线局部坐标: 沿 X 轴略微抬起的三点折线
CURVE_POINTS = ((-2.0, 0.0, 0.0), (0.0, 0.0, 1.0), (2.0, 0.0, 0.0))
# 计划预览依赖的模拟设置所在的工具标识; 只读它的起止帧与时间缩放, 让预览与烘焙用同样的时间换算
SIMULATION_ID = "simulation"
