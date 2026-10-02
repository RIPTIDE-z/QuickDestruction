"""关键帧写入 - 交接关键帧与烘焙关键帧; 一个资产共用一个动作, 每块碎块一个槽位

动作先赋给碎块, 再由 fcurve_ensure_for_datablock 建立曲线, 槽位随之自动建立并分配
交接的时序: 起动帧 T 保持当前位姿且受动画控制, T + 1 帧按速度移动一个模拟步, T + 2 帧交还模拟
"""

from mathutils import Quaternion, Vector

# 刚体受动画控制的开关, 交接靠它在动画与模拟之间切换
KINEMATIC_PATH = "rigid_body.kinematic"
# 关键帧插值的枚举值, 与 Blender 的 CONSTANT 与 LINEAR 一致
CONSTANT = 0
LINEAR = 1


def bind(shard, action, kinematic):
    """把动作赋给碎块并建立曲线, 返回 {(路径, 分量): 曲线}; kinematic 为假时不建开关曲线"""
    data = shard.animation_data or shard.animation_data_create()
    data.action = action
    paths = [("location", axis) for axis in range(3)]
    paths += [("rotation_quaternion", axis) for axis in range(4)]
    if kinematic:
        paths.append((KINEMATIC_PATH, 0))
    return {
        (path, axis): action.fcurve_ensure_for_datablock(shard, path, index=axis)
        for path, axis in paths
    }


def _key(curve, frame, value, interpolation):
    """插入或覆盖一个关键帧并设置插值"""
    point = curve.keyframe_points.insert(frame, value, options={"FAST"})
    point.interpolation = "CONSTANT" if interpolation == CONSTANT else "LINEAR"


def key_pose(curves, frame, location, rotation):
    """在 frame 帧记下位置与朝向"""
    for axis in range(3):
        _key(curves["location", axis], frame, location[axis], LINEAR)
    for axis in range(4):
        _key(curves["rotation_quaternion", axis], frame, rotation[axis], LINEAR)


def key_kinematic(curves, frame, value):
    """在 frame 帧记下刚体是否受动画控制, 布尔通道用常量插值"""
    _key(curves[KINEMATIC_PATH, 0], frame, 1.0 if value else 0.0, CONSTANT)


def turned(rotation, spin, dt):
    """当前朝向按世界坐标的轴角速度转过一个模拟步, 与原朝向保持同一符号"""
    vector = Vector(spin)
    angle = vector.length * dt
    if angle <= 0.0:
        return rotation.copy()
    result = Quaternion(vector.normalized(), angle) @ rotation
    return -result if result.dot(rotation) < 0.0 else result


def key_handoff(curves, frame, location, rotation, event, dt):
    """从 location 与 rotation 起交接: frame 帧保持, frame + 1 帧按速度移动一个模拟步, frame + 2 帧交还模拟"""
    key_kinematic(curves, frame, True)
    key_pose(curves, frame, location, rotation)
    moved = Vector(location) + Vector(event.velocity) * dt
    key_pose(curves, frame + 1, moved, turned(rotation, event.spin, dt))
    key_kinematic(curves, frame + 2, False)


def write_track(shard, action, frames, locations, rotations):
    """把逐帧采样写成一块碎块的烘焙曲线: 每条曲线一次批量写入坐标与插值"""
    data = shard.animation_data or shard.animation_data_create()
    data.action = action
    count = len(frames)
    channels = [("location", axis, locations) for axis in range(3)]
    channels += [("rotation_quaternion", axis, rotations) for axis in range(4)]
    for path, axis, values in channels:
        curve = action.fcurve_ensure_for_datablock(shard, path, index=axis)
        curve.keyframe_points.add(count)
        flat = []
        for frame, value in zip(frames, values, strict=True):
            flat.extend((frame, value[axis]))
        curve.keyframe_points.foreach_set("co", flat)
        curve.keyframe_points.foreach_set("interpolation", [LINEAR] * count)
        curve.update()
