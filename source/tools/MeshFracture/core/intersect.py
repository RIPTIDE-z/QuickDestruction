"""布尔求交 - 给胞体加相交修改器, 取求值结果替换胞体网格, 按设置整理并拆分孤岛

分两步: attach 挂上修改器, 调用方统一刷新一次视图层求值, collect 取回结果
一批胞体的布尔因此在同一次刷新里并行求值, 逐个刷新时每次都有固定开销
"""

import bpy

from ..settings import bakes_booleans, finishes_interior, splits_islands, uses_interior
from . import boolean, cleanup, islands, shard_objects


def _add_intersection(cell, obj, snapshot, hide_faces):
    """给胞体加布尔求交修改器; hide_faces 时把胞体面全部标记为隐藏, 返回修改器"""
    modifier = cell.modifiers.new(name="Boolean", type="BOOLEAN")
    modifier.object = obj
    boolean.configure_intersection(modifier, snapshot)
    if hide_faces:
        # 布尔结果里仍然隐藏的面来自胞体, 就是内部断面
        cell.data.polygons.foreach_set("hide", [True] * len(cell.data.polygons))
        cell.data.update()
    return modifier


def _bake_intersection(cell, modifier, snapshot, depsgraph, tracker):
    """取布尔求值结果替换胞体网格并移除修改器, 返回新网格; 旧网格登记为待删"""
    with boolean.validated_evaluation(cell, modifier, snapshot, depsgraph) as evaluated:
        # 直接复制依赖图里已求值的网格, 避免按全部数据层重新求值修改器栈
        mesh = tracker.track("meshes", bpy.data.meshes.new_from_object(evaluated))
    old_mesh = cell.data
    cell.data = mesh
    cell.modifiers.remove(modifier)
    if old_mesh.users == 0:
        tracker.bury(old_mesh)
    return mesh


def attach(obj, snapshot, cells, settings, batch):
    """给每个胞体挂上与被切物体求交的修改器, 返回修改器列表; 还需要刷新视图层才会求值"""
    if uses_interior(settings) and batch.first:
        # 首次切割先清掉源物体原有的面隐藏标记, 内部断面判定要从干净的起点开始
        obj.data.polygons.foreach_set("hide", [False] * len(obj.data.polygons))
        obj.data.update()
    hide_faces = finishes_interior(settings)
    return [_add_intersection(cell, obj, snapshot, hide_faces) for cell in cells]


def collect(snapshot, cells, modifiers, settings, batch, depsgraph):
    """取回求值结果得到贴合表面的碎块; 只做焊接, 清理与压平, 不合并面

    depsgraph 必须是刷新视图层之后取到的; 空交集的胞体被丢弃, 需要时再拆分孤岛
    """
    tracker = batch.naming.tracker
    bake = bakes_booleans(settings)
    results, multi = [], set()
    for cell, modifier in zip(cells, modifiers, strict=True):
        if bake:
            mesh = _bake_intersection(cell, modifier, snapshot, depsgraph, tracker)
            parts = cleanup.weld_intersection(mesh, snapshot.weld_distance)
            if not mesh.polygons:
                # 求交为空说明胞体落在源物体之外
                shard_objects.discard_object(tracker, cell)
                continue
            if parts > 1:
                multi.add(cell)
        else:
            # 保留修改器, 只校验求值结果是否越界
            with boolean.validated_evaluation(cell, modifier, snapshot, depsgraph):
                pass
        results.append(cell)
        if settings.use_debug_redraw:
            shard_objects.redraw()
    if results and splits_islands(settings):
        results = islands.split_islands(results, multi, tracker)
    return results
