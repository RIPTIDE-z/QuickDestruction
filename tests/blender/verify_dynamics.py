"""动力学测试 - 物理设置, 破坏驱动与模拟烘焙的阶段 API 在真实 Blender 里的行为

测试目的:
    1. 物理应用: 每块碎块建立凸包刚体, 质量等于世界体积乘以密度, 原点移到体积质心而世界几何不动,
       静止姿态只在首次记录, 刚体世界成员不变, 生效记录与状态正确
    2. 单位与变换: 物体缩放与场景单位比例都计入质量; 不封闭的碎块改用凸包体积并计数
    3. 重复应用不再移动原点; 中途失败时属性, 原点, 静止姿态与新建的刚体全部撤回
    4. 移除物理与重设静止姿态; 刚体被删除后状态变为已过期
    5. 材料下拉的联动: 选择材料写入物理量, 手改后仍保留材料并提示, 预设载入与回滚时后写的物理量生效
    6. 破坏驱动: 新建驱动的外观, 集合与默认起动帧; 只有空物体能设为驱动; 快照的作用空间与摘要
    7. 交接精度: 普通, 慢动作, 快动作与分数帧率下起动后第一个动力学帧的速度等于计划速度
    8. 激活方式: 范围外的冻结碎块与锚定碎块全程不动且没有动画; 自由下落的碎块被再交接后速度等于计划值
    9. 会话: 多个资产一起模拟会相互碰撞, 未参与的资产临时移出并恢复, 环境刚体参与但属性不变
    10. 取消与失败: 推进中取消, 写成果与提交阶段失败, 场景都回到烘焙之前, 上一次的成果保留
    11. 状态: 驱动, 帧率, 重新应用物理, 同会话资产与环境刚体的变化都给出对应的过期原因
    12. 守卫: 复位, 外来动画, 运行锁, 起止帧校验, 有成果时禁止重设静止姿态;
        两次烘焙逐位一致; 保存重开后成果仍在
测试过程:
    在后台 Blender 里注册插件, 用 MeshFracture 的阶段 API 切出碎块, 再调用各工具的阶段 API;
    每条判据输出 [PASS] 或 [FAIL], 有失败时以非零状态结束
运行方式 (仓库根目录, 需要 -b 后台模式):
    <Blender 可执行文件> -b --factory-startup --python-exit-code 1 --python tests\\blender\\verify_dynamics.py
实测环境: Blender 5.2.2 (Python 3.13)
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import bpy
from bpy_extras import anim_utils
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness
from harness import check, cube, reset, seeds, volume

from source import registration
from source.common import asset, drivers
from source.common.status import (
    CURRENT,
    NONE,
    OUTDATED,
    environment_signature,
    physics_status,
    simulation_status,
)
from source.tools.Drivers.api import add_driver, make_driver
from source.tools.Drivers.errors import DriverError
from source.tools.MeshFracture.api import generate_fragments
from source.tools.Physics.api import apply_physics, remove_physics, reset_rest
from source.tools.Physics.core import apply as physics_core
from source.tools.Physics.settings import SCHEMA as PHYSICS_SCHEMA
from source.tools.Physics.settings import material_edited
from source.tools.Simulation.api import bake, bake_steps
from source.tools.Simulation.api import reset as reset_simulation
from source.tools.Simulation.core import keys as sim_keys
from source.tools.Simulation.core import session as sim_session
from source.tools.Simulation.core import task as sim_task
from source.tools.Simulation.settings import SCHEMA as SIM_SCHEMA

# 子物体顶点做种子, 关闭扰动, 保证碎块数确定
FRACTURE = {"source": {"VERT_CHILD"}, "first_cut_seeds": 0, "source_noise": 0.0}
# 混凝土的默认物理配置
CONCRETE = {"material": "CONCRETE", "density": 2400.0}


def fractured(name="Src", count=8, location=(0.0, 0.0, 0.0), scale=1.0):
    """切出一个资产, 返回 (资产根集合, 碎块列表)"""
    source = cube(name, half=scale, location=location)
    seeds(source, count, 0.9 * scale, 7)
    result = generate_fragments(bpy.context, [source], {**FRACTURE, "collection_name": "Out"})
    root = result.source_to_asset[source]
    return root, asset.shards_of(root)


def world_bounds(objects):
    """一组物体世界顶点的包围盒, 用于核对几何没有移动"""
    points = [obj.matrix_world @ vertex.co for obj in objects for vertex in obj.data.vertices]
    return tuple(f(point[axis] for point in points) for f in (min, max) for axis in range(3))


def local_centroid(shard):
    """网格局部坐标里的体积质心, 原点已在质心时接近零"""
    from source.common.blender_context import mesh_edit
    from source.common.geometry import volume_centroid

    with mesh_edit(shard.data) as bm:
        return volume_centroid(bm)[1]


def members(scene):
    """刚体世界当前的成员名集合"""
    world = scene.rigidbody_world
    if world is None or world.collection is None:
        return set()
    return {obj.name for obj in world.collection.objects}


def case_physics_apply():
    """物理应用: 凸包刚体, 质量, 质心原点, 世界几何, 静止姿态, 世界成员与生效记录"""
    reset()
    root, shards = fractured()
    before = world_bounds(shards)
    result = apply_physics(bpy.context, [root], CONCRETE)
    check("每块碎块都有刚体", all(shard.rigid_body for shard in shards))
    check("碰撞形状是凸包", all(s.rigid_body.collision_shape == "CONVEX_HULL" for s in shards))
    errors = [abs(s.rigid_body.mass - volume(s) * 2400.0) / (volume(s) * 2400.0) for s in shards]
    # 质量按三角化后的带符号体积计算, 对照值用 calc_volume 按面法线计算, 两者对略不平面的多边形面
    # 有 1e-4 量级的差异; 刚体质量以单精度保存, 总质量按相对误差比较
    check("质量等于世界体积乘以密度", max(errors) < 2e-4, f"最大相对误差 {max(errors):.2e}")
    total = sum(s.rigid_body.mass for s in shards)
    check("返回的总质量等于各块之和", abs(result.mass - total) / total < 1e-6)
    offsets = [local_centroid(shard).length for shard in shards]
    check("原点在体积质心", max(offsets) < 1e-6, f"{max(offsets):.2e}")
    after = world_bounds(shards)
    check("世界几何没有移动", max(abs(a - b) for a, b in zip(before, after, strict=True)) < 1e-6)
    rests = [asset.rest_matrix(shard) for shard in shards]
    check(
        "静止姿态等于当前世界矩阵",
        all(r == s.matrix_world for r, s in zip(rests, shards, strict=True)),
    )
    check("刚体世界成员没有增加", not members(bpy.context.scene) & {s.name for s in shards})
    record = asset.physics_record(root)
    check("生效记录写入修订号 1 与碎块数", record["rev"] == 1 and record["shards"] == len(shards))
    check("生效记录保存材料", record["material"] == "CONCRETE")
    check("物理状态为输入未变", physics_status(root).state == CURRENT)


def case_physics_units():
    """物体缩放与场景单位比例计入质量; 不封闭的碎块改用凸包体积"""
    reset()
    root, shards = fractured()
    apply_physics(bpy.context, [root], CONCRETE)
    base = shards[0].rigid_body.mass
    shards[0].scale = (2.0, 2.0, 2.0)
    bpy.context.view_layer.update()
    apply_physics(bpy.context, [root], CONCRETE)
    check("放大 2 倍后质量变为 8 倍", abs(shards[0].rigid_body.mass / base - 8.0) < 1e-4)
    bpy.context.scene.unit_settings.scale_length = 0.1
    apply_physics(bpy.context, [root], CONCRETE)
    check("单位比例 0.1 时质量乘以 0.001", abs(shards[0].rigid_body.mass / base - 0.008) < 1e-6)
    bpy.context.scene.unit_settings.scale_length = 1.0
    mesh = shards[1].data
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[0]], context="FACES_ONLY")
    bm.to_mesh(mesh)
    bm.free()
    result = apply_physics(bpy.context, [root], CONCRETE)
    check("不封闭的碎块被计数", result.open_meshes == 1, str(result.open_meshes))
    check("不封闭的碎块质量为正", shards[1].rigid_body.mass > 0.001)


def case_physics_reapply():
    """重复应用不再移动原点, 静止姿态不变, 修订号递增"""
    reset()
    root, shards = fractured()
    apply_physics(bpy.context, [root], CONCRETE)
    matrices = [shard.matrix_world.copy() for shard in shards]
    rests = [asset.rest_matrix(shard) for shard in shards]
    apply_physics(bpy.context, [root], {"material": "WOOD"})
    check("原点没有再移动", all(m == s.matrix_world for m, s in zip(matrices, shards, strict=True)))
    check(
        "静止姿态没有改变",
        all(r == asset.rest_matrix(s) for r, s in zip(rests, shards, strict=True)),
    )
    check("修订号递增为 2", asset.physics_record(root)["rev"] == 2)
    check("只给材料时其余物理量取该材料的值", abs(shards[0].rigid_body.friction - 0.6) < 1e-6)
    apply_physics(bpy.context, [root], {"material": "WOOD", "friction": 0.3})
    check("显式给出的物理量优先于材料", abs(shards[0].rigid_body.friction - 0.3) < 1e-6)


def case_physics_rollback():
    """中途失败时撤回属性, 原点, 静止姿态, 新建的刚体与新建的刚体世界"""
    reset()
    root, shards = fractured()
    if bpy.context.scene.rigidbody_world is not None:
        bpy.ops.rigidbody.world_remove()
    matrices = [shard.matrix_world.copy() for shard in shards]
    coords = [[vertex.co.copy() for vertex in shard.data.vertices] for shard in shards]
    original = physics_core._write_body
    calls = []

    def flaky(shard, mass, values, log):
        """第 3 块碎块写属性时失败"""
        calls.append(shard)
        if len(calls) == 3:
            raise RuntimeError("injected physics failure")
        original(shard, mass, values, log)

    with patch.object(physics_core, "_write_body", flaky):
        try:
            apply_physics(bpy.context, [root], CONCRETE)
            check("注入的失败会抛出", False)
        except RuntimeError as error:
            check("注入的失败会抛出", "injected" in str(error), str(error))
    check("新建的刚体都被删除", not any(shard.rigid_body for shard in shards))
    check("原点都已恢复", all(m == s.matrix_world for m, s in zip(matrices, shards, strict=True)))
    restored = all(
        (vertex.co - old).length < 1e-6
        for shard, olds in zip(shards, coords, strict=True)
        for vertex, old in zip(shard.data.vertices, olds, strict=True)
    )
    check("网格坐标都已恢复", restored)
    check("没有残留的静止姿态", all(asset.rest_matrix(shard) is None for shard in shards))
    check("没有残留的生效记录", asset.physics_record(root) is None)
    check("本次新建的刚体世界已删除", bpy.context.scene.rigidbody_world is None)


def case_physics_remove_and_rest():
    """移除物理, 重设静止姿态, 刚体被删除后状态为已过期"""
    reset()
    root, shards = fractured()
    apply_physics(bpy.context, [root], CONCRETE)
    moved = shards[0].matrix_world.translation + Vector((0.0, 0.0, 1.0))
    # 故意不刷新依赖图: 重设静止姿态要自己刷新, 否则记下的是移动前的世界矩阵
    shards[0].location += Vector((0.0, 0.0, 1.0))
    count = reset_rest(bpy.context, [root])
    check("重设静止姿态处理全部碎块", count == len(shards))
    gap = (asset.rest_matrix(shards[0]).translation - moved).length
    check("脚本移动后不刷新直接重设, 静止姿态等于移动后的位置", gap < 1e-6, gap)
    check("重设后修订号加一", asset.physics_record(root)["rev"] == 2)
    bpy.data.objects.remove(shards[1])
    check("碎块数变化后物理已过期", physics_status(root).state == OUTDATED)
    remove_physics(bpy.context, [root])
    remaining = asset.shards_of(root)
    check("移除后没有刚体", not any(shard.rigid_body for shard in remaining))
    check("移除后状态为未完成", physics_status(root).state == NONE)
    check("移除后原点与静止姿态保留", asset.rest_matrix(remaining[0]) is not None)


def case_material_link():
    """材料下拉的联动, 手改提示, 预设写入顺序与回滚"""
    reset()
    owner = bpy.context.scene.qd_settings.physics
    owner.material = "WOOD"
    check("选择木材写入密度 600", abs(owner.density - 600.0) < 1e-6)
    owner.density = 700.0
    check("手改数值后材料保持木材", owner.material == "WOOD")
    check("手改数值后提示已修改", material_edited(owner))
    PHYSICS_SCHEMA.write(owner, {"material": "GLASS", "density": 3000.0})
    check("预设里的数值覆盖材料联动的值", abs(owner.density - 3000.0) < 1e-6)
    check("预设里没给的数值取材料的值", abs(owner.friction - 0.4) < 1e-6)
    before = PHYSICS_SCHEMA.read(owner)
    try:
        PHYSICS_SCHEMA.write(owner, {"material": "METAL", "density": "heavy"})
    except Exception:
        pass
    check("写入失败时参数保持不变", PHYSICS_SCHEMA.read(owner) == before)


def case_drivers():
    """新建驱动, 只有空物体能设为驱动, 快照的作用空间与摘要"""
    reset()
    scene = bpy.context.scene
    scene.frame_current = 20
    obj = add_driver(bpy.context, drivers.EXPLOSION, location=(1.0, 2.0, 3.0))
    check("爆炸驱动显示为球", obj.empty_display_type == "SPHERE")
    check("驱动放进 QD_Drivers 集合", obj.users_collection[0].name == "QD_Drivers")
    check("默认起动帧取当前帧", obj.qd_driver.frame == 20)
    check("驱动出现在启用列表里", drivers.driver_objects(scene) == [obj])
    obj.scale = (2.0, 2.0, 2.0)
    bpy.context.view_layer.update()
    snap = drivers.snapshot(obj)
    from source.common.impulse import length, transform_point

    edge = transform_point(snap.inverse, (3.0, 2.0, 3.0))
    check("范围边缘映射到单位距离", abs(length(edge) - 1.0) < 1e-6, str(edge))
    check("作用半径按缩放显示", abs(drivers.range_radius(obj) - 2.0) < 1e-6)
    before = drivers.digest([snap])
    obj.location.x += 0.5
    bpy.context.view_layer.update()
    check("移动驱动后摘要改变", drivers.digest([drivers.snapshot(obj)]) != before)
    obj.qd_driver.enabled = False
    check("停用的驱动不在启用列表里", drivers.driver_objects(scene) == [])
    check("停用的驱动仍在全部列表里", drivers.all_drivers(scene) == [obj])
    try:
        make_driver(cube("Mesh"), drivers.PUSH)
        check("网格不能设为驱动", False)
    except DriverError as error:
        check("网格不能设为驱动", "empty" in error.message)
    empty = bpy.data.objects.new("Plain", None)
    scene.collection.objects.link(empty)
    make_driver(empty, drivers.PUSH)
    check("空物体设为推力后显示为箭头", empty.empty_display_type == "SINGLE_ARROW")


def sim_scene(gravity=True, fps=24, fps_base=1.0, end=40):
    """清空场景并设好帧范围与重力; 模拟用场景的帧范围"""
    reset()
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = fps, fps_base
    scene.frame_start, scene.frame_end = 1, end
    scene.use_gravity = gravity
    sim = scene.qd_settings.simulation
    sim.use_scene_range, sim.time_scale = True, 1.0
    return scene


def physical(name, location=(0.0, 0.0, 1.0), count=10, activation="FROZEN", **extra):
    """切出一个资产并应用物理, 返回 (根集合, 碎块列表)"""
    root, shards = fractured(name, count=count, location=location)
    config = {"material": "CONCRETE", "activation": activation, **extra}
    apply_physics(bpy.context, [root], config)
    return root, shards


def explosion(location, radius, frame=5, **params):
    """在 location 放一个半径 radius 的爆炸驱动, 默认关闭随机锥角与自转, 便于核对速度"""
    values = {"frame": frame, "speed": 6.0, "falloff": "CONSTANT", "spread": 0.0, "max_spin": 0.0}
    obj = add_driver(bpy.context, drivers.EXPLOSION, location=location, **{**values, **params})
    obj.scale = (radius, radius, radius)
    bpy.context.view_layer.update()
    return obj


def plans(roots):
    """不改动场景地编译计划, 返回 {碎块名: ShardPlan}"""
    session = sim_session.build(bpy.context, roots, SIM_SCHEMA.resolve({}))
    return {participant.shard.name: participant.plan for participant in session.participants}


def position(shard, frame):
    """回放到 frame 帧时碎块的世界位置; 烘焙后碎块不在刚体世界里, 可以任意跳帧"""
    bpy.context.scene.frame_set(frame)
    return shard.matrix_world.translation.copy()


def keyed(shard):
    """碎块成果动作里的全部关键帧坐标, 用于逐位比较"""
    data = shard.animation_data
    if data is None or data.action is None:
        return ()
    bag = anim_utils.action_get_channelbag_for_slot(data.action, data.action_slot)
    return tuple(tuple(point.co) for curve in bag.fcurves for point in curve.keyframe_points)


def displaced(shard, frame):
    """碎块在 frame 帧离静止位置的距离"""
    return (position(shard, frame) - asset.rest_matrix(shard).translation).length


def message_of(error):
    """领域错误的消息键, 其他异常的文字"""
    return getattr(error, "message", str(error))


def check_handoff(fps, fps_base, time_scale):
    """无重力时起动后第一个动力学帧的速度等于计划速度, 速度按模拟秒计"""
    scene = sim_scene(gravity=False, fps=fps, fps_base=fps_base)
    scene.qd_settings.simulation.time_scale = time_scale
    # 关闭阻尼: 阻尼会在第一个动力学帧里削减速度; 均匀推力让碎块同速平移, 彼此没有相对运动,
    # 接触不会干扰速度, 这里核对的是交接本身
    root, shards = physical("Wall", linear_damping=0.0, angular_damping=0.0)
    values = {"frame": 5, "speed": 6.0, "falloff": "CONSTANT", "spread": 0.0, "max_spin": 0.0}
    push = add_driver(bpy.context, drivers.PUSH, location=(0.0, 0.0, 1.0), **values)
    push.rotation_euler = (0.0, 1.5707963267948966, 0.0)
    push.scale = (5.0, 5.0, 5.0)
    bpy.context.view_layer.update()
    session = sim_session.build(bpy.context, [root], SIM_SCHEMA.resolve({"time_scale": time_scale}))
    expected = {p.shard.name: p.plan for p in session.participants}
    bake(bpy.context, [root], {"time_scale": time_scale})
    dt = time_scale * fps_base / fps
    worst = 0.0
    for shard in shards:
        event = expected[shard.name].events[0]
        speed = (position(shard, event.frame + 2) - position(shard, event.frame + 1)) / dt
        worst = max(worst, (speed - Vector(event.velocity)).length)
    return worst


def case_bake_handoff():
    """交接精度: 普通, 慢动作, 快动作与分数帧率下起动速度都等于计划速度"""
    for fps, base, scale in ((24, 1.0, 1.0), (24, 1.0, 0.5), (24, 1.0, 2.0), (24, 1.001, 1.0)):
        worst = check_handoff(fps, base, scale)
        label = f"起动速度等于计划速度 fps={fps} base={base} scale={scale}"
        check(label, worst < 1e-3, f"{worst:.2e}")


def case_bake_frozen_anchor():
    """范围外的冻结碎块与锚定的碎块全程不动, 不带动画; 范围内的碎块被炸开"""
    scene = sim_scene()
    root, shards = physical("Wall", use_anchor_bottom=True, anchor_height=0.3)
    # 爆炸放在顶部, 上抛足够大, 被波及的碎块都向上飞出, 不会被下方冻结的碎块挡住
    explosion((0.0, 0.0, 1.7), 0.7, lift=2.0)
    expected = plans([root])
    result = bake(bpy.context, [root], {})
    anchored = [s for s in shards if expected[s.name].mode == "ANCHORED"]
    idle = [s for s in shards if expected[s.name].mode == "FROZEN" and not expected[s.name].events]
    hit = [s for s in shards if expected[s.name].events]
    counts = f"{len(anchored)} {len(idle)} {len(hit)}"
    check("有锚定, 未波及与被波及三类碎块", bool(anchored and idle and hit), counts)
    still = all(keyed(s) == () and displaced(s, 40) < 1e-6 for s in anchored + idle)
    check("锚定与未波及的碎块全程不动且没有动画", still)
    # 冻结的邻居不可撞动: 被炸起的碎块撞上它会停下, 所以只要求被波及的都带动画, 且确有碎块飞远
    check("被波及的碎块都带烘焙动画", all(keyed(s) for s in hit))
    farthest = max(displaced(s, 40) for s in hit)
    check("确有被波及的碎块飞出 1 米以上", farthest > 1.0, f"{farthest:.2f}")
    check("报告的动过块数与被波及块数一致", result.moved == len(hit), f"{result.moved} {len(hit)}")
    check("烘焙后碎块都已移出刚体世界", not members(scene) & {s.name for s in shards})
    check("烘焙后刚体数据保留", all(s.rigid_body for s in shards))
    check("烘焙后模拟状态为输入未变", simulation_status(root, scene).state == CURRENT)


def case_bake_rehandoff():
    """自由下落中的碎块被后来的驱动再交接: 水平速度等于计划值, 模拟不重置"""
    sim_scene(gravity=True)
    damping = {"linear_damping": 0.0, "angular_damping": 0.0}
    root, shards = physical("Wall", location=(0.0, 0.0, 6.0), activation="FREE", **damping)
    values = {"frame": 10, "speed": 5.0, "falloff": "CONSTANT", "spread": 0.0, "max_spin": 0.0}
    push = add_driver(bpy.context, drivers.PUSH, location=(0.0, 0.0, 4.0), **values)
    push.rotation_euler = (0.0, 1.5707963267948966, 0.0)
    push.scale = (10.0, 10.0, 10.0)
    bpy.context.view_layer.update()
    expected = plans([root])
    result = bake(bpy.context, [root], {})
    check("每块碎块各再交接一次", result.rehandoffs == len(shards), f"{result.rehandoffs}")
    dt = 1.0 / 24.0
    worst = 0.0
    for shard in shards:
        event = expected[shard.name].events[0]
        speed = (position(shard, event.frame + 2) - position(shard, event.frame + 1)) / dt
        worst = max(worst, abs(speed.x - event.velocity[0]), abs(speed.y - event.velocity[1]))
    check("再交接后的水平速度等于计划值", worst < 1e-3, f"{worst:.2e}")
    fallen = all(position(s, 9).z < asset.rest_matrix(s).translation.z - 0.1 for s in shards)
    check("再交接前已经下落, 模拟没有重置", fallen)


def case_bake_session():
    """多资产会话: 一起模拟会相互碰撞; 未参与的资产临时移出并恢复; 环境刚体参与但属性不变"""
    scene = sim_scene()
    # 地面足够大: 碎块以 12 m/s 飞 40 帧约 20 m, 不能从边缘掉下去
    ground = cube("Ground", half=60.0, location=(0.0, 0.0, -60.0))
    left, left_shards = physical("Left", location=(-1.2, 0.0, 1.0))
    right, right_shards = physical("Right", location=(1.2, 0.0, 1.0), activation="SLEEPING")
    _other, other_shards = physical("Other", location=(0.0, 8.0, 1.0), activation="FREE")
    world = scene.rigidbody_world
    world.collection.objects.link(ground)
    ground.rigid_body.type = "PASSIVE"
    for shard in other_shards:
        world.collection.objects.link(shard)
    explosion((-2.6, 0.0, 1.0), 1.8, speed=12.0, lift=0.0)
    before = environment_signature(scene)
    bake(bpy.context, [left, right], {})
    same = asset.sim_record(left)["run"] == asset.sim_record(right)["run"]
    check("两个资产记在同一个会话里", same)
    hit = [s for s in right_shards if displaced(s, 40) > 0.01]
    check("右边休眠的资产被左边飞来的碎块撞动", bool(hit), f"{len(hit)}")
    back = all(s.name in world.collection.objects for s in other_shards)
    check("未参与的资产恢复到刚体世界里", back)
    check("未参与的资产没有被烘焙", all(keyed(s) == () for s in other_shards))
    check("环境刚体留在刚体世界里", "Ground" in world.collection.objects)
    check("环境刚体的签名不变", environment_signature(scene) == before)
    lowest = min(position(s, 40).z for s in left_shards)
    check("碎块落在环境地面上而没有穿过", lowest > -0.5, f"{lowest:.3f}")


def case_bake_cancel():
    """取消: 推进到一半关闭生成器, 场景回到烘焙之前, 上一次的成果原样保留"""
    scene = sim_scene()
    root, shards = physical("Wall")
    explosion((0.0, 0.0, 1.0), 3.0)
    bake(bpy.context, [root], {})
    old_action = asset.sim_action(root)
    old_keys = [keyed(s) for s in shards]
    world = scene.rigidbody_world
    substeps = world.substeps_per_frame
    scene.frame_set(17)
    steps = bake_steps(bpy.context, [root], {"substeps": 25})
    for _index in range(12):
        next(steps)
    check("推进中碎块在刚体世界里", all(s.name in world.collection.objects for s in shards))
    steps.close()
    check("取消后碎块移出刚体世界", not members(scene) & {s.name for s in shards})
    check("取消后世界设置恢复", world.substeps_per_frame == substeps)
    check("取消后回到原来的帧", scene.frame_current == 17)
    check("取消后上一次的成果动作还在", asset.sim_action(root) == old_action)
    check("取消后碎块仍用上一次的关键帧", [keyed(s) for s in shards] == old_keys)
    names = [action.name for action in bpy.data.actions]
    check("取消后没有残留的临时动作", names == [old_action.name], str(names))


def case_bake_failures():
    """写成果与提交两个阶段注入失败: 场景回到烘焙之前, 旧成果保留"""

    def broken(*_args, **_kwargs):
        """注入的失败"""
        raise RuntimeError("injected bake failure")

    for label, target, name in (("写成果", sim_keys, "write_track"), ("提交", asset, "add_run")):
        scene = sim_scene()
        root, shards = physical("Wall")
        explosion((0.0, 0.0, 1.0), 3.0)
        bake(bpy.context, [root], {})
        old_action = asset.sim_action(root)
        old_keys = [keyed(s) for s in shards]
        with patch.object(target, name, broken):
            try:
                bake(bpy.context, [root], {"substeps": 20})
                check(f"{label}失败会抛出", False)
            except RuntimeError as error:
                check(f"{label}失败会抛出", "injected" in str(error))
        check(f"{label}失败后旧成果动作保留", asset.sim_action(root) == old_action)
        check(f"{label}失败后碎块仍用旧关键帧", [keyed(s) for s in shards] == old_keys)
        names = [action.name for action in bpy.data.actions]
        check(f"{label}失败后没有残留动作", names == [old_action.name], str(names))
        check(f"{label}失败后碎块不在刚体世界里", not members(scene) & {s.name for s in shards})
        state = simulation_status(root, scene).state
        check(f"{label}失败后状态仍为输入未变", state == CURRENT, state)


def case_bake_status():
    """过期原因: 驱动, 场景帧率, 重新应用物理, 同一会话的另一个资产, 环境刚体"""
    scene = sim_scene()
    left, _left = physical("Left", location=(-1.5, 0.0, 1.0))
    right, _right = physical("Right", location=(1.5, 0.0, 1.0))
    driver = explosion((0.0, 0.0, 1.0), 3.0)
    # 故意不刷新依赖图: 烘焙要自己刷新, 否则计划用旧位置, 烘焙后马上显示驱动已变
    driver.location.y += 0.2
    bake(bpy.context, [left, right], {})
    after = simulation_status(left, scene)
    check("脚本移动驱动后不刷新直接烘焙, 烘焙后为输入未变", after.state == CURRENT, after)

    def reason():
        """左边资产的过期原因"""
        return simulation_status(left, scene).reason

    driver.location.x += 0.3
    bpy.context.view_layer.update()
    check("移动驱动后过期", reason() == "Drivers changed", reason())
    driver.location.x -= 0.3
    bpy.context.view_layer.update()
    check("移回原处后恢复为输入未变", simulation_status(left, scene).state == CURRENT)
    scene.render.fps = 30
    check("改帧率后过期", reason() == "Frame rate, units or gravity changed", reason())
    scene.render.fps = 24
    apply_physics(bpy.context, [right], {"material": "WOOD"})
    expected = "Another asset of the same simulation changed"
    check("同一会话的另一个资产重新应用物理后过期", reason() == expected, reason())
    apply_physics(bpy.context, [left], {"material": "WOOD"})
    check("自己重新应用物理后过期", reason() == "Physics was applied again", reason())
    bake(bpy.context, [left, right], {})
    box = cube("Box", location=(0.0, 0.0, -3.0))
    scene.rigidbody_world.collection.objects.link(box)
    check("加入新的环境刚体后过期", reason() == "Other rigid bodies changed", reason())


def expect_error(label, call, fragment):
    """调用 call, 期望抛出消息包含 fragment 的错误"""
    try:
        call()
        check(label, False, "没有报错")
    except Exception as error:
        check(label, fragment in message_of(error), message_of(error))


def case_bake_reset_and_guards():
    """复位, 外来动画, 运行锁, 起止帧校验, 已有成果时禁止重设静止姿态"""
    scene = sim_scene()
    root, shards = physical("Wall")
    explosion((0.0, 0.0, 1.0), 3.0)
    bake(bpy.context, [root], {})
    expect_error(
        "有模拟成果时拒绝重设静止姿态",
        lambda: reset_rest(bpy.context, [root]),
        "Reset the simulation",
    )
    count = reset_simulation(bpy.context, [root])
    check("复位处理全部碎块", count == len(shards))
    check("复位后没有成果动作", asset.sim_action(root) is None and not bpy.data.actions)
    check("复位后碎块回到静止姿态", all(s.matrix_world == asset.rest_matrix(s) for s in shards))
    check("复位后模拟状态为未完成", simulation_status(root, scene).state == NONE)
    check("复位后会话记录被清理", asset.runs(scene) == {})
    shards[0].keyframe_insert("location", frame=1)
    expect_error("碎块带外来动画时拒绝", lambda: bake(bpy.context, [root], {}), "own animation")
    shards[0].animation_data_clear()
    custom = {"use_scene_range": False, "frame_start": 30, "frame_end": 30}
    expect_error("结束帧不晚于起始帧时拒绝", lambda: bake(bpy.context, [root], custom), "End Frame")
    sim_task.claim(sim_task.ensure_idle)
    try:
        expect_error("已有任务时拒绝", lambda: bake(bpy.context, [root], {}), "Another simulation")
    finally:
        sim_task.release(sim_task.ensure_idle)


def case_bake_deterministic():
    """同一场景烘焙两次关键帧逐位一致"""
    sim_scene()
    root, shards = physical("Wall")
    explosion((0.0, 0.0, 1.0), 3.0, spread=0.4, max_spin=6.0)
    bake(bpy.context, [root], {})
    first = [keyed(s) for s in shards]
    bake(bpy.context, [root], {})
    check("两次烘焙的关键帧逐位一致", [keyed(s) for s in shards] == first)


def case_bake_persistence():
    """保存并重新打开后, 成果动作, 会话记录与状态都还在"""
    sim_scene()
    root, shards = physical("Wall")
    explosion((0.0, 0.0, 1.0), 3.0)
    bake(bpy.context, [root], {})
    name, moved_at = root.name, position(shards[0], 30)
    path = Path(tempfile.mkdtemp(prefix="qd_sim_")) / "sim.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    bpy.ops.wm.open_mainfile(filepath=str(path))
    reopened = bpy.data.collections[name]
    scene = bpy.context.scene
    check("重开后成果动作还在", asset.sim_action(reopened) is not None)
    check("重开后状态为输入未变", simulation_status(reopened, scene).state == CURRENT)
    shard = asset.shards_of(reopened)[0]
    check("重开后回放位置不变", (position(shard, 30) - moved_at).length < 1e-6)


def main():
    """注册插件后运行全部场景"""
    registration.register()
    harness.run_cases(CASES)


CASES = (
    case_physics_apply,
    case_physics_units,
    case_physics_reapply,
    case_physics_rollback,
    case_physics_remove_and_rest,
    case_material_link,
    case_drivers,
    case_bake_handoff,
    case_bake_frozen_anchor,
    case_bake_rehandoff,
    case_bake_session,
    case_bake_cancel,
    case_bake_failures,
    case_bake_status,
    case_bake_reset_and_guards,
    case_bake_deterministic,
    # 会重新打开文件, 放在最后
    case_bake_persistence,
)

main()
