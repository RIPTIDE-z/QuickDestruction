"""弹体飞行动画 - 弹道驱动的子物体沿路径飞行, 到达打穿处之后的路径端点, 或停在嵌入点

子物体沿驱动的路径运动: 发射帧之前停在原位, 之后按弹速逐个路径折点写线性关键帧, 停下后保持
动画写在每个驱动一个的动作里, 子物体的原位置记在自定义属性上, 复位时据此放回
"""

import bpy
from mathutils import Vector

from ....common import asset, ballistics, drivers
from .keys import remember_animation

# 飞行动作的阶段标记值, 写在动作的阶段标记上, 替换旧动作前按它核对归属
FLIGHT_STAGE = "flight"
# 飞行动作的名字前缀, 后接驱动名
FLIGHT_PREFIX = "QD_Flight_"


def _end_arc(flights, total):
    """子弹在各资产里最远飞到的累计长度: 在某个资产里停下的取停点, 没停的飞完整条路径

    flights 各资产对这发子弹的 Flight 计划
    total 整条路径的长度
    返回 累计长度
    """
    return max((total if flight.stop_arc is None else flight.stop_arc) for flight in flights)


def _keys(driver, end_arc, dt, frame_start):
    """计算飞行的关键帧

    起始帧与发射帧都在起点; 之后路径折点按到达时刻写, 最后一帧在终点
    driver 弹道驱动快照
    end_arc 子弹飞到的累计长度
    dt 一帧经过的模拟秒
    frame_start 模拟起始帧
    返回 ([(帧, 世界位置)], 起点), 帧升序且不重复
    """
    lengths = ballistics.path_lengths(driver.path)
    start = Vector(driver.path[0])
    # 以帧为键的字典: 同一帧的点自然去重, 后写的覆盖先写的
    points = {frame_start: start, driver.frame: start}
    # 只写子弹终点之前的路径折点
    for arc in (value for value in lengths[1:-1] if value < end_arc):
        point, _direction = ballistics.point_at(driver.path, lengths, arc)
        points[ballistics.arrival_frame(driver, arc, dt)] = Vector(point)
    point, _direction = ballistics.point_at(driver.path, lengths, end_arc)
    points[ballistics.arrival_frame(driver, end_arc, dt)] = Vector(point)
    return sorted(points.items()), start


def _local_delta(child, delta):
    """世界位移换算成子物体 location 的位移; 考虑父物体的变换与父逆矩阵

    child 子物体
    delta 世界坐标的位移向量
    返回 子物体局部 location 的位移向量
    """
    frame = child.parent.matrix_world @ child.matrix_parent_inverse
    return frame.to_3x3().inverted_safe() @ delta


def _animate_child(child, action, keys, start, result):
    """把一个子物体绑定到飞行动作, 写 location 的线性关键帧

    child 弹体子物体
    action 飞行动作
    keys [(帧, 世界位置)]
    start 路径起点, 位移从它起算
    result 写前快照, 失败时撤回
    """
    remember_animation(child, result)
    # 第一次写入时记下原 location, 复位时放回; 已记录过的不重复记录
    if asset.flight_rest(child) is None:
        asset.set_flight_rest(child, child.location)
        result.add(lambda: asset.clear_flight_rest(child))
    base = Vector(asset.flight_rest(child))
    data = child.animation_data or child.animation_data_create()
    data.action = action
    for axis in range(3):
        curve = action.fcurve_ensure_for_datablock(child, "location", index=axis)
        for frame, point in keys:
            # 值 = 原 location + 世界位移换算到子物体局部空间的分量
            value = base[axis] + _local_delta(child, point - start)[axis]
            key = curve.keyframe_points.insert(frame, value, options={"FAST"})
            key.interpolation = "LINEAR"
        curve.update()


def write(session, tracker, result):
    """为有子物体的弹道驱动写飞行动画; 失败时由写前快照与创建账本撤回

    session 模拟会话, 提供驱动快照, 各弹道的飞行计划与时间设置
    tracker 创建账本
    result 写前快照
    返回 新建的动作列表
    """
    created = []
    for driver in session.snapshots:
        # 只处理参与了这次会话计划的弹道驱动
        if driver.role != drivers.PROJECTILE or driver.name not in session.flights:
            continue
        obj = bpy.data.objects.get(driver.name)
        # 没有子物体 (弹体模型) 或路径不足两点时没有东西可动
        if obj is None or not obj.children or len(driver.path) < 2:
            continue
        total = ballistics.path_lengths(driver.path)[-1]
        end_arc = _end_arc(session.flights[driver.name], total)
        keys, start = _keys(driver, end_arc, session.dt, session.frame_start)
        action = tracker.track("actions", bpy.data.actions.new(FLIGHT_PREFIX + driver.name))
        action[asset.STAGE_MARK] = FLIGHT_STAGE
        for child in obj.children:
            _animate_child(child, action, keys, start, result)
        created.append(action)
    return created


def stale(created):
    """已有的飞行动作里这次没有重建的: 子物体已改用新动作, 提交的最后删除它们

    created 本次新建的动作列表
    返回 过期的动作列表
    """
    return [
        action
        for action in bpy.data.actions
        if action.get(asset.STAGE_MARK) == FLIGHT_STAGE and action not in created
    ]


def clear():
    """删除全部飞行动作, 子物体放回原位置; 复位时在没有任何模拟成果之后调用"""
    for obj in asset.flight_rest_objects():
        data = obj.animation_data
        # 只摘本插件的飞行动作, 子物体上用户自己的动作保留
        if data is not None and data.action is not None:
            if data.action.get(asset.STAGE_MARK) == FLIGHT_STAGE:
                data.action = None
        obj.location = asset.flight_rest(obj)
        asset.clear_flight_rest(obj)
    # 没有重建任何动作, 所以全部飞行动作都是过期的
    for action in stale([]):
        bpy.data.actions.remove(action)
