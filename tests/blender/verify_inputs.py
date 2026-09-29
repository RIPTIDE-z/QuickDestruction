"""MeshFracture 输入测试 - 源网格体检与各种切割种子来源

测试目的:
    1. 体检: 封闭且不自相交的网格没有问题; 开放, 点接触与带散点的网格报不封闭; 互相穿插的网格报自相交;
       体检按求值后的网格判断, 只列出有问题的网格物体
    2. 种子来源: 自身与子物体的顶点, 曲线的求值几何, 自身与子物体的粒子, 注解铅笔, 以及没有任何点时
       回退到自身顶点; 多个来源取并集且顺序固定
测试过程:
    每个场景先清空数据再建立物体, 体检直接调用 source_issues, 种子来源直接调用 sampling.sample_points;
    每条判据输出 [PASS] 或 [FAIL], 有失败时以非零状态结束
运行方式 (仓库根目录, 需要 -b 后台模式):
    <Blender 可执行文件> -b --factory-startup --python-exit-code 1 --python tests\\blender\\verify_inputs.py
实测环境: Blender 5.2.2 (Python 3.13) 与 Blender 4.2.23 (Python 3.11)
"""

import sys
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness
from harness import check, cube, reset

from source.tools.MeshFracture.api import generate_fragments, source_issues
from source.tools.MeshFracture.core import sampling
from source.tools.MeshFracture.core.diagnosis import NOT_CLOSED, SELF_INTERSECTING
from source.tools.MeshFracture.settings import SCHEMA


def primitive(add, **options):
    """用内置基本体建立源物体"""
    add(**options)
    return bpy.context.active_object


def mesh_object(name, build):
    """用 bmesh 构建网格物体; build 接收空 BMesh"""
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    build(bm)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def issues_of(obj):
    """体检结果, 排序后便于比较"""
    bpy.context.view_layer.update()
    return sorted(source_issues(bpy.context, [obj]).get(obj, ()))


def two_cubes(bm):
    """两个互相穿插的封闭立方体"""
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.create_cube(bm, size=2.0, matrix=Matrix.Translation((0.5, 0.5, 0.5)))


def touching_cones(bm):
    """两个圆锥的尖端合并成同一个顶点, 只在这个顶点处相接"""
    bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=1, radius2=0, depth=1)
    bmesh.ops.create_cone(
        bm,
        cap_ends=True,
        segments=8,
        radius1=1,
        radius2=0,
        depth=1,
        matrix=Matrix.Translation((0, 0, 1)) @ Matrix.Rotation(3.14159265, 4, "X"),
    )
    bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=1e-6)


def cube_with_loose_vertex(bm):
    """封闭立方体外加一个孤立顶点"""
    bmesh.ops.create_cube(bm, size=2.0)
    bm.verts.new((5.0, 5.0, 5.0))


def case_diagnosis():
    """体检: 干净的网格没有问题, 开放, 点接触, 散点报不封闭, 穿插报自相交"""
    expectations = (
        ("立方体", lambda: cube("Cube"), []),
        ("圆环", lambda: primitive(bpy.ops.mesh.primitive_torus_add), []),
        ("UV 球", lambda: primitive(bpy.ops.mesh.primitive_uv_sphere_add), []),
        ("二十面球", lambda: primitive(bpy.ops.mesh.primitive_ico_sphere_add), []),
        ("平面 (开放)", lambda: primitive(bpy.ops.mesh.primitive_plane_add), [NOT_CLOSED]),
        (
            "猴头 (开放且自相交)",
            lambda: primitive(bpy.ops.mesh.primitive_monkey_add),
            [NOT_CLOSED, SELF_INTERSECTING],
        ),
        ("两个穿插的立方体", lambda: mesh_object("Two", two_cubes), [SELF_INTERSECTING]),
        ("尖端相接的两个圆锥", lambda: mesh_object("Cones", touching_cones), [NOT_CLOSED]),
        ("立方体加散点", lambda: mesh_object("Loose", cube_with_loose_vertex), [NOT_CLOSED]),
    )
    for label, build, expected in expectations:
        reset()
        found = issues_of(build())
        check(f"体检 {label}", found == sorted(expected), f"得到 {found}, 期望 {sorted(expected)}")


def case_diagnosis_uses_evaluated_mesh():
    """体检按求值后的网格判断, 只列出有问题的网格物体, 忽略非网格物体"""
    reset()
    source = cube("Array")
    modifier = source.modifiers.new("array", "ARRAY")
    modifier.count = 2
    modifier.relative_offset_displace = (0.5, 0.0, 0.0)
    clean = cube("Clean", location=(10.0, 0.0, 0.0))
    empty = bpy.data.objects.new("Empty", None)
    bpy.context.collection.objects.link(empty)
    bpy.context.view_layer.update()
    found = source_issues(bpy.context, [source, clean, empty])
    check(
        "阵列修改器让求值后的网格自相交",
        found.get(source) == {SELF_INTERSECTING},
        str(found.get(source)),
    )
    check(
        "干净的立方体与空物体不出现在结果里",
        set(found) == {source},
        str(sorted(o.name for o in found)),
    )


def settings_for(*sources):
    """只指定种子来源的参数值"""
    return SCHEMA.resolve({"source": set(sources)})


def points(obj, *sources):
    """按来源采集到的候选点"""
    bpy.context.view_layer.update()
    return sampling.sample_points(bpy.context, obj, settings_for(*sources))


