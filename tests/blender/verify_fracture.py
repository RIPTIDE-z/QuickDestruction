"""MeshFracture 几何回归测试 - 在后台 Blender 里直接调用阶段 API, 核对切割结果的不变量

测试目的:
    1. 体积守恒: 不同尺度, 非等轴权重, 两轮递归, 凹体加孤岛拆分后碎块体积之和等于源体积
    2. 可复现: 同一输入两次切割的几何完全一致
    3. 断面收尾: Interior 顶点组, 锐边, 隐藏面清理与断面材质索引
    4. 命名: 多源输出不重名, 名字与组集合名带源物体名, 超长中文源名也不会撞名
    5. 失败处理: 注入异常后本次新建的数据全部回收, 源物体状态还原
    6. 输入边界: 碎块硬上限与未知参数被拒绝, 链接库网格只在标记断面时被拒绝
    7. 重设原点: 碎块原点等于切割时顶点的平均位置, 世界几何不动
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

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from source.common.errors import InvalidParameterError  # noqa: E402
from source.tools.MeshFracture.api import generate_fragments  # noqa: E402
from source.tools.MeshFracture.core import mesh_ops, pipeline  # noqa: E402
from source.tools.MeshFracture.errors import MeshFractureError  # noqa: E402

FAILURES = []
# 子物体顶点做种子, 关闭噪波与内缩, 其余按场景覆盖
BASE = {"source": {"VERT_CHILD"}, "source_limit": 0, "source_noise": 0.0, "margin": 0.0}
# 体积比允许的偏差: 凸包阶段的抖动与焊接带来约 1e-5 的误差
VOLUME_TOLERANCE = 1e-4


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
        "use_sharp_edges_apply": False,
        "material_index": 1,
    }
    shards = cut([source], config).shards
    grouped = all(shard.vertex_groups.get("Interior") for shard in shards)
    sharp = sum(edge.use_edge_sharp for shard in shards for edge in shard.data.edges)
    hidden = sum(face.hide for shard in shards for face in shard.data.polygons)
    inner = sum(face.material_index == 1 for shard in shards for face in shard.data.polygons)
    check("每块都有 Interior 顶点组", grouped)
    check("内外交界标记了锐边", sharp > 0, str(sharp))
    check("收尾后没有隐藏面", hidden == 0, str(hidden))
    check("断面使用材质索引 1", inner > 0, str(inner))


def case_naming():
    """多源与超长中文源名不重名, 名字与组集合名带源物体名"""
    reset()
    long_name = "非常长的中文源物体名字用来检查字节截断"
    sources = [cube("A"), cube("B", location=(5.0, 0.0, 0.0)), cube(long_name, location=(10, 0, 0))]
    result = cut(sources, {"recursion": 1}, "Shards")
    names = [shard.name for shard in result.shards]
    groups = [coll.name for coll in bpy.data.collections if "_L1_G" in coll.name]
    check("多源碎块没有重名后缀", not any("." in name for name in names), str(names[:3]))
    check(
        "碎块名带源物体名",
        all(name.startswith(("cell_A_", "cell_B_", "cell_非常")) for name in names),
    )
    check("组集合没有重名后缀", groups and not any("." in name for name in groups), str(groups[:3]))
    output = bpy.data.collections["Shards"]
    inside = {output, *output.children}
    check(
        "碎块都落在 collection_name 指定的集合里",
        all(s.users_collection[0] in inside for s in result.shards),
    )
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
