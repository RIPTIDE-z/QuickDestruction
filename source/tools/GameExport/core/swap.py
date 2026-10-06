"""原物体切换 - 绑定方式为原物体切换时, 把源物体做成一段蒙皮网格, 并给出切换帧与各骨骼的缩放开关

起始帧显示完整的原物体, 碎块全部缩放为 0; 到切换帧原物体缩放为 0, 碎块缩放为 1 并开始破碎动画
缩放是骨骼上的常量插值关键帧: 原物体与不动碎块的骨骼头部在资产中心, 会动碎块的骨骼头部在各自的静止质心,
引擎在相邻两帧之间插值时每块都绕自己的中心缩放, 不会从世界原点飞过来
"""

import bpy
from mathutils import Vector

from ....common import asset
from ..errors import ExportError

# 绑定方式: 只导出碎块
SHARDS = "SHARDS"
# 绑定方式: 先显示原物体, 到切换帧换成碎块
SWAP = "SWAP"
# 原物体蒙皮网格归属的骨骼名
INTACT_BONE = "intact"
# 原物体切换时不动碎块归属的骨骼名: 它们也要在切换前隐藏, 所以不能像仅碎块那样归 root
STILL_BONE = "still"
# 原物体切换在 root 之外最多多出的骨骼数: 原物体骨骼与不动碎块骨骼
SWAP_BONES = 2

# 源物体已被删除时的错误
NO_SOURCE_MESSAGE = 'The source object of "{name}" was deleted; Swap from Intact needs it'


def source_object(root):
    """原物体切换要用的源物体

    root 资产根集合
    返回 源物体; 已被删除时抛 ExportError
    """
    source = asset.source_of(root)
    if source is None:
        raise ExportError(NO_SOURCE_MESSAGE, name=root.name)
    return source


def swap_frame(frame_start, frame_end, first_start):
    """切换帧: 第一块碎块离开静止姿态的那一帧, 至少比起始帧晚一帧, 保证起始帧显示原物体

    frame_start 模拟起始帧
    frame_end 模拟结束帧
    first_start 会动碎块里最早的起动帧; 没有碎块平移过时为 None, 此时在起始帧之后一帧切换
    返回 帧号; 帧范围只有一帧时就是起始帧
    """
    if frame_end <= frame_start:
        return frame_start
    frame = frame_start + 1 if first_start is None else max(first_start, frame_start + 1)
    return min(frame, frame_end)


def toggle_keys(frame_start, frame, visible_before):
    """一根骨骼的缩放开关关键帧

    frame_start 模拟起始帧
    frame 切换帧
    visible_before 为真时切换前缩放为 1, 切换后为 0 (原物体); 为假时相反 (碎块)
    返回 [(帧, 缩放), ...]; 切换帧不晚于起始帧时只有切换后的一帧
    """
    before, after = (1.0, 0.0) if visible_before else (0.0, 1.0)
    if frame <= frame_start:
        return [(frame_start, after)]
    return [(frame_start, before), (frame, after)]


def pivot(rests):
    """原物体与不动碎块骨骼的头部位置: 全部碎块静止质心的平均

    rests 碎块的静止世界矩阵序列
    返回 世界坐标; 没有碎块时为原点
    """
    if not rests:
        return Vector((0.0, 0.0, 0.0))
    return sum((rest.translation for rest in rests), Vector()) / len(rests)


def is_intact(obj):
    """判断蒙皮网格是不是原物体: 只有它带原物体骨骼的顶点组

    obj 导出集合里的网格物体
    返回 是原物体为真
    """
    return INTACT_BONE in obj.vertex_groups


def build_intact(context, tracker, name, collection, source):
    """把源物体求值后的网格复制到世界坐标, 全部顶点权重 1 归原物体骨骼

    骨架修改器与父级由调用方加上, 与碎块蒙皮网格一致
    context Blender 上下文
    tracker 创建账本
    name 网格物体与数据的名字
    collection 物体链接进的集合
    source 源物体
    返回 网格物体
    """
    depsgraph = context.evaluated_depsgraph_get()
    # 取求值后的结果: 源物体上的修改器 (倒角, 细分等) 与视口里看到的一致
    evaluated = source.evaluated_get(depsgraph)
    mesh = tracker.track(
        "meshes",
        bpy.data.meshes.new_from_object(
            evaluated, preserve_all_data_layers=True, depsgraph=depsgraph
        ),
    )
    mesh.name = name
    # 顶点烘进世界坐标, 物体保持单位变换, 与碎块蒙皮网格的约定一致
    mesh.transform(evaluated.matrix_world)
    obj = tracker.track("objects", bpy.data.objects.new(name, mesh))
    collection.objects.link(obj)
    group = obj.vertex_groups.new(name=INTACT_BONE)
    group.add(list(range(len(mesh.vertices))), 1.0, "REPLACE")
    return obj
