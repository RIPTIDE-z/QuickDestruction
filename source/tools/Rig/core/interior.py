"""断面 UV - 给切割新生成的断面做盒投影, 让引擎里能给断面单独铺材质

断面由碎块网格上的面属性标出; 网格已经在静止世界姿态上, 所以投影用世界坐标
每个断面按法线的主轴投到垂直于它的平面上, UV 按面角存储, 不影响外表面的 UV
"""

from ....common import shards

# 没有 UV 层时新建的 UV 层名
UV_NAME = "UVMap"


def interior_faces(mesh):
    """列出断面面属性为真的面

    mesh 网格数据
    返回 面序号列表; 网格没有这个属性时为 None
    """
    attribute = mesh.attributes.get(shards.INTERIOR_ATTRIBUTE)
    # 属性缺失或不在面域, 说明碎块不是由带断面标记的版本生成的
    if attribute is None or attribute.domain != "FACE":
        return None
    flags = [False] * len(mesh.polygons)
    attribute.data.foreach_get("value", flags)
    return [index for index, flag in enumerate(flags) if flag]


def _unwrapped(mesh):
    """已经有自己 UV 的断面的面序号集合, 例如按 UV 岛拆分时展开成长条的侧壁

    mesh 网格数据
    返回 面序号集合; 网格没有这个属性时为空
    """
    attribute = mesh.attributes.get(shards.INTERIOR_UV_ATTRIBUTE)
    if attribute is None or attribute.domain != "FACE":
        return set()
    flags = [False] * len(mesh.polygons)
    attribute.data.foreach_get("value", flags)
    return {index for index, flag in enumerate(flags) if flag}


def _dominant_axis(normal):
    """法线绝对值最大的轴, 数值相同时取序号小的

    normal 面法线
    返回 轴序号 0, 1 或 2
    """
    return max(range(3), key=lambda axis: (abs(normal[axis]), -axis))


def box_project(mesh, scale):
    """给断面做盒投影

    没有 UV 层时先建一个, 外表面取 Blender 新建 UV 层时的默认展开; 已有 UV 层时只改断面的面角
    已经展开过的断面 (带 INTERIOR_UV_ATTRIBUTE 标记) 保留原来的 UV, 不被盒投影覆盖
    mesh 网格数据, 顶点已在世界坐标
    scale 每单位距离对应的 UV 长度
    返回 写入的面数; 没有断面属性或没有需要投影的断面时为 0
    """
    faces = interior_faces(mesh)
    if faces:
        skip = _unwrapped(mesh)
        faces = [index for index in faces if index not in skip]
    # 没有断面标记或没有需要投影的断面: 不创建 UV 层, 不改动网格
    if not faces:
        return 0
    layer = mesh.uv_layers.active or mesh.uv_layers.new(name=UV_NAME)
    # 一次批量读出顶点, 面角与 UV, 逐面改写后一次写回
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
