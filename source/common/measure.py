"""包围盒测量 - 一组世界空间点的轴对齐包围盒与体对角线长度"""

from mathutils import Vector


def bounds(points):
    """一组点的轴对齐包围盒 - 返回 (最小角点, 最大角点) 两个三元组"""
    # 逐轴取极值: 只在三轴上各走一遍, 因此调用方只需调用一次本函数
    minimum = tuple(min(point[i] for point in points) for i in range(3))
    maximum = tuple(max(point[i] for point in points) for i in range(3))
    return minimum, maximum


def diagonal(minimum, maximum):
    """包围盒的体对角线长度 - 交由 mathutils 计算, 与 Blender 的浮点精度一致

    尺度同时决定焊接距离与噪波位移, 因此这里不用初等函数重算一遍,
    否则末位精度差异会传到几何结果上
    """
    return Vector(tuple(maximum[i] - minimum[i] for i in range(3))).length


def points_diagonal(points):
    """一组点包围盒的体对角线长度"""
    return diagonal(*bounds(points))
