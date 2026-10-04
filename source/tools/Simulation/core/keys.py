"""关键帧写入 - 交接关键帧与烘焙关键帧; 一个资产共用一个动作, 每块碎块一个槽位

动作先赋给碎块, 再由 fcurve_ensure_for_datablock 建立曲线, 槽位随之自动建立并分配
交接的时序: 起动帧 T 保持当前位姿且受动画控制, T + 1 帧按速度移动一个模拟步, T + 2 帧交还模拟
"""

from mathutils import Quaternion, Vector

# 刚体受动画控制的开关, 交接靠它在动画与模拟之间切换
KINEMATIC_PATH = "rigid_body.kinematic"
# 关键帧插值的枚举值, 与 Blender 的 CONSTANT 一致
CONSTANT = 0
# 关键帧插值的枚举值, 与 Blender 的 LINEAR 一致
LINEAR = 1


def remember_animation(obj, log):
    """记下物体原来的动画引用, 失败时恢复; 原来没有动画数据时撤回就是清掉它

    obj 物体
    log 写前快照
    """
    data = obj.animation_data
    # 原来没有动画数据: 撤回就是把新建的清掉
    if data is None:
        log.add(obj.animation_data_clear)
        return
    action, slot = data.action, data.action_slot
    # 先登记槽位再登记动作: 撤回逆序执行, 动作先恢复, 槽位才能属于它
    log.add(lambda: setattr(data, "action_slot", slot) if slot is not None else None)
    log.add(lambda: setattr(data, "action", action))


def bind(shard, action, kinematic):
    """把动作赋给碎块并建立曲线

    shard 碎块物体
    action 要赋给它的动作
    kinematic 为假时不建受动画控制开关的曲线
    返回 {(路径, 分量): 曲线}
    """
    data = shard.animation_data or shard.animation_data_create()
    data.action = action
    # 位置三条, 四元数四条, 加上可选的开关曲线
    paths = [("location", axis) for axis in range(3)]
    paths += [("rotation_quaternion", axis) for axis in range(4)]
    if kinematic:
        paths.append((KINEMATIC_PATH, 0))
    return {
        (path, axis): action.fcurve_ensure_for_datablock(shard, path, index=axis)
        for path, axis in paths
    }


def _key(curve, frame, value, interpolation):
    """插入或覆盖一个关键帧并设置插值

    curve 动画曲线
    frame 帧号
    value 值
    interpolation CONSTANT 或 LINEAR
    """
    point = curve.keyframe_points.insert(frame, value, options={"FAST"})
    point.interpolation = "CONSTANT" if interpolation == CONSTANT else "LINEAR"


def key_pose(curves, frame, location, rotation):
    """在 frame 帧记下位置与朝向

    curves bind 返回的曲线字典
    frame 帧号
    location 位置
    rotation 朝向四元数
    """
    for axis in range(3):
        _key(curves["location", axis], frame, location[axis], LINEAR)
    for axis in range(4):
        _key(curves["rotation_quaternion", axis], frame, rotation[axis], LINEAR)


def key_kinematic(curves, frame, value):
    """在 frame 帧记下刚体是否受动画控制, 布尔通道用常量插值

    curves bind 返回的曲线字典, 必须带开关曲线
    frame 帧号
    value 为真表示受动画控制
    """
    _key(curves[KINEMATIC_PATH, 0], frame, 1.0 if value else 0.0, CONSTANT)


def turned(rotation, spin, dt):
    """当前朝向按世界坐标的轴角速度转过一个模拟步, 与原朝向保持同一符号

    rotation 当前朝向四元数
    spin 世界坐标的轴角向量, 弧度每模拟秒
    dt 每步经过的模拟秒
    返回 转动后的四元数
    """
    vector = Vector(spin)
    angle = vector.length * dt
    # 没有角速度时不转, 返回副本避免调用方改到原对象
    if angle <= 0.0:
        return rotation.copy()
    result = Quaternion(vector.normalized(), angle) @ rotation
    # q 与 -q 同一朝向; 与原朝向取同一符号, 插值才不会绕远路
    return -result if result.dot(rotation) < 0.0 else result


def motion(previous_location, previous_rotation, matrix, dt):
    """碎块当前的线速度与角速度: 当前位姿与上一帧位姿的差除以 dt, 角速度的约定与 turned 一致

    返回的是上一帧到这一帧的平均速度; 碎块在动画控制下没有动过时为零
    previous_location 上一帧位置
    previous_rotation 上一帧朝向四元数
    matrix 当前世界矩阵
    dt 一帧经过的模拟秒
    返回 (线速度, 角速度)
    """
    location, rotation, _scale = matrix.decompose()
    velocity = (location - previous_location) / dt
    delta = rotation @ previous_rotation.inverted()
    if delta.w < 0.0:
        # 取较短的那条转动路径
        delta = -delta
    axis, angle = delta.to_axis_angle()
    return velocity, axis * (angle / dt)


def key_handoff(curves, frame, location, rotation, event, dt):
    """从 location 与 rotation 起交接

    frame 帧保持, frame + 1 帧按速度移动一个模拟步, frame + 2 帧交还模拟

    frame + 1 帧也显式写为动画控制: 上一次交接的释放恰好落在这一帧时, 要把它盖掉
    否则第二次交接的位移被丢弃
    curves bind 返回的曲线字典, 必须带开关曲线
    frame 起动帧
    location 起动帧的位置
    rotation 起动帧的朝向
    event 起动事件, 提供线速度与角速度
    dt 一帧经过的模拟秒
    """
    key_kinematic(curves, frame, True)
    key_kinematic(curves, frame + 1, True)
    key_pose(curves, frame, location, rotation)
    moved = Vector(location) + Vector(event.velocity) * dt
    key_pose(curves, frame + 1, moved, turned(rotation, event.spin, dt))
    key_kinematic(curves, frame + 2, False)


def write_track(shard, action, frames, locations, rotations):
    """把逐帧采样写成一块碎块的烘焙曲线: 每条曲线一次批量写入坐标与插值

    shard 碎块物体
    action 成果动作
    frames 帧号列表
    locations 每帧的位置
    rotations 每帧的朝向四元数
    """
    data = shard.animation_data or shard.animation_data_create()
    data.action = action
    count = len(frames)
    channels = [("location", axis, locations) for axis in range(3)]
    channels += [("rotation_quaternion", axis, rotations) for axis in range(4)]
    for path, axis, values in channels:
        curve = action.fcurve_ensure_for_datablock(shard, path, index=axis)
        curve.keyframe_points.add(count)
        # 平铺成 [帧, 值, 帧, 值, ...] 一次写入, 比逐个插入关键帧快得多
        flat = []
        for frame, value in zip(frames, values, strict=True):
            flat.extend((frame, value[axis]))
        curve.keyframe_points.foreach_set("co", flat)
        curve.keyframe_points.foreach_set("interpolation", [LINEAR] * count)
        curve.update()
