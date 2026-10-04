"""轨迹与动静分类 - 从模拟成果的关键帧里读出每块碎块逐帧的位置与朝向, 按阈值分出会动的和不动的

模拟烘焙给动过的碎块逐帧写了 location 与 rotation_quaternion 的线性关键帧, 没动过的碎块不带动画
这里直接读关键帧坐标, 不逐帧求值场景, 读取的开销只与关键帧数成正比
"""

from dataclasses import dataclass

from bpy_extras import anim_utils
from mathutils import Quaternion, Vector

from ..constants import MOTION_START_EPSILON

# 读取的变换通道: 属性路径 -> 分量数
CHANNELS = {"location": 3, "rotation_quaternion": 4}


@dataclass
class Track:
    """一块碎块逐帧的世界位置与朝向, 帧号升序"""

    # 帧号
    frames: list
    # 每帧的位置
    locations: list
    # 每帧的朝向, 相邻帧之间保持同一符号
    rotations: list


def _read_curves(shard):
    """读出碎块动作里的变换曲线

    shard 碎块物体
    返回 {(路径, 分量): 平铺的 [帧, 值, 帧, 值, ...]}; 没有动画时为 None
    """
    data = shard.animation_data
    # 没有动画, 没有动作或没有动作槽位都算没有轨迹
    if data is None or data.action is None or data.action_slot is None:
        return None
    # 5.x 的动作按槽位分通道包, 要先取到本槽位的曲线集合
    bag = anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot)
    if bag is None:
        return None
    curves = {}
    for curve in bag.fcurves:
        # 只读位置与四元数旋转, 其余通道 (缩放, 自定义属性) 与导出无关
        if curve.data_path in CHANNELS:
            flat = [0.0] * (2 * len(curve.keyframe_points))
            curve.keyframe_points.foreach_get("co", flat)
            curves[curve.data_path, curve.array_index] = flat
    return curves


def read_track(shard):
    """读出碎块的轨迹

    shard 碎块物体
    返回 Track; 没有完整的位置与朝向曲线, 或各曲线长度不一致时为 None
    """
    curves = _read_curves(shard)
    if not curves:
        return None
    # 七条曲线 (位置三条, 四元数四条) 缺一不可
    wanted = [(path, axis) for path, size in CHANNELS.items() for axis in range(size)]
    if any(key not in curves for key in wanted):
        return None
    # 各曲线的关键帧数必须一致, 否则无法逐帧对齐
    count = len(curves["location", 0]) // 2
    if any(len(curves[key]) != 2 * count for key in wanted):
        return None
    # 平铺列表里偶数位是帧, 奇数位是值
    frames = [round(value) for value in curves["location", 0][0::2]]
    locations = [
        Vector([curves["location", axis][2 * index + 1] for axis in range(3)])
        for index in range(count)
    ]
    rotations = []
    for index in range(count):
        rotation = Quaternion(
            [curves["rotation_quaternion", axis][2 * index + 1] for axis in range(4)]
        )
        rotation.normalize()
        # q 与 -q 表示同一朝向; 相邻帧取同一符号, 插值才不会绕远路
        if rotations and rotation.dot(rotations[-1]) < 0.0:
            rotation = -rotation
        rotations.append(rotation)
    return Track(frames, locations, rotations)


def read_tracks(shards):
    """读出全部碎块的轨迹

    shards 碎块物体序列
    返回 {碎块名: Track 或 None}
    """
    return {shard.name: read_track(shard) for shard in shards}


def is_moving(track, rest, distance, angle):
    """判断碎块是否会动: 轨迹相对静止姿态的位移或转角超过阈值

    track 碎块的轨迹, 没有轨迹的碎块不动
    rest 碎块的静止世界矩阵
    distance 位移阈值, Blender 单位
    angle 转角阈值, 弧度
    返回 会动为真
    """
    if track is None:
        return False
    location, rotation, _scale = rest.decompose()
    # 任一帧超过阈值就算会动, 不必看完全部帧
    for position, orientation in zip(track.locations, track.rotations, strict=True):
        if (position - location).length > distance:
            return True
        if rotation.rotation_difference(orientation).angle > angle:
            return True
    return False


def motion_start(track, rest):
    """碎块从哪一帧开始离开静止姿态

    track 碎块的轨迹, 可以为 None
    rest 碎块的静止世界矩阵
    返回 帧号; 没有轨迹或始终没动时为 None
    """
    if track is None:
        return None
    location = rest.translation
    # 以位移为准: 只旋转而不平移的碎块没有起动帧, 但 is_moving 仍把它算作会动
    for frame, position in zip(track.frames, track.locations, strict=True):
        if (position - location).length > MOTION_START_EPSILON:
            return frame
    return None


def classify(shards, rests, tracks, distance, angle):
    """把碎块分成会动的与不动的

    shards 碎块物体序列
    rests 与 shards 同序的静止世界矩阵
    tracks {碎块名: Track 或 None}
    distance 位移阈值, Blender 单位
    angle 转角阈值, 弧度
    返回 (会动的序号列表, 不动的序号列表); 序号是碎块在 shards 里的位置
    """
    moving, still = [], []
    for index, shard in enumerate(shards):
        if is_moving(tracks[shard.name], rests[index], distance, angle):
            moving.append(index)
        else:
            still.append(index)
    return moving, still
