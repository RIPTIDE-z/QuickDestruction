"""MeshFracture 几何回归测试 - 在后台 Blender 里直接调用阶段 API, 核对切割结果的不变量

测试目的:
    1. 体积守恒: 不同尺度, 非等轴权重, 两轮递归, 凹体加孤岛拆分后碎块体积之和等于源体积
    2. 可复现: 同一输入两次切割的几何完全一致
    3. 断面收尾: Interior 顶点组, 锐边, 隐藏面清理与断面材质索引, 标记锐边后碎块仍然封闭
    4. 命名: 多源输出不重名, 名字由源物体名与逐层序号组成, 每个源物体一个集合且碎块集中在 <源>_Shards,
       超长中文源名也不会撞名
    5. 失败处理: 注入异常后本次新建的数据全部回收, 源物体状态还原
    6. 输入边界: 碎块硬上限与未知参数被拒绝, 链接库网格只在标记断面时被拒绝
    7. 重设原点: 碎块原点等于切割时顶点的平均位置, 世界几何不动
    8. 保留父碎块: 2 到 5 轮递归下父块只被切一次, 按层移进 Src_Parents_L<层>, 名字能从路径对应到父级,
       最终碎块与删除父块时一致
    9. 碎块总数上限: 放不下整轮细分时总数不超过上限; 平均细分时每块都被细分, 大碎块优先时被切的正是最大的几块
    10. 点接触的部分: 默认参数切割猴头, 封闭父块的体积都被子块接住, 没有没有面的空碎块
测试过程:
    每个场景先清空数据, 建立源物体与子物体种子, 经 MeshFracture.api.generate_fragments 切割,
    不注册插件界面; 每条判据输出 [PASS] 或 [FAIL]
运行方式 (仓库根目录, 需要 -b 后台模式):
    <Blender 可执行文件> -b --factory-startup --python-exit-code 1 --python tests\\blender\\verify_fracture.py
实测环境: Blender 5.2.2 (Python 3.13) 与 Blender 4.2.23 (Python 3.11)
"""

import random
import sys
import tempfile
import traceback
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from source.common import shards as shard_contract  # noqa: E402
from source.common.errors import InvalidParameterError  # noqa: E402
from source.tools.MeshFracture.api import generate_fragments  # noqa: E402
from source.tools.MeshFracture.core import mesh_ops, pipeline  # noqa: E402
from source.tools.MeshFracture.errors import MeshFractureError  # noqa: E402

FAILURES = []
# 子物体顶点做种子, 关闭种子扰动与内缩, 其余按场景覆盖
BASE = {"source": {"VERT_CHILD"}, "source_limit": 0, "source_noise": 0.0, "margin": 0.0}
# 体积比允许的偏差: 凸包阶段的抖动与焊接带来约 1e-5 的误差
VOLUME_TOLERANCE = 1e-4
# 默认参数切割猴头时单个父块允许丢失的体积: 高于切面内缩造成的缝隙, 远低于求交出错时的损失
TOUCH_LOSS_LIMIT = 0.005


def check(label, condition, detail=""):
    """记录一条必须成立的判据"""
    print(f"[{'PASS' if condition else 'FAIL'}] {label}{f' | {detail}' if detail else ''}")
    if not condition:
        FAILURES.append(label)


def reset():
    """清空物体, 网格, 材质, 集合与链接库, 活动集合设回场景根集合"""
    for kind in ("objects", "meshes", "materials", "collections", "libraries"):
        data = getattr(bpy.data, kind)
        for item in list(data):
            data.remove(item)
    layer = bpy.context.view_layer
    layer.active_layer_collection = layer.layer_collection


def cube(name, half=1.0, location=(0.0, 0.0, 0.0)):
    """边长 2*half 的立方体物体"""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0 * half)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    obj.location = location
    bpy.context.collection.objects.link(obj)
    return obj


