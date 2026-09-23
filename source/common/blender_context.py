"""Blender 上下文适配 - BMesh 生命周期管理, 选择集与活动物体的保存和恢复"""

from contextlib import contextmanager

import bmesh


@contextmanager
def mesh_edit(mesh=None):
    """BMesh 编辑上下文, 保证任何路径下都会 free"""
    bm = bmesh.new()
    try:
        # mesh 为 None 时得到空 BMesh, 用于从零构建网格
        if mesh is not None:
            bm.from_mesh(mesh)
        yield bm
    finally:
        # 正常返回与抛错都要释放, 否则会泄漏 BMesh 占用的内存
        bm.free()


def restore_selection(context, selection, active):
    """恢复指定选择集与活动物体 - 跳过视图层中的空条目"""
    # 先刷新视图层, 让刚创建或删除的物体在选择集里已经生效
    context.view_layer.update()
    for obj in context.view_layer.objects:
        # 物体可能已被删除而残留空条目, 需要跳过
        if obj is not None and obj.select_get():
            obj.select_set(False)
    for obj in selection:
        obj.select_set(True)
    context.view_layer.objects.active = active


@contextmanager
def preserve_selection(context):
    """作用域结束时恢复原选择和活动物体"""
    # 复制快照: 作用域内的切割过程会反复改动选择集
    selection = list(context.selected_objects)
    active = context.view_layer.objects.active
    try:
        yield
    finally:
        restore_selection(context, selection, active)
