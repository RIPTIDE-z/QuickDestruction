"""游戏导出测试 - 绑定构建, 检查与 FBX 导出的阶段 API 在真实 Blender 里的行为

测试目的:
    1. 构建: 每块碎块都进了蒙皮网格 (几何守恒), 会动的碎块各有一根骨骼, 不动的并入 root;
       逐帧 (全部帧) 蒙皮网格与碎块动画的世界顶点一致; 命名, 集合, 材质与记录正确; 构建不留临时数据
    2. 骨骼上限: 每段网格骨骼数与每个骨架骨骼总数的上限生效, 拆分后仍逐帧一致
    3. 骨骼名: 路径名符合规则; 路径名不可用时退回顺序号并记录映射
    4. 断面 UV: 盒投影只写断面, 外表面 UV 不变; 选择保持时断面 UV 不动
    5. 失败回滚: 校验失败时旧导出, 绑定记录与场景选择原样保留, 没有残留的临时数据
    6. 状态: 构建后输入未变; 重新烘焙后过期; 导出记录随绑定重建而过期; 设置摘要随参数变化
    7. 导出: 文件写出, 覆盖需要确认, 轴向冲突, 目录与未保存文件的错误; 过期的绑定拒绝导出;
       导出后选择, 活动物体, 当前帧与帧范围还原
    7b. 文件名: 默认用资产名, 自定义名与占位符, 扩展名与非法字符, 多骨架序号, 多资产写到同一文件时拒绝
    8. 往返: 导出的 FBX 重新导入 (动画偏移为 0), 各帧的顶点都能在碎块动画给出的世界顶点上找到
测试过程:
    在后台 Blender 里注册插件, 用 MeshFracture, Physics, Drivers 与 Simulation 的阶段 API 得到烘焙好的
    资产, 再调用 GameExport 的阶段 API; 每条判据输出 [PASS] 或 [FAIL], 有失败时以非零状态结束
运行方式 (仓库根目录, 需要 -b 后台模式):
    <Blender 可执行文件> -b --factory-startup --python-exit-code 1 --python tests\\blender\\verify_export.py
实测环境: Blender 5.2.2 (Python 3.13)
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import bpy
from mathutils.kdtree import KDTree

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness
from harness import check, cube, reset, seeds

from source import registration
from source.common import asset, drivers
from source.common.status import CURRENT, NONE, OUTDATED, export_status, rig_status
from source.tools.Drivers.api import add_driver
from source.tools.GameExport.api import build_rig, existing_files, export_fbx, export_paths
from source.tools.GameExport.api import check as check_rig
from source.tools.GameExport.core import build as build_core
from source.tools.GameExport.core import check as check_core
from source.tools.GameExport.core import interior
from source.tools.GameExport.errors import ExportError
from source.tools.MeshFracture.api import generate_fragments
from source.tools.Physics.api import apply_physics
from source.tools.Simulation.api import bake

# 子物体顶点做种子, 关闭扰动, 保证碎块数确定
FRACTURE = {"source": {"VERT_CHILD"}, "first_cut_seeds": 0, "source_noise": 0.0}
# 往返比较的顶点容差, Blender 单位; FBX 以双精度存储, 误差主要来自导入端的换算
ROUND_TRIP_TOLERANCE = 1e-3


def baked_asset(count=10, partial=False):
    """切碎, 应用物理并烘焙一个资产, 返回 (根集合, 碎块列表); partial 为真时只有一部分碎块被爆炸波及"""
    reset()
    for action in list(bpy.data.actions):
        bpy.data.actions.remove(action)
    scene = bpy.context.scene
    scene.render.fps, scene.render.fps_base = 24, 1.0
    scene.frame_start, scene.frame_end = 1, 30
    scene.use_gravity = True
    sim = scene.qd_settings.simulation
    sim.use_scene_range, sim.time_scale = True, 1.0
    source = cube("Src", half=1.0, location=(0.0, 0.0, 1.0))
    seeds(source, count, 0.9, 7)
    result = generate_fragments(bpy.context, [source], {**FRACTURE, "collection_name": "Out"})
    root = result.source_to_asset[source]
    apply_physics(bpy.context, [root], {"material": "CONCRETE", "activation": "FROZEN"})
    center, radius = ((1.5, 0.0, 1.0), 1.2) if partial else ((0.0, 0.0, 1.0), 3.0)
    values = {"frame": 3, "speed": 5.0, "falloff": "CONSTANT", "spread": 0.0, "max_spin": 2.0}
    blast = add_driver(bpy.context, drivers.EXPLOSION, location=center, **values)
    blast.scale = (radius, radius, radius)
    bpy.context.view_layer.update()
    bake(bpy.context, [root], {})
    return root, asset.shards_of(root)


def export_parts(root):
    """导出集合里的 (骨架列表, 网格列表)"""
    return check_core.export_objects(root)


def all_frames(root):
    """模拟范围里的全部帧"""
    first, last = asset.rig_record(root)["frames"]
    return list(range(first, last + 1))


def skin_error(root):
    """全部帧上蒙皮网格与碎块动画的最大顶点偏差"""
    error, _frame = check_core.verify_skin(bpy.context, root, all_frames(root))
    return error


def leftovers():
    """构建期间的临时名字还留着的数据块数"""
    prefix = "QD_Building_"
    kinds = (
        bpy.data.objects,
        bpy.data.meshes,
        bpy.data.armatures,
        bpy.data.collections,
        bpy.data.actions,
    )
    return sum(1 for kind in kinds for item in kind if item.name.startswith(prefix))


def case_build_basics():
    """构建: 守恒, 逐帧一致, 命名, 集合, 记录与不留临时数据"""
    root, shards = baked_asset(partial=True)
    results = build_rig(bpy.context, [root], {})
    rig = results[0]
    check("返回一个构建结果", len(results) == 1)
    check(
        "会动的与不动的碎块数之和等于碎块总数",
        rig.moving + rig.static == len(shards),
        f"{rig.moving}+{rig.static}",
    )
    check("部分波及时有不动的碎块", rig.static > 0 and rig.moving > 0, f"{rig.moving}/{rig.static}")
    armatures, meshes = export_parts(root)
    check("一个骨架一段网格", len(armatures) == 1 and len(meshes) == 1)
    check("骨骼数等于会动的碎块数加 root", rig.bones == rig.moving + 1, str(rig.bones))
    collection = asset.rig_collection(root)
    check("导出集合名是源名加后缀", collection.name == "Src_Export", collection.name)
    check("骨架与网格名正确", armatures[0].name == "Src_Rig" and meshes[0].name == "Src_RigMesh")
    check("没有残留的临时名数据", leftovers() == 0, str(leftovers()))
    check("导出集合挂在场景里", collection.name in bpy.context.scene.collection.children)
    issues = check_rig(bpy.context, root, {})
    errors = [issue for issue in issues if issue.level == check_core.ERROR]
    check("检查没有错误", not errors, str([issue.code for issue in errors]))
    error = skin_error(root)
    check("全部帧蒙皮与碎块动画一致", error < 1e-4, f"{error:.2e}")
    groups = {group.name for group in meshes[0].vertex_groups}
    check(
        "顶点组与骨骼一一对应",
        groups == {bone.name for bone in armatures[0].data.bones},
        str(len(groups)),
    )
    names = [bone.name for bone in armatures[0].data.bones if bone.name != "root"]
    check(
        "骨骼名是 b_ 加逐层序号且只含 ASCII",
        all(n.startswith("b_") and n.isascii() for n in names),
        names[0],
    )
    record = asset.rig_record(root)
    check(
        "绑定记录写入帧范围与骨骼数", record["frames"] == [1, 30] and record["bones"] == rig.bones
    )
    check("绑定记录含构建参数摘要", bool(record["digest"]))
    check("绑定状态为输入未变", rig_status(root, bpy.context.scene).state == CURRENT)
    action = armatures[0].animation_data.action
    check("骨架动作按名字命名", action.name == "QD_Rig_Src_Rig", action.name)


def case_build_all_moving():
    """全部被波及时没有不动的碎块, 全部骨骼都有关键帧"""
    root, shards = baked_asset(count=8)
    rig = build_rig(bpy.context, [root], {})[0]
    check(
        "全部碎块都会动",
        rig.static == 0 and rig.moving == len(shards),
        f"{rig.moving}/{rig.static}",
    )
    check("逐帧一致", skin_error(root) < 1e-4)


def case_bone_limits():
    """骨骼上限: 每段网格与每个骨架的上限生效, 拆分后仍逐帧一致"""
    root, _shards = baked_asset(count=12)
    build_rig(bpy.context, [root], {"max_bones_per_mesh": 5})
    armatures, meshes = export_parts(root)
    check("网格被拆成多段", len(meshes) > 1, str(len(meshes)))
    check("每段网格的顶点组不超过上限", all(len(mesh.vertex_groups) <= 5 for mesh in meshes))
    check("逐帧一致 (拆段)", skin_error(root) < 1e-4)
    build_rig(bpy.context, [root], {"max_bones_total": 5})
    armatures, meshes = export_parts(root)
    check("骨架被拆成多个", len(armatures) > 1, str(len(armatures)))
    check("每个骨架的骨骼数不超过上限", all(len(arm.data.bones) <= 5 for arm in armatures))
    check("每段网格归属某个骨架", all(mesh.parent in armatures for mesh in meshes))
    check("逐帧一致 (拆骨架)", skin_error(root) < 1e-4)
    check("多个骨架时没有残留临时数据", leftovers() == 0)
    try:
        build_rig(bpy.context, [root], {"max_bones_total": 1})
        failed = False
    except ValueError:
        failed = True
    check("上限小于 2 时拒绝", failed)


def case_bone_name_fallback():
    """路径名不可用时退回顺序号, 映射记在绑定记录里"""
    root, shards = baked_asset(count=8)
    with patch.object(build_core, "bone_name", return_value=None):
        build_rig(bpy.context, [root], {})
    armatures, _meshes = export_parts(root)
    names = sorted(bone.name for bone in armatures[0].data.bones if bone.name != "root")
    check("退回 b_ 加五位顺序号", names[0] == "b_00000", names[0])
    mapping = asset.rig_record(root)["map"]
    check(
        "记录了骨骼到碎块的映射",
        set(mapping) == set(names) and set(mapping.values()) <= {s.name for s in shards},
    )
    check("退回命名后逐帧一致", skin_error(root) < 1e-4)


def uv_of(mesh, face):
    """一个面各角的 UV"""
    layer = mesh.uv_layers.active
    return [tuple(layer.data[loop].uv) for loop in mesh.polygons[face].loop_indices]


def case_interior_uv():
    """断面 UV: 盒投影只写断面, Keep 时不动"""
    root, _shards = baked_asset(count=8)
    build_rig(bpy.context, [root], {"interior_uv": "BOX", "interior_uv_scale": 2.0})
    _armatures, meshes = export_parts(root)
    mesh = meshes[0].data
    faces = interior.interior_faces(mesh)
    check("蒙皮网格带断面标记且有断面", bool(faces), str(len(faces or ())))
    check("有 UV 层", len(mesh.uv_layers) > 0)
    # 新建的 UV 层里外表面是 Blender 的默认展开, 不是零; 这里核对断面的值等于世界坐标的盒投影
    worst = 0.0
    for face in faces:
        polygon = mesh.polygons[face]
        axis = max(range(3), key=lambda a: (abs(polygon.normal[a]), -a))
        first, second = [a for a in range(3) if a != axis]
        for loop, uv in zip(polygon.loop_indices, uv_of(mesh, face), strict=True):
            co = mesh.vertices[mesh.loops[loop].vertex_index].co
            worst = max(worst, abs(uv[0] - co[first] * 2.0), abs(uv[1] - co[second] * 2.0))
    check("断面 UV 等于世界坐标沿主轴的盒投影", worst < 1e-5, f"最大偏差 {worst:.2e}")
    build_rig(bpy.context, [root], {"interior_uv": "KEEP"})
    _armatures, meshes = export_parts(root)
    check("选择保持时不写 UV", len(meshes[0].data.uv_layers) == 0)


def case_failure_rollback():
    """校验失败时旧导出, 绑定记录, 选择与帧原样保留, 没有残留的临时数据"""
    root, _shards = baked_asset(count=8)
    build_rig(bpy.context, [root], {})
    old_id = asset.rig_record(root)["id"]
    old_collection = asset.rig_collection(root)
    old_names = sorted(obj.name for obj in old_collection.objects)
    objects_before = len(bpy.data.objects)
    meshes_before = len(bpy.data.meshes)
    shard = asset.shards_of(root)[0]
    bpy.context.view_layer.objects.active = shard
    shard.select_set(True)
    bpy.context.scene.frame_set(7)
    with patch.object(build_core.check, "verify_skin", return_value=(1.0, 3)):
        try:
            build_rig(bpy.context, [root], {"max_bones_per_mesh": 4})
            failed = False
        except ExportError as error:
            failed = True
            check("错误消息指向校验失败", "failed its checks" in error.message, error.message)
    check("校验失败时抛出错误", failed)
    check("绑定记录没变", asset.rig_record(root)["id"] == old_id)
    check("导出集合引用没变", asset.rig_collection(root) == old_collection)
    check("旧导出的物体没变", sorted(obj.name for obj in old_collection.objects) == old_names)
    check("没有残留的临时名数据", leftovers() == 0, str(leftovers()))
    check(
        "物体数没有增加",
        len(bpy.data.objects) == objects_before,
        f"{len(bpy.data.objects)}/{objects_before}",
    )
    check("网格数没有增加", len(bpy.data.meshes) == meshes_before)
    check(
        "选择与活动物体保持", shard.select_get() and bpy.context.view_layer.objects.active == shard
    )
    check("当前帧保持", bpy.context.scene.frame_current == 7)
    check("旧绑定仍然逐帧一致", skin_error(root) < 1e-4)


def case_no_simulation():
    """没有模拟成果时拒绝构建; 没构建时检查与导出都拒绝"""
    root, _shards = baked_asset(count=8)
    from source.tools.Simulation.api import reset as reset_simulation

    check_none = rig_status(root, bpy.context.scene).state == NONE
    check("没构建时绑定状态为未完成", check_none)
    for label, action in (
        ("检查", lambda: check_rig(bpy.context, root, {})),
        ("导出", lambda: export_fbx(bpy.context, root, {"directory": tempfile.gettempdir()})),
    ):
        try:
            action()
            refused = False
        except ExportError:
            refused = True
        check(f"没构建时{label}被拒绝", refused)
    reset_simulation(bpy.context, [root])
    try:
        build_rig(bpy.context, [root], {})
        refused = False
    except ExportError:
        refused = True
    check("没有模拟成果时构建被拒绝", refused)
    check("拒绝后没有绑定记录", asset.rig_record(root) is None and leftovers() == 0)


def case_status():
    """状态: 构建后输入未变; 重新烘焙后过期; 导出记录随绑定重建而过期; 设置摘要随参数变化"""
    root, _shards = baked_asset(count=8)
    scene = bpy.context.scene
    build_rig(bpy.context, [root], {})
    check("构建后输入未变", rig_status(root, scene).state == CURRENT)
    check("没导出时导出状态为未完成", export_status(root).state == NONE)
    digest = asset.rig_record(root)["digest"]
    build_rig(bpy.context, [root], {"static_distance": 0.5})
    check("参数变化后摘要变化", asset.rig_record(root)["digest"] != digest)
    with tempfile.TemporaryDirectory() as directory:
        export_fbx(bpy.context, root, {"directory": directory})
        check("导出后导出状态输入未变", export_status(root).state == CURRENT)
        build_rig(bpy.context, [root], {})
        check("绑定重建后导出状态过期", export_status(root).state == OUTDATED)
    # 重新烘焙后, 基于旧成果的绑定与导出集合被一并清除, 不留可能被误导出的旧数据
    collection = asset.rig_collection(root)
    old_name = collection.name
    old_armatures = {obj.data.name for obj in collection.objects if obj.type == "ARMATURE"}
    old_actions = {
        obj.animation_data.action.name for obj in collection.objects if obj.animation_data
    }
    bake(bpy.context, [root], {})
    check("重新烘焙后绑定记录被清除", asset.rig_record(root) is None)
    check("重新烘焙后导出记录被清除", asset.export_record(root) is None)
    check(
        "重新烘焙后导出集合被删除",
        asset.rig_collection(root) is None and old_name not in bpy.data.collections,
    )
    check(
        "重新烘焙后旧骨架与网格不再留在场景里", not [o for o in bpy.data.objects if "Rig" in o.name]
    )
    check(
        "重新烘焙后旧骨架数据与动作也被回收",
        not (old_armatures & {a.name for a in bpy.data.armatures})
        and not (old_actions & {a.name for a in bpy.data.actions}),
    )
    check("重新烘焙后绑定状态为未完成", rig_status(root, scene).state == NONE)
    try:
        export_fbx(bpy.context, root, {"directory": tempfile.gettempdir()})
        refused = False
    except ExportError as error:
        refused = "Build the rig first" in error.message
    check("没有绑定时拒绝导出", refused)
    build_rig(bpy.context, [root], {})
    check("重新构建后恢复", rig_status(root, scene).state == CURRENT)
    from source.tools.Simulation.api import reset as reset_simulation

    reset_simulation(bpy.context, [root])
    check(
        "复位模拟后绑定被清除",
        asset.rig_record(root) is None and asset.rig_collection(root) is None,
    )
    check("复位模拟后没有残留的骨架物体", not [o for o in bpy.data.objects if "Rig" in o.name])


def case_export_files():
    """导出: 文件写出, 覆盖需要确认, 错误情形与现场还原"""
    root, shards = baked_asset(count=8)
    build_rig(bpy.context, [root], {})
    scene = bpy.context.scene
    shard = shards[0]
    bpy.context.view_layer.objects.active = shard
    shard.select_set(True)
    scene.frame_set(9)
    start = scene.frame_start
    scene.frame_end = 80
    with tempfile.TemporaryDirectory() as directory:
        config = {"directory": directory, "engine": "UNREAL"}
        paths, issues = export_fbx(bpy.context, root, config)
        check("写出一个文件", len(paths) == 1 and paths[0].name == "Src.fbx", str(paths))
        check("文件非空", paths[0].stat().st_size > 1000, str(paths[0].stat().st_size))
        check("返回检查结果", isinstance(issues, tuple))
        check("帧范围还原", (scene.frame_start, scene.frame_end) == (start, 80))
        check("当前帧还原", scene.frame_current == 9)
        check(
            "选择与活动物体还原",
            shard.select_get() and bpy.context.view_layer.objects.active == shard,
        )
        check("现有文件被发现", existing_files(root, config) == paths)
        try:
            export_fbx(bpy.context, root, config)
            refused = False
        except ExportError as error:
            refused = "already exists" in error.message
        check("不允许覆盖时拒绝", refused)
        stamp = paths[0].stat().st_mtime_ns
        export_fbx(bpy.context, root, config, overwrite=True)
        check("允许覆盖时重写", paths[0].stat().st_mtime_ns >= stamp)
        record = asset.export_record(root)
        check("导出记录写入文件", record["files"] == [str(paths[0])])
        for label, patch_config, text in (
            (
                "轴向冲突",
                {"engine": "CUSTOM", "axis_forward": "Y", "axis_up": "-Y"},
                "different axes",
            ),
            ("目录为空", {"directory": " "}, "export directory"),
        ):
            try:
                export_fbx(bpy.context, root, {**config, **patch_config}, overwrite=True)
                refused = False
            except ExportError as error:
                refused = text in error.message
            check(f"{label}被拒绝", refused)
    for name in ("UNITY", "GENERIC"):
        with tempfile.TemporaryDirectory() as directory:
            paths, _issues = export_fbx(bpy.context, root, {"directory": directory, "engine": name})
            check(f"{name} 预设能导出", paths[0].is_file())
    try:
        export_fbx(bpy.context, root, {"directory": "//export/"})
        refused = bpy.data.filepath != ""
    except ExportError as error:
        refused = "Save the blend file" in error.message
    check("文件未保存时相对目录被拒绝", refused)


def imported_points(frame, before):
    """导入的网格在 frame 帧求值后的世界顶点"""
    scene = bpy.context.scene
    scene.frame_set(frame)
    depsgraph = bpy.context.evaluated_depsgraph_get()
    points = []
    for obj in bpy.data.objects:
        if obj in before or obj.type != "MESH":
            continue
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        points.extend(evaluated.matrix_world @ vertex.co for vertex in mesh.vertices)
        evaluated.to_mesh_clear()
    return points


def case_round_trip():
    """往返: 导出的 FBX 重新导入, 各帧的世界顶点都能与碎块动画给出的顶点对上"""
    root, shards = baked_asset(count=8, partial=True)
    build_rig(bpy.context, [root], {})
    scene = bpy.context.scene
    with tempfile.TemporaryDirectory() as directory:
        paths, _issues = export_fbx(
            bpy.context, root, {"directory": directory, "engine": "GENERIC"}
        )
        before = set(bpy.data.objects)
        bpy.ops.import_scene.fbx(filepath=str(paths[0]), anim_offset=0.0)
    imported = [obj for obj in bpy.data.objects if obj not in before]
    check(
        "导入了骨架与网格",
        {obj.type for obj in imported} >= {"ARMATURE", "MESH"},
        str(len(imported)),
    )
    frames = [1, 3, 8, 15, 30]
    worst = 0.0
    for frame in frames:
        scene.frame_set(frame)
        expected = [
            shard.matrix_world @ vertex.co for shard in shards for vertex in shard.data.vertices
        ]
        points = imported_points(frame, before)
        tree = KDTree(len(points))
        for index, point in enumerate(points):
            tree.insert(point, index)
        tree.balance()
        gap = max(tree.find(point)[2] for point in expected)
        worst = max(worst, gap)
    check("各帧顶点与碎块动画一致", worst < ROUND_TRIP_TOLERANCE, f"最大偏差 {worst:.2e}")
    action = next((obj.animation_data.action for obj in imported if obj.animation_data), None)
    check("导入的骨架带动画", action is not None)


def case_operators():
    """算子: 经选择解析资产, 构建, 检查与导出; 目标已存在时不覆盖会报错"""
    root, shards = baked_asset(count=8)
    harness.select([shards[0]])
    with tempfile.TemporaryDirectory() as directory:
        bpy.context.scene.qd_settings.game_export.directory = directory
        check("构建算子完成", bpy.ops.qd_game_export.build("EXEC_DEFAULT") == {"FINISHED"})
        check("构建后有绑定记录", asset.rig_record(root) is not None)
        check("检查算子完成", bpy.ops.qd_game_export.check("EXEC_DEFAULT") == {"FINISHED"})
        check("导出算子完成", bpy.ops.qd_game_export.export("EXEC_DEFAULT") == {"FINISHED"})
        check("导出文件存在", (Path(directory) / "Src.fbx").is_file())
        # 算子报告 ERROR 时, 脚本调用会抛出 RuntimeError
        try:
            bpy.ops.qd_game_export.export("EXEC_DEFAULT")
            refused = False
        except RuntimeError as error:
            refused = "already exists" in str(error)
        check("再次导出不覆盖时失败", refused)
        check(
            "允许覆盖时成功",
            bpy.ops.qd_game_export.export("EXEC_DEFAULT", overwrite=True) == {"FINISHED"},
        )
    # 选中导出的网格也能解析到资产
    _armatures, meshes = export_parts(root)
    harness.select([meshes[0]])
    check("选中蒙皮网格也能解析到资产", asset.resolve([meshes[0]]) == [root])


def case_file_names():
    """文件名: 默认用资产名, 自定义名与占位符, 扩展名与非法字符处理, 多骨架序号, 多资产重名"""
    root, _shards = baked_asset(count=12)
    build_rig(bpy.context, [root], {})
    # 导出目录要声明支持 blend 相对路径, 否则界面把默认的 //export/ 标红并警告
    prop = bpy.context.scene.qd_settings.game_export.bl_rna.properties["directory"]
    check("导出目录声明支持 blend 相对路径", prop.is_path_supports_blend_relative)
    with tempfile.TemporaryDirectory() as directory:
        base = {"directory": directory}
        check("默认文件名是资产名", export_paths([root], base)[0].name == "Src.fbx")
        cases = (
            ("自定义名", {"file_name": "Wall_Break"}, "Wall_Break.fbx"),
            ("占位符换成资产名", {"file_name": "{asset}_Fx"}, "Src_Fx.fbx"),
            ("多写的扩展名被去掉", {"file_name": "Wall.FBX"}, "Wall.fbx"),
            ("非法字符换成下划线", {"file_name": "a:b/c?"}, "a_b_c_.fbx"),
            ("空名取默认", {"file_name": "   "}, "Src.fbx"),
        )
        for label, extra, expected in cases:
            names = [path.name for path in export_paths([root], {**base, **extra})]
            check(label, names == [expected], str(names))
        paths, _issues = export_fbx(bpy.context, root, {**base, "file_name": "Custom"})
        check("按自定义名导出文件", paths[0].name == "Custom.fbx" and paths[0].is_file())
        check("导出记录里是自定义名的文件", asset.export_record(root)["files"] == [str(paths[0])])
        build_rig(bpy.context, [root], {"max_bones_total": 6})
        names = [path.name for path in export_paths([root], {**base, "file_name": "Part"})]
        check(
            "多个骨架时加两位序号",
            len(names) > 1 and names[0] == "Part_01.fbx" and names[1] == "Part_02.fbx",
            str(names),
        )
    # 两个资产写到同一个文件名时拒绝, 带占位符时各用各的名字
    first, _s = baked_asset(count=6)
    second_source = cube("Other", half=1.0, location=(6.0, 0.0, 1.0))
    seeds(second_source, 6, 0.9, 3)
    result = generate_fragments(
        bpy.context, [second_source], {**FRACTURE, "collection_name": "Out2"}
    )
    second = result.source_to_asset[second_source]
    apply_physics(bpy.context, [second], {"material": "CONCRETE", "activation": "FROZEN"})
    bake(bpy.context, [first, second], {})
    build_rig(bpy.context, [first, second], {})
    with tempfile.TemporaryDirectory() as directory:
        try:
            export_paths([first, second], {"directory": directory, "file_name": "Same"})
            refused = False
        except ExportError as error:
            refused = "same file" in error.message
        check("不同资产写到同一个文件名时被拒绝", refused)
        names = sorted(
            path.name for path in export_paths([first, second], {"directory": directory})
        )
        check("默认文件名各用各的资产名", names == ["Other.fbx", "Src.fbx"], str(names))


def main():
    """注册插件后运行全部场景"""
    registration.register()
    harness.run_cases(CASES)


CASES = (
    case_build_basics,
    case_build_all_moving,
    case_bone_limits,
    case_bone_name_fallback,
    case_interior_uv,
    case_failure_rollback,
    case_no_simulation,
    case_status,
    case_export_files,
    case_round_trip,
    case_operators,
    case_file_names,
)

if __name__ == "__main__":
    main()