def seeds(parent, count, spread, seed):
    """在父物体局部空间放一组随机种子点, 作为 VERT_CHILD 来源"""
    rng = random.Random(seed)
    points = [tuple(rng.uniform(-spread, spread) for _ in range(3)) for _ in range(count)]
    mesh = bpy.data.meshes.new(parent.name + "_seeds")
    mesh.from_pydata(points, [], [])
    obj = bpy.data.objects.new(parent.name + "_seeds", mesh)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    return obj


def cut(sources, config, collection=""):
    """经阶段 API 切割; 输出集合名默认为空, 即当前集合"""
    return generate_fragments(bpy.context, sources, {**config, "collection_name": collection})


def volume(obj):
    """物体在世界空间的网格体积"""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    result = bm.calc_volume(signed=False)
    bm.free()
    return result


def volume_ratio(result, expected):
    """碎块体积之和与期望体积之比"""
    return sum(volume(shard) for shard in result.shards) / expected


def fingerprint(result):
    """碎块世界坐标的有序列表, 用于可复现性比较"""
    return sorted(
        tuple(
            round(value, 7)
            for vertex in shard.data.vertices
            for value in shard.matrix_world @ vertex.co
        )
        for shard in result.shards
    )


def case_volume():
    """尺度, 非等轴权重与递归下的体积守恒"""
    for half in (0.01, 1.0, 100.0):
        reset()
        source = cube("Src", half)
        seeds(source, 30, 0.9 * half, 11)
        ratio = volume_ratio(cut([source], BASE), 8 * half**3)
        check(f"体积守恒 半边长 {half:g}", abs(ratio - 1) < VOLUME_TOLERANCE, f"{ratio:.6f}")
    reset()
    source = cube("Src")
    seeds(source, 30, 0.9, 12)
    config = {**BASE, "cell_scale": (0.2, 0.5, 1.0), "recursion": 0}
    ratio = volume_ratio(cut([source], config), 8.0)
    check("非等轴权重体积守恒", abs(ratio - 1) < VOLUME_TOLERANCE, f"{ratio:.6f}")
    reset()
    source = cube("Src")
    seeds(source, 12, 0.9, 8)
    config = {**BASE, "recursion": 2, "recursion_source_limit": 4, "recursion_clamp": 0}
    result = cut([source], config, "Out")
    ratio = volume_ratio(result, 8.0)
    check(
        "两轮递归体积守恒",
        abs(ratio - 1) < VOLUME_TOLERANCE,
        f"{len(result.shards)} 块 {ratio:.6f}",
    )


def case_torus():
    """凹体加孤岛拆分与递归"""
    reset()
    bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.35)
    source = bpy.context.active_object
    expected = volume(source)
    seeds(source, 16, 1.2, 9)
    config = {**BASE, "recursion": 1, "recursion_source_limit": 4, "use_island_split": True}
    result = cut([source], config, "Out")
    ratio = volume_ratio(result, expected)
    check(
        "圆环孤岛体积守恒",
        abs(ratio - 1) < VOLUME_TOLERANCE,
        f"{len(result.shards)} 块 {ratio:.6f}",
    )


def case_reproducible():
    """同一输入两次切割的几何一致"""
    prints = []
    for _run in range(2):
        reset()
        source = cube("Src")
        seeds(source, 20, 0.9, 3)
        config = {**BASE, "source_noise": 0.4, "recursion": 1, "recursion_chance": 0.5}
        prints.append(fingerprint(cut([source], config, "Out")))
    check("两次切割几何一致", prints[0] == prints[1], f"{len(prints[0])} 块")