def child_of(parent, name, vertices):
    """给 parent 加一个带指定顶点的网格子物体"""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], [])
    child = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(child)
    child.parent = parent
    return child


def add_particles(obj, count):
    """给物体加一个粒子系统, 第一帧就有 count 个粒子 (含还没出生的)"""
    obj.modifiers.new("particles", "PARTICLE_SYSTEM")
    obj.particle_systems[0].settings.count = count
    bpy.context.scene.frame_set(1)


def case_vertex_sources():
    """顶点来源: 世界空间, 修改器之前, 只取直接子物体, 曲线取求值几何, 多来源取并集"""
    reset()
    source = cube("Src", location=(3.0, 0.0, 0.0))
    source.modifiers.new("subsurf", "SUBSURF")
    child = child_of(source, "Child", [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0)])
    child_of(child, "Grandchild", [(9.0, 9.0, 9.0)])
    own = points(source, "VERT_OWN")
    check("自身顶点是修改器之前的 8 个顶点", len(own) == 8, str(len(own)))
    check(
        "坐标在世界空间", all(2.0 <= p.x <= 4.0 for p in own), str([round(p.x, 2) for p in own][:4])
    )
    children = points(source, "VERT_CHILD")
    check("子物体顶点只取直接子物体", len(children) == 3, str(len(children)))
    both = points(source, "VERT_OWN", "VERT_CHILD")
    check("多来源取并集, 自身在前", len(both) == 11 and both[:8] == own and both[8:] == children)

    reset()
    holder = cube("Holder")
    curve = bpy.data.curves.new("Curve", "CURVE")
    spline = curve.splines.new("POLY")
    spline.points.add(3)
    curve.bevel_depth = 0.1
    curve_object = bpy.data.objects.new("Curve", curve)
    bpy.context.collection.objects.link(curve_object)
    curve_object.parent = holder
    found = points(holder, "VERT_CHILD")
    check("曲线子物体取求值后的几何顶点, 不是 4 个控制点", len(found) > 4, str(len(found)))


def case_particle_sources():
    """粒子来源: 自身与子物体的全部粒子, 包括还没出生的"""
    reset()
    source = cube("Src")
    add_particles(source, 50)
    child = child_of(source, "Emitter", [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)])
    add_particles(child, 30)
    check("自身粒子 50 个", len(points(source, "PARTICLE_OWN")) == 50)
    check("子物体粒子 30 个", len(points(source, "PARTICLE_CHILD")) == 30)
    check("两种粒子取并集", len(points(source, "PARTICLE_OWN", "PARTICLE_CHILD")) == 80)


def add_annotation(frames):
    """场景里建一个注解, 一层, 每个关键帧一笔; frames 是 {帧号: 点数}"""
    scene = bpy.context.scene
    data = bpy.data.annotations if hasattr(bpy.data, "annotations") else bpy.data.grease_pencils
    annotation = data.new("Notes")
    if hasattr(scene, "annotation"):
        scene.annotation = annotation
    else:
        scene.grease_pencil = annotation
    layer = annotation.layers.new("Note")
    for number, count in frames.items():
        stroke = layer.frames.new(number).strokes.new()
        stroke.points.add(count)
        for index, point in enumerate(stroke.points):
            point.co = (0.1 * index, 0.0, 0.0)
    return annotation


def case_annotation_source():
    """注解铅笔: 取当前帧或之前最近一帧的笔画点, 只取活动层, 没有点时回退到自身顶点"""
    reset()
    source = cube("Src")
    annotation = add_annotation({1: 12, 10: 6})
    scene = bpy.context.scene
    for frame, expected in ((1, 12), (5, 12), (10, 6), (20, 6)):
        scene.frame_set(frame)
        found = len(points(source, "PENCIL"))
        check(f"第 {frame} 帧读到 {expected} 个点", found == expected, str(found))
    scene.frame_set(0)
    check("第一个关键帧之前没有点, 回退到自身的 8 个顶点", len(points(source, "PENCIL")) == 8)
    scene.frame_set(5)
    check("与自身顶点取并集", len(points(source, "PENCIL", "VERT_OWN")) == 12 + 8)

    other = annotation.layers.new("Other")
    stroke = other.frames.new(1).strokes.new()
    stroke.points.add(3)
    annotation.layers.active_index = len(annotation.layers) - 1
    check("只取活动层", len(points(source, "PENCIL")) == 3)
    annotation.layers.active_index = 0
    check("切回第一层", len(points(source, "PENCIL")) == 12)

    reset()
    source = cube("Src")
    add_annotation({1: 12})
    scene.frame_set(1)
    result = generate_fragments(
        bpy.context,
        [source],
        {"source": {"PENCIL"}, "source_noise": 0.0, "recursion": 0, "collection_name": ""},
    )
    check("注解的点直接用于切割", 2 <= len(result.shards) <= 12, f"{len(result.shards)} 块")


def case_fallback():
    """没有找到任何点时回退到自身顶点"""
    reset()
    source = cube("Src")
    for kind in ("PARTICLE_OWN", "PARTICLE_CHILD", "VERT_CHILD", "PENCIL"):
        check(f"{kind} 没有点时回退到自身的 8 个顶点", len(points(source, kind)) == 8)


CASES = (
    case_diagnosis,
    case_diagnosis_uses_evaluated_mesh,
    case_vertex_sources,
    case_particle_sources,
    case_annotation_source,
    case_fallback,
)

harness.run_cases(CASES)
