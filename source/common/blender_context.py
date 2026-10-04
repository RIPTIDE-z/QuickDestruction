"""Blender 上下文适配 - BMesh 生命周期管理, 选择集与活动物体的保存和恢复"""

from contextlib import contextmanager

import bmesh


@contextmanager
def mesh_edit(mesh=None):
    """BMesh 编辑上下文, 保证任何路径下都会 free

    mesh 要读入 BMesh 的网格数据, 为 None 时得到空 BMesh
    产出 BMesh 对象; 这里不会把它写回网格, 需要写回的调用方自己调用 to_mesh
    """
    bm = bmesh.new()
    try:
        # mesh 为 None 时得到空 BMesh, 用于从零构建网格
        if mesh is not None:
            bm.from_mesh(mesh)
        yield bm
    finally:
        # 正常返回与抛错都要释放, 否则会泄漏 BMesh 占用的内存
        bm.free()


@contextmanager
def evaluated_mesh(obj, depsgraph):
    """求值后的临时网格, 离开作用域立即释放

    obj 要求值的物体
    depsgraph 求值用的依赖图
    产出 (求值物体, 网格), 没有几何的物体网格为 None
    """
    evaluated = obj.evaluated_get(depsgraph)
    try:
        yield evaluated, evaluated.to_mesh()
    finally:
        # 临时网格归求值物体所有, 必须成对清除否则会一直占着内存
        evaluated.to_mesh_clear()


def vertex_coordinates(mesh):
    """网格全部顶点坐标的平铺列表, 比逐个顶点读取快得多

    mesh 网格数据
    返回 [x0, y0, z0, x1, ...] 形式的浮点列表, 单位是网格局部坐标
    """
    flat = [0.0] * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", flat)
    return flat


def select_only(context, selection, active):
    """只选中指定物体并设置活动物体 - 只遍历已选中的物体, 不随场景物体总数变慢

    context Blender 上下文
    selection 要选中的物体序列
    active 要设为活动物体的物体, 可以为 None
    """
    # 先复制成列表: 取消选择会改变正在遍历的选中集合; 物体可能已被删除而残留空条目, 需要跳过
    for obj in list(context.view_layer.objects.selected):
        if obj is not None:
            obj.select_set(False)
    for obj in selection:
        obj.select_set(True)
    context.view_layer.objects.active = active


def restore_selection(context, selection, active):
    """刷新视图层后恢复选择集, 让刚创建或删除的物体在选择集里已经生效

    context Blender 上下文
    selection 要恢复选中的物体序列
    active 要恢复的活动物体
    """
    # 视图层不刷新时, 刚创建的物体还不在视图层里, 选择会静默失败
    context.view_layer.update()
    select_only(context, selection, active)


@contextmanager
def preserve_selection(context):
    """作用域结束时恢复原选择和活动物体

    context Blender 上下文
    """
    # 复制快照: 作用域内的切割过程会反复改动选择集
    selection = list(context.selected_objects)
    active = context.view_layer.objects.active
    try:
        yield
    finally:
        # 无论作用域内成功还是抛错都恢复, 不能让失败的操作改变用户的选择
        restore_selection(context, selection, active)