def case_interior():
    """断面顶点组, 锐边, 隐藏面清理与断面材质"""
    reset()
    source = cube("Src")
    for name in ("Outer", "Inner"):
        source.data.materials.append(bpy.data.materials.new(name))
    seeds(source, 12, 0.9, 5)
    config = {
        **BASE,
        "recursion": 0,
        "use_interior_vgroup": True,
        "use_sharp_edges": True,
        "material_index": 1,
    }
    shards = cut([source], config).shards
    grouped = all(shard.vertex_groups.get("Interior") for shard in shards)
    sharp = sum(edge.use_edge_sharp for shard in shards for edge in shard.data.edges)
    hidden = sum(face.hide for shard in shards for face in shard.data.polygons)
    inner = sum(face.material_index == 1 for shard in shards for face in shard.data.polygons)
    closed = sum(closed_mesh(shard) for shard in shards)
    check("每块都有 Interior 顶点组", grouped)
    check("内外交界标记了锐边", sharp > 0, str(sharp))
    check("收尾后没有隐藏面", hidden == 0, str(hidden))
    check("断面使用材质索引 1", inner > 0, str(inner))
    check("标记锐边后碎块仍然封闭", closed == len(shards), f"{closed}/{len(shards)}")


def closed_mesh(obj):
    """网格没有开放边与非流形边"""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    result = all(edge.is_manifold for edge in bm.edges)
    bm.free()
    return result


def keep_parents_run(rounds, remove):
    """3 个种子, 每个父块 2 个种子, 按给定轮数递归, 返回切割结果"""
    reset()
    source = cube("Src")
    seeds(source, 3, 0.9, 21)
    config = {
        **BASE,
        "recursion": rounds,
        "recursion_source_limit": 2,
        "recursion_clamp": 0,
        "use_keep_parents": not remove,
    }
    return cut([source], config, "Out")


def expected_name(obj):
    """按路径推出的名字: 前缀, 源物体名, 每层三位序号"""
    return "_".join(("cell", "Src", *(f"{index:03d}" for index in obj[shard_contract.PATH_MARK])))


def check_parents(rounds, result, parents):
    """父碎块按层收纳且已收尾, 不在结果里, 最终碎块都在 Src_Shards; 名字能从路径对应到父级"""
    leaves = set(result.shards)
    everything = (*leaves, *parents)
    root = bpy.data.collections.get("Src_Parents")
    root = root if root and root.name in bpy.data.collections["Src"].children else None
    placed = (
        all(
            obj.users_collection == (bpy.data.collections.get(f"Src_Parents_L{obj['_qd_level']}"),)
            and obj.users_collection[0].name in root.children
            for obj in parents
        )
        if root
        else False
    )
    kept = {tuple(obj[shard_contract.PATH_MARK]) for obj in parents}
    referenced = {shard_contract.parent_path(obj) for obj in everything} - {()}
    shards_collection = bpy.data.collections.get("Src_Shards")
    collected = shards_collection.name in bpy.data.collections["Src"].children and all(
        obj.users_collection == (shards_collection,) for obj in leaves
    )
    check(f"{rounds} 轮 父碎块不在结果里", not leaves & set(parents))
    check(f"{rounds} 轮 父碎块按层放在 Src_Parents_L<层>", placed)
    check(
        f"{rounds} 轮 每个父块都有子块, 每个子块的父块都被保留",
        kept == referenced,
        f"{len(kept)} 个父块",
    )
    check(f"{rounds} 轮 最终碎块都在 Src_Shards 里", collected)
    check(
        f"{rounds} 轮 父碎块已收尾",
        not any(face.hide for obj in parents for face in obj.data.polygons),
    )
    names = [obj.name for obj in everything]
    check(
        f"{rounds} 轮 名字由路径决定且不重复",
        all(obj.name == expected_name(obj) for obj in everything),
        str(sorted(names)[:3]),
    )
    check(
        f"{rounds} 轮 碎块契约不含父碎块",
        set(shard_contract.shards_of(bpy.data.objects, "Src")) == leaves,
    )


