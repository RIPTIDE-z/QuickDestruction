"""动画换算 - 把碎块的世界轨迹换成骨架上每根骨骼的姿态关键帧

设 B 为骨骼的 matrix_local (纯平移), M0 为碎块的静止世界矩阵, Mt 为第 t 帧的世界矩阵
蒙皮后顶点的世界位置 = 骨骼姿态空间矩阵 · B⁻¹ · 静止顶点, 要让它等于 Mt · M0⁻¹ · 静止顶点,
姿态基矩阵就是
    basis = B⁻¹ · Mt · M0⁻¹ · B
位置取它的平移, 旋转取它的四元数并保持相邻帧同一符号; 只写会动的骨骼
原物体切换的缩放开关另写成常量插值的 scale 关键帧, 不参与上面的换算
"""

import bpy
from mathutils import Matrix

from ..constants import ACTION_PREFIX

# 常量插值在关键帧枚举里的值: 保持到下一个关键帧再跳变
CONSTANT = 0
# 线性插值在关键帧枚举里的值
LINEAR = 1


def _pose_path(bone_name, channel):
    """姿态骨骼某个变换通道的属性路径

    bone_name 骨骼名
    channel 通道名, location 或 rotation_quaternion
    返回 动画曲线的 data_path
    """
    return f'pose.bones["{bone_name}"].{channel}'


def basis_samples(track, rest):
    """求一块碎块每帧的姿态基矩阵分解结果

    track 碎块的轨迹
    rest 碎块的静止世界矩阵
    返回 ([位置], [四元数]), 与轨迹的帧一一对应, 四元数相邻帧保持同一符号
    """
    # B 是骨骼的 matrix_local: 头部在静止质心的纯平移
    head = Matrix.Translation(rest.translation)
    inverse_head = head.inverted()
    inverse_rest = rest.inverted()
    scale = rest.to_scale()
    locations, rotations = [], []
    for location, rotation in zip(track.locations, track.rotations, strict=True):
        # 第 t 帧的世界矩阵: 轨迹给出位置与朝向, 缩放沿用静止姿态
        world = Matrix.LocRotScale(location, rotation, scale)
        basis_location, basis_rotation, _scale = (
            inverse_head @ world @ inverse_rest @ head
        ).decompose()
        # q 与 -q 同一朝向; 相邻帧取同一符号, 线性插值才不会绕远路
        if rotations and basis_rotation.dot(rotations[-1]) < 0.0:
            basis_rotation = -basis_rotation
        locations.append(basis_location)
        rotations.append(basis_rotation)
    return locations, rotations


def _write_channel(action, armature, path, frames, values, size, interpolation=LINEAR):
    """写一个变换通道的全部分量曲线, 每条曲线一次批量写入关键帧

    action 动作
    armature 骨架物体, 动作已赋给它
    path 通道的 data_path
    frames 帧号列表
    values 每帧的值, 位置或四元数
    size 分量数, 位置与缩放为 3, 四元数为 4
    interpolation 关键帧插值的枚举值
    """
    for axis in range(size):
        curve = action.fcurve_ensure_for_datablock(armature, path, index=axis)
        curve.keyframe_points.add(len(frames))
        # 平铺成 [帧, 值, 帧, 值, ...] 一次写入, 比逐个插入关键帧快得多
        flat = []
        for frame, value in zip(frames, values, strict=True):
            flat.extend((frame, value[axis]))
        curve.keyframe_points.foreach_set("co", flat)
        curve.keyframe_points.foreach_set("interpolation", [interpolation] * len(frames))
        curve.update()


def write_action(tracker, armature, animated, toggles=()):
    """为骨架建动作并写入

    动作先赋给骨架再建曲线, 槽位随之自动建立并分配
    tracker 创建账本
    armature 骨架物体
    animated [(骨骼名, 轨迹, 静止世界矩阵), ...], 只含会动的碎块
    toggles [(骨骼名, [(帧, 缩放), ...]), ...], 原物体切换的缩放开关; 仅碎块时为空
    返回 动作
    """
    action = tracker.track("actions", bpy.data.actions.new(ACTION_PREFIX + armature.name))
    armature.animation_data_create().action = action
    for bone_name, track, rest in animated:
        pose_bone = armature.pose.bones[bone_name]
        # 四元数旋转才有 rotation_quaternion 通道可写
        pose_bone.rotation_mode = "QUATERNION"
        locations, rotations = basis_samples(track, rest)
        _write_channel(
            action, armature, _pose_path(bone_name, "location"), track.frames, locations, 3
        )
        _write_channel(
            action,
            armature,
            _pose_path(bone_name, "rotation_quaternion"),
            track.frames,
            rotations,
            4,
        )
    # 缩放开关用常量插值: 在 Blender 里切换帧之前保持原值, 到切换帧才跳变
    for bone_name, keys in toggles:
        frames = [frame for frame, _scale in keys]
        scales = [(scale, scale, scale) for _frame, scale in keys]
        path = _pose_path(bone_name, "scale")
        _write_channel(action, armature, path, frames, scales, 3, CONSTANT)
    return action
