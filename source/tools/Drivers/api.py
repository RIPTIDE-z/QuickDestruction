"""阶段入口 - 新建驱动物体, 或把已有的物体设为驱动; 计划预览与受影响的碎块

参数经驱动规格校验后写入; 计划预览与模拟烘焙用同一个编译函数, 所以看到的初始条件就是实际使用的
"""

import bpy

from ...common import asset, drivers, impulse, planning, rigidbody, shards
from .constants import (
    CURVE_POINTS,
    CURVE_ROLES,
    DISPLAY_TYPES,
    DRIVER_COLLECTION,
    OBJECT_NAMES,
    SIMULATION_ID,
)
from .errors import DriverError


def driver_collection(scene):
    """取场景里放驱动物体的集合, 没有时新建并链接到场景根集合

    scene 场景
    返回 驱动集合
    """
    collection = bpy.data.collections.get(DRIVER_COLLECTION)
    if collection is None:
        collection = bpy.data.collections.new(DRIVER_COLLECTION)
    # 集合可能存在但没链接进当前场景 (例如换了场景), 补链接
    if collection.name not in scene.collection.children:
        scene.collection.children.link(collection)
    return collection


def default_frame(scene):
    """新驱动的起动帧: 当前帧, 但不早于场景起始帧之后的第一帧

    scene 场景
    返回 帧号
    """
    return max(scene.frame_start + 1, scene.frame_current)


def _new_object(role):
    """新建角色对应的物体: 曲线方向是一条三点折线, 其余是显示类型合适的空物体

    role 驱动角色
    返回 新建的物体, 还没有链接进任何集合
    """
    name = OBJECT_NAMES[role]
    # 曲线方向要曲线物体: 一条三点的多段线, 用户再拖动控制点
    if role == drivers.CURVE_FLOW:
        data = bpy.data.curves.new(name, "CURVE")
        data.dimensions = "3D"
        spline = data.splines.new("POLY")
        spline.points.add(len(CURVE_POINTS) - 1)
        for point, co in zip(spline.points, CURVE_POINTS, strict=True):
            point.co = (*co, 1.0)
        return bpy.data.objects.new(name, data)
    # 其余角色是空物体, 显示类型 (球, 箭头, 圆环, 立方体) 就是作用范围的形状
    obj = bpy.data.objects.new(name, None)
    obj.empty_display_type = DISPLAY_TYPES[role]
    obj.empty_display_size = 1.0
    return obj


def add_driver(context, role, *, location=None, **params):
    """在 location 处新建一个驱动物体, 默认放在 3D 游标处; params 是驱动参数, 经规格校验后写入

    参数不合法时新建的物体与曲线数据一并删除, 不留下半成品
    context Blender 上下文
    role 驱动角色
    location 放置的位置, 缺省取 3D 游标
    params 驱动参数, 缺省的字段取规格默认值, 起动帧缺省取 default_frame
    返回 新建的驱动物体
    """
    if role not in OBJECT_NAMES:
        raise DriverError("Unknown driver type")
    scene = context.scene
    obj = _new_object(role)
    obj.location = scene.cursor.location if location is None else location
    driver_collection(scene).objects.link(obj)
    values = {"role": role, "frame": default_frame(scene), **params}
    try:
        drivers.DRIVER_SCHEMA.write(getattr(obj, drivers.ATTRIBUTE), values)
    except Exception:
        # 参数不合法: 删除刚建的物体与曲线数据, 再把原异常抛出
        data = obj.data
        bpy.data.objects.remove(obj)
        if data is not None:
            bpy.data.curves.remove(data)
        raise
    return obj


def _check_target(obj, role):
    """物体的类型要与角色匹配: 曲线方向只能用曲线, 弹道空物体与曲线都行, 其余只能用空物体

    obj 要设为驱动的物体, 可以为 None
    role 驱动角色
    """
    if role not in OBJECT_NAMES:
        raise DriverError("Unknown driver type")
    if role == drivers.CURVE_FLOW and (obj is None or obj.type != "CURVE"):
        raise DriverError("Select a curve to use as a driver of this type")
    if role == drivers.PROJECTILE and (obj is None or obj.type not in ("EMPTY", "CURVE")):
        raise DriverError("Select an empty or a curve to use as a projectile")
    # 其余角色 (爆炸, 推力, 旋涡, 区域) 只能用空物体
    if role not in CURVE_ROLES and (obj is None or obj.type != "EMPTY"):
        raise DriverError("Select an empty to use as a driver")


def make_driver(obj, role):
    """把已有的物体设为驱动, 空物体的显示类型改成与角色一致, 其余参数保持不变

    obj 要设为驱动的物体
    role 驱动角色
    返回 obj 本身
    """
    _check_target(obj, role)
    # 空物体的显示类型决定作用范围的形状, 要与角色一致
    if obj.type == "EMPTY":
        obj.empty_display_type = DISPLAY_TYPES[role]
    drivers.DRIVER_SCHEMA.write(getattr(obj, drivers.ATTRIBUTE), {"role": role})
    return obj


def _timing(context):
    """预览用的时间换算: 帧范围与时间缩放取模拟烘焙的设置, 与烘焙一致; 模拟设置不存在时取场景值

    context Blender 上下文
    返回 impulse.Timing
    """
    scene = context.scene
    sim = getattr(scene.qd_settings, SIMULATION_ID, None)
    start = scene.frame_start if sim is None or sim.use_scene_range else sim.frame_start
    scale = 1.0 if sim is None else sim.time_scale
    return impulse.Timing(frame_start=start, dt=rigidbody.frame_seconds(scene, scale))


def compile_assets(context, roots, refresh=True):
    """用场景里启用的驱动编译各资产的计划; 资产还没有应用物理时报错

    context Blender 上下文
    roots 资产根集合序列
    refresh 为真时先刷新视图层, 脚本改过的变换才是最新的; 视口绘制回调里不能刷新, 传 False
    返回 [AssetPlan]
    """
    asset.validate(roots)
    for root in roots:
        if asset.physics_record(root) is None:
            raise DriverError('Apply physics to "{name}" first', name=root.name)
    if refresh:
        context.view_layer.update()
    snapshots = [drivers.snapshot(obj) for obj in drivers.driver_objects(context.scene)]
    depsgraph = context.evaluated_depsgraph_get()
    timing = _timing(context)
    return [planning.compile_asset(root, snapshots, timing, depsgraph) for root in roots]


def plan_preview(context, roots):
    """各资产的计划初始条件, 与模拟烘焙编译同一个函数

    碎块路径是 shards.path_of 给出的逐层序号元组; 事件是起动时给出的速度, 不是最终轨迹
    context Blender 上下文
    roots 资产根集合序列
    返回 {资产标识: {碎块路径: ShardPlan}}
    """
    result = {}
    for root, item in zip(roots, compile_assets(context, roots), strict=True):
        result[shards.asset_of(root)] = {
            shards.path_of(shard): item.plan.shards[info.key]
            for shard, info in zip(item.shards, item.infos, strict=True)
        }
    return result


def affected_shards(context, roots):
    """计划里有起动事件的碎块: 驱动会让它们动起来的碎块

    context Blender 上下文
    roots 资产根集合序列
    返回 碎块物体列表
    """
    found = []
    for item in compile_assets(context, roots):
        for shard, info in zip(item.shards, item.infos, strict=True):
            if item.plan.shards[info.key].events:
                found.append(shard)
    return found