def case_keep_parents():
    """开启保留父碎块: 父块只切一次并按层移进父碎块集合, 最终碎块与删除父块时一致"""
    for rounds in (2, 3, 4, 5):
        removed = fingerprint(keep_parents_run(rounds, True))
        result = keep_parents_run(rounds, False)
        parents = [obj for obj in bpy.data.objects if obj.get(shard_contract.PARENT_MARK)]
        levels = sorted({shard[shard_contract.LEVEL_MARK] for shard in result.shards})
        ratio = volume_ratio(result, 8.0)
        print(
            f"   {rounds} 轮: {len(result.shards)} 块碎块, {len(parents)} 个父碎块, 层号 {levels}"
        )
        check(f"{rounds} 轮 碎块体积守恒", abs(ratio - 1) < VOLUME_TOLERANCE, f"{ratio:.6f}")
        check(f"{rounds} 轮 碎块与删除父块时一致", fingerprint(result) == removed)
        # 首次切割是第 1 层, 每轮递归加一层
        check(f"{rounds} 轮 递归到第 {rounds + 1} 层", levels[-1] == rounds + 1, str(levels))
        check_parents(rounds, result, parents)


def diagonal(obj):
    """世界空间包围盒对角线长度"""
    corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
    return (corners[0] - corners[6]).length


def max_shards_run(priority):
    """30 个种子首次切成 30 块, 每块最多切 8 块, 上限 80 放不下整轮细分; 保留父碎块便于检查"""
    reset()
    source = cube("Src")
    seeds(source, 30, 0.95, 7)
    config = {
        **BASE,
        "recursion": 1,
        "recursion_source_limit": 8,
        "recursion_clamp": 80,
        "recursion_chance_select": priority,
        "use_keep_parents": True,
    }
    result = cut([source], config, "Out")
    parents = [obj for obj in bpy.data.objects if obj.get(shard_contract.PARENT_MARK)]
    first = [obj for obj in (*result.shards, *parents) if obj[shard_contract.LEVEL_MARK] == 1]
    return result, parents, first


def case_max_shards():
    """碎块总数上限: 整轮放不下时按细分优先级规划, 总数不超过上限"""
    result, parents, first = max_shards_run("EVEN")
    count = len(result.shards)
    print(f"   平均细分: 首次 {len(first)} 块, 细分 {len(parents)} 块, 最终 {count} 块")
    check("平均细分 总数不超过上限", count <= 80, str(count))
    check(
        "平均细分 首次切割的每块都被细分",
        len(parents) == len(first),
        f"{len(parents)}/{len(first)}",
    )
    ratio = volume_ratio(result, 8.0)
    check("平均细分 体积守恒", abs(ratio - 1) < VOLUME_TOLERANCE, f"{ratio:.6f}")
    result, parents, first = max_shards_run("SIZE_MAX")
    count = len(result.shards)
    untouched = [obj for obj in first if obj not in parents]
    print(f"   大碎块优先: 首次 {len(first)} 块, 细分 {len(parents)} 块, 最终 {count} 块")
    check("大碎块优先 总数不超过上限", count <= 80, str(count))
    check(
        "大碎块优先 被细分的正是最大的几块",
        parents and untouched and min(map(diagonal, parents)) >= max(map(diagonal, untouched)),
        f"细分 {len(parents)} 块",
    )


def case_touching_parts():
    """点接触的部分: 只在顶点处相接的两部分会被拆开, 下一轮 Exact 求交不再丢掉大片几何"""
    reset()
    bpy.ops.mesh.primitive_monkey_add()
    source = bpy.context.active_object
    result = cut([source], {"use_keep_parents": True}, "Out")
    parents = [obj for obj in bpy.data.objects if obj.get(shard_contract.PARENT_MARK)]
    children = {}
    for shard in result.shards:
        children.setdefault(shard_contract.parent_path(shard), []).append(shard)
    worst = 0.0
    for parent in parents:
        if not closed_mesh(parent):
            # 开放的父块没有确定的体积, 只比较封闭的
            continue
        kids = children.get(tuple(parent[shard_contract.PATH_MARK]), [])
        worst = max(worst, volume(parent) - sum(map(volume, kids)))
    empty = [shard.name for shard in result.shards if not shard.data.polygons]
    print(
        f"   {len(result.shards)} 块碎块, {len(parents)} 个父块, 单个父块最多丢失体积 {worst:.4f}"
    )
    # 切面内缩留出的缝隙每块约丢 0.001 量级; 出错时曾一次丢掉 0.0437, 约为父块的一半
    check("封闭父块的体积被子块接住", worst < TOUCH_LOSS_LIMIT, f"{worst:.4f}")
    check("没有空碎块", not empty, str(empty[:3]))


