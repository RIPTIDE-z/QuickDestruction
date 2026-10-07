"""原物体切换 - 绑定方式为原物体切换时, 把源物体做成一段蒙皮网格, 并给出各骨骼的缩放开关

每个骨架有两个动作: 待机与破碎, 游戏导出分别写成两个文件
待机: 从起始帧起持续若干帧的静止循环, 只显示原物体, 碎块都是隐藏缩放, 会动碎块的骨骼停在静止姿态
破碎: 原物体从隐藏帧起缩到隐藏缩放; 碎块在出现帧之前是隐藏缩放, 出现帧起恢复原尺寸并播放破碎动画
两个帧分开设置: 可以让两者重叠一段, 也可以中间空一段; 默认都是第 0 帧, 即破碎动画一开始就只有碎块
两个动作给同一批骨骼的同一批通道打关键帧: 导出时未打关键帧的通道会沿用上一个动作留下的姿态
隐藏缩放默认 0.001 而不是 0: 部分引擎 (虚幻) 不接受零缩放的骨骼
缩放是骨骼上的常量插值关键帧: 原物体与不动碎块的骨骼头部在资产中心, 会动碎块的在各自的静止质心,
引擎在相邻两帧之间插值时每块都绕自己的中心缩放, 不会从世界原点飞过来
"""

import bpy
from mathutils import Vector

from ....common import asset
from ..errors import RigError
from .tracks import Track

# 原物体蒙皮网格归属的骨骼名
INTACT_BONE = "origin"
# 原物体切换时不动碎块归属的骨骼名: 它们也要在切换前隐藏, 所以不能像仅碎块那样归 root
STILL_BONE = "still"
# 原物体切换在 root 之外最多多出的骨骼数: 原物体骨骼与不动碎块骨骼
SWAP_BONES = 2

# 源物体已被删除时的错误
NO_SOURCE_MESSAGE = 'The source object of "{name}" was deleted; Swap from Intact needs it'


def source_object(root):
    """原物体切换要用的源物体

    root 资产根集合
    返回 源物体; 已被删除时抛 RigError
    """
    source = asset.source_of(root)
    if source is None:
        raise RigError(NO_SOURCE_MESSAGE, name=root.name)
    return source


def toggle_keys(frame_start, frame, visible_before, hidden):
    """一根骨骼的缩放开关关键帧

    frame_start 模拟起始帧
    frame 开关帧: 原物体的隐藏帧或碎块的出现帧
    visible_before 为真时开关前缩放为 1, 开关后为隐藏缩放 (原物体); 为假时相反 (碎块)
    hidden 隐藏时的缩放
    返回 [(帧, 缩放), ...]; 开关帧不晚于起始帧时只有开关后的一帧
    """
    before, after = (1.0, hidden) if visible_before else (hidden, 1.0)
    if frame <= frame_start:
        return [(frame_start, after)]
    return [(frame_start, before), (frame, after)]


def idle_keys(frame_start, length, visible, hidden):
    """待机动作里一根骨骼的缩放: 起止两帧同一个值

    frame_start 模拟起始帧, 待机动作从它开始
    length 待机长度, 帧
    visible 为真时缩放为 1 (原物体), 否则为隐藏缩放 (碎块)
    hidden 隐藏时的缩放
    返回 [(帧, 缩放), (帧, 缩放)]
    """
    scale = 1.0 if visible else hidden
    return [(frame_start, scale), (frame_start + length, scale)]


def idle_track(rest, frame_start, length):
    """待机动作里会动碎块的轨迹: 起止两帧都在静止姿态

    rest 碎块的静止世界矩阵
    frame_start 模拟起始帧
    length 待机长度, 帧
    返回 Track
    """
    location, rotation, _scale = rest.decompose()
    frames = [frame_start, frame_start + length]
    return Track(frames, [location, location.copy()], [rotation, rotation.copy()])


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
