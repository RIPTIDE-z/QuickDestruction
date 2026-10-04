"""动画换算 - 把碎块的世界轨迹换成骨架上每根骨骼的姿态关键帧

设 B 为骨骼的 matrix_local (纯平移), M0 为碎块的静止世界矩阵, Mt 为第 t 帧的世界矩阵
蒙皮后顶点的世界位置 = 骨骼姿态空间矩阵 · B⁻¹ · 静止顶点, 要让它等于 Mt · M0⁻¹ · 静止顶点, 姿态基矩阵就是
    basis = B⁻¹ · Mt · M0⁻¹ · B
位置取它的平移, 旋转取它的四元数并保持相邻帧同一符号; 只写会动的骨骼
"""

import bpy
from mathutils import Matrix

from ..constants import ACTION_PREFIX

# 线性插值在关键帧枚举里的值
LINEAR = 1


def _pose_path(bone_name, channel):
    """姿态骨骼某个变换通道的属性路径"""
    return f'pose.bones["{bone_name}"].{channel}'


def basis_samples(track, rest):
    """一块碎块每帧的姿态基矩阵分解结果 ([位置], [四元数]); rest 是它的静止世界矩阵"""
    head = Matrix.Translation(rest.translation)
    inverse_head = head.inverted()
    inverse_rest = rest.inverted()
    scale = rest.to_scale()
    locations, rotations = [], []
    for location, rotation in zip(track.locations, track.rotations, strict=True):
        world = Matrix.LocRotScale(location, rotation, scale)
        basis_location, basis_rotation, _scale = (
            inverse_head @ world @ inverse_rest @ head
        ).decompose()
        if rotations and basis_rotation.dot(rotations[-1]) < 0.0:
            basis_rotation = -basis_rotation
        locations.append(basis_location)
        rotations.append(basis_rotation)
    return locations, rotations


def _write_channel(action, armature, path, frames, values, size):
    """一个变换通道的全部分量曲线, 每条曲线一次批量写入线性关键帧"""
    for axis in range(size):
        curve = action.fcurve_ensure_for_datablock(armature, path, index=axis)
        curve.keyframe_points.add(len(frames))
        flat = []
        for frame, value in zip(frames, values, strict=True):
            flat.extend((frame, value[axis]))
        curve.keyframe_points.foreach_set("co", flat)
        curve.keyframe_points.foreach_set("interpolation", [LINEAR] * len(frames))
        curve.update()


def write_action(tracker, armature, animated):
    """为骨架建动作并写入; animated 是 [(骨骼名, 轨迹, 静止世界矩阵), ...]; 返回动作

    动作先赋给骨架再建曲线, 槽位随之自动建立并分配
    """
    action = tracker.track("actions", bpy.data.actions.new(ACTION_PREFIX + armature.name))
    armature.animation_data_create().action = action
    for bone_name, track, rest in animated:
        pose_bone = armature.pose.bones[bone_name]
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
    return action