def case_naming():
    """多源与超长中文源名不重名, 名字带源物体名与路径, 碎块集中在各源物体的 <源>_Shards 里"""
    reset()
    long_name = "非常长的中文源物体名字用来检查字节截断"
    sources = [cube("A"), cube("B", location=(5.0, 0.0, 0.0)), cube(long_name, location=(10, 0, 0))]
    result = cut(sources, {"recursion": 1}, "Out")
    names = [shard.name for shard in result.shards]
    groups = [coll.name for coll in bpy.data.collections if coll.name != "Out"]
    check("多源碎块没有重名后缀", not any("." in name for name in names), str(names[:3]))
    check(
        "碎块名带源物体名与两层序号",
        all(
            name.startswith(("cell_A_", "cell_B_", "cell_非常")) and name.count("_") >= 3
            for name in names
        ),
    )
    check("集合没有重名后缀", groups and not any("." in name for name in groups), str(groups))
    output = bpy.data.collections["Out"]

    def placed(shard):
        """碎块只在 输出集合/<源>/<源>_Shards 里, <源> 是按字节截短的源物体名"""
        label = shard["_qd_source"].encode("utf-8")[:32].decode("utf-8", "ignore")
        holder = bpy.data.collections.get(f"{label}_Shards")
        return (
            label in output.children
            and holder is not None
            and holder.name in output.children[label].children
            and shard.users_collection == (holder,)
        )

    check("碎块都在各自源物体的 <源>_Shards 里", all(map(placed, result.shards)))
    marks = {shard["_qd_source"] for shard in result.shards}
    check("来源标记记录完整源名", marks == {"A", "B", long_name}, str(marks))


def case_rollback():
    """收尾阶段抛错时回收本次新建的数据, 源物体状态还原"""
    reset()
    source = cube("Src")
    seeds(source, 10, 0.9, 10)
    source.data.polygons[0].hide = True
    before = {
        kind: set(getattr(bpy.data, kind).keys()) for kind in ("objects", "meshes", "collections")
    }
    original = mesh_ops.finish_interior

    def boom(*_args):
        """模拟收尾阶段的意外错误"""
        raise RuntimeError("injected failure")

    mesh_ops.finish_interior = boom
    try:
        config = {**BASE, "recursion": 1, "recursion_source_limit": 4, "use_debug_redraw": True}
        cut([source], config, "RollbackOut")
        check("注入的异常被抛出", False)
    except RuntimeError:
        after = {kind: set(getattr(bpy.data, kind).keys()) for kind in before}
        check("新建数据全部回收", after == before, str({k: after[k] - before[k] for k in after}))
        check("源物体面隐藏状态还原", source.data.polygons[0].hide)
        check("源物体显示方式还原", source.display_type == "TEXTURED")
    finally:
        mesh_ops.finish_interior = original


def expect_error(label, error_type, call):
    """调用必须抛出指定类型的异常"""
    try:
        call()
        check(label, False, "没有抛错")
    except error_type as error:
        check(label, True, repr(error))


