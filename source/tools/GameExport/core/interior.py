"""断面 UV - 给切割新生成的断面做盒投影, 让引擎里能给断面单独铺材质

断面由碎块网格上的面属性标出; 网格已经在静止世界姿态上, 所以投影用世界坐标
每个断面按法线的主轴投到垂直于它的平面上, UV 按面角存储, 不影响外表面的 UV
"""

from ....common import shards

# 没有 UV 层时新建的 UV 层名
UV_NAME = "UVMap"


def interior_faces(mesh):
    """断面面属性为真的面序号列表; 网格没有这个属性时为 None"""
    attribute = mesh.attributes.get(shards.INTERIOR_ATTRIBUTE)
    if attribute is None or attribute.domain != "FACE":
        return None
    flags = [False] * len(mesh.polygons)
    attribute.data.foreach_get("value", flags)
    return [index for index, flag in enumerate(flags) if flag]


def _dominant_axis(normal):
    """法线绝对值最大的轴, 数值相同时取序号小的"""
    return max(range(3), key=lambda axis: (abs(normal[axis]), -axis))


def box_project(mesh, scale):
    """给断面做盒投影, 返回写入的面数; 没有断面属性或没有断面时返回 0

    没有 UV 层时先建一个, 外表面的 UV 保持 0; 已有 UV 层时只改断面的面角
    """
    faces = interior_faces(mesh)
    if not faces:
        return 0
    layer = mesh.uv_layers.active or mesh.uv_layers.new(name=UV_NAME)
    positions = [0.0] * (3 * len(mesh.vertices))
    mesh.vertices.foreach_get("co", positions)
    loop_vertex = [0] * len(mesh.loops)
    mesh.loops.foreach_get("vertex_index", loop_vertex)
    uvs = [0.0] * (2 * len(mesh.loops))
    layer.data.foreach_get("uv", uvs)
    for index in faces:
        polygon = mesh.polygons[index]
        # 投影轴取面法线的主轴, 另外两个坐标按顺序当作 U 与 V
        first, second = [axis for axis in range(3) if axis != _dominant_axis(polygon.normal)]
        for loop in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
            base = 3 * loop_vertex[loop]
            uvs[2 * loop] = positions[base + first] * scale
            uvs[2 * loop + 1] = positions[base + second] * scale
    layer.data.foreach_set("uv", uvs)
    return len(faces)
