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
    """碎块动作里的变换曲线 {(路径, 分量): 平铺的 (帧, 值) 列表}; 没有动画时为 None"""
    data = shard.animation_data
    if data is None or data.action is None or data.action_slot is None:
        return None
    bag = anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot)
    if bag is None:
        return None
    curves = {}
    for curve in bag.fcurves:
        if curve.data_path in CHANNELS:
            flat = [0.0] * (2 * len(curve.keyframe_points))
            curve.keyframe_points.foreach_get("co", flat)
            curves[curve.data_path, curve.array_index] = flat
    return curves


def read_track(shard):
    """读出碎块的轨迹; 没有完整的位置与朝向曲线, 或各曲线长度不一致时为 None"""
    curves = _read_curves(shard)
    if not curves:
        return None
    wanted = [(path, axis) for path, size in CHANNELS.items() for axis in range(size)]
    if any(key not in curves for key in wanted):
        return None
    count = len(curves["location", 0]) // 2
    if any(len(curves[key]) != 2 * count for key in wanted):
        return None
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
        if rotations and rotation.dot(rotations[-1]) < 0.0:
            rotation = -rotation
        rotations.append(rotation)
    return Track(frames, locations, rotations)


def read_tracks(shards):
    """全部碎块的轨迹 {碎块名: Track 或 None}"""
    return {shard.name: read_track(shard) for shard in shards}


def is_moving(track, rest, distance, angle):
    """轨迹相对静止姿态的位移或转角超过阈值时算作会动; 没有轨迹的碎块不动"""
    if track is None:
        return False
    location, rotation, _scale = rest.decompose()
    for position, orientation in zip(track.locations, track.rotations, strict=True):
        if (position - location).length > distance:
            return True
        if rotation.rotation_difference(orientation).angle > angle:
            return True
    return False


def motion_start(track, rest):
    """碎块从哪一帧开始离开静止姿态; 没有轨迹或始终没动时为 None"""
    if track is None:
        return None
    location = rest.translation
    for frame, position in zip(track.frames, track.locations, strict=True):
        if (position - location).length > MOTION_START_EPSILON:
            return frame
    return None


def classify(shards, rests, tracks, distance, angle):
    """把碎块分成会动的与不动的, 返回两个序号列表; 序号是碎块在 shards 里的位置"""
    moving, still = [], []
    for index, shard in enumerate(shards):
        if is_moving(tracks[shard.name], rests[index], distance, angle):
            moving.append(index)
        else:
            still.append(index)
    return moving, still