def case_limits():
    """碎块硬上限与未知参数被拒绝; 链接库网格只在需要改写隐藏标记时被拒绝"""
    reset()
    source = cube("Src")
    seeds(source, 12, 0.9, 7)
    # 上限只由流程编排层负责, 临时调小这一处即可
    original = pipeline.SHARD_LIMIT
    pipeline.SHARD_LIMIT = 20
    try:
        config = {**BASE, "recursion": 3, "recursion_clamp": 0}
        expect_error(
            "超过硬上限时报错",
            MeshFractureError,
            lambda: cut([source], config, "Out"),
        )
        check("超限后没有遗留碎块", not [obj for obj in bpy.data.objects if obj.get("_qd_source")])
    finally:
        pipeline.SHARD_LIMIT = original
    expect_error(
        "未知参数被拒绝",
        InvalidParameterError,
        lambda: cut([source], {"recursio": 3}),
    )
    with tempfile.TemporaryDirectory() as directory:
        path = str(Path(directory) / "library.blend")
        bpy.data.libraries.write(path, {cube("Linked").data})
        with bpy.data.libraries.load(path, link=True) as (_source_data, target):
            target.meshes = ["Linked"]
        linked = bpy.data.objects.new("UsesLinked", target.meshes[0])
        bpy.context.collection.objects.link(linked)
        expect_error(
            "标记断面时链接库网格被拒绝",
            MeshFractureError,
            lambda: cut([linked], BASE),
        )
        # 不标记断面时布尔只读取源网格, 链接库网格也能切割
        plain = {"recursion": 0, "use_sharp_edges": False, "use_interior_vgroup": False}
        shards = cut([linked], plain).shards
        check("不标记断面时链接库网格可以切割", len(shards) > 0, f"{len(shards)} 块")


def case_recenter():
    """重设原点: 原点位于顶点平均位置, 世界几何不动"""
    reset()
    source = cube("Src")
    seeds(source, 12, 0.9, 4)
    # 保留布尔修改器时没有面合并与断面收尾, 胞体网格就是重设原点时的顶点
    config = {**BASE, "recursion": 0, "use_debug_bool": True}
    moved = cut([source], config).shards
    offsets = []
    for shard in moved:
        flat = [0.0] * (len(shard.data.vertices) * 3)
        shard.data.vertices.foreach_get("co", flat)
        offsets.extend(abs(sum(flat[axis::3])) / len(shard.data.vertices) for axis in range(3))
    check("原点位于顶点平均位置", max(offsets) < 1e-6, f"{max(offsets):.2e}")
    world = fingerprint_bounds(moved)
    kept = cut([source], {**config, "use_recenter": False})
    check("重设原点不移动世界几何", close(world, fingerprint_bounds(kept.shards)))


def fingerprint_bounds(shards):
    """每块碎块世界包围盒的有序列表"""
    boxes = []
    for shard in shards:
        points = [shard.matrix_world @ vertex.co for vertex in shard.data.vertices]
        boxes.append(tuple(f(p[i] for p in points) for f in (min, max) for i in range(3)))
    return sorted(boxes)


def close(first, second):
    """两组包围盒在 1e-6 内一致"""
    return len(first) == len(second) and all(
        abs(a - b) < 1e-6
        for p, q in zip(first, second, strict=True)
        for a, b in zip(p, q, strict=True)
    )


CASES = (
    case_volume,
    case_torus,
    case_reproducible,
    case_interior,
    case_naming,
    case_rollback,
    case_limits,
    case_recenter,
    case_keep_parents,
    case_max_shards,
    case_touching_parts,
)


def main():
    """逐个运行场景, 单个场景出错记为失败但不影响其余场景"""
    print(f"Blender {bpy.app.version_string} Python {sys.version.split()[0]}")
    for case in CASES:
        print(f"== {case.__doc__}")
        try:
            case()
        except Exception:
            traceback.print_exc()
            check(f"{case.__name__} 执行完成", False)
    print("ALL PASS" if not FAILURES else f"失败 {len(FAILURES)} 项: {FAILURES}")
    if FAILURES:
        sys.exit(1)


main()
