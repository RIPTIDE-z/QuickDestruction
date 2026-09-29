"""Blender 后台测试的公共部分 - 判据记录, 场景清理, 建立测试物体与逐个运行场景

用法: 测试脚本先把本目录加入 sys.path 再导入本模块
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import harness
本模块导入时会把仓库根目录加入 sys.path, 之后可以直接导入 source 下的模块
"""

import random
import sys
import traceback
from pathlib import Path

import bmesh
import bpy

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# 不成立的判据
FAILURES = []


def check(label, condition, detail=""):
    """记录一条必须成立的判据, 输出 [PASS] 或 [FAIL]"""
    print(f"[{'PASS' if condition else 'FAIL'}] {label}{f' | {detail}' if detail else ''}")
    if not condition:
        FAILURES.append(label)


def reset():
    """清空物体, 网格, 材质, 集合, 曲线, 注解与链接库, 活动集合设回场景根集合"""
    for kind in ("objects", "meshes", "materials", "collections", "curves", "libraries"):
        data = getattr(bpy.data, kind)
        for item in list(data):
            data.remove(item)
    for kind in ("annotations", "grease_pencils", "particles"):
        data = getattr(bpy.data, kind, ())
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


def volume(obj):
    """物体在世界空间的网格体积"""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    result = bm.calc_volume(signed=False)
    bm.free()
    return result


def select(objects, active=None):
    """只选中给定的物体, 并设置活动物体"""
    for obj in bpy.data.objects:
        obj.select_set(False)
    for obj in objects:
        obj.select_set(True)
    bpy.context.view_layer.objects.active = active or (objects[0] if objects else None)


def run_cases(cases):
    """逐个运行场景, 单个场景出错记为失败但不影响其余场景; 有失败时以非零状态结束"""
    print(f"Blender {bpy.app.version_string} Python {sys.version.split()[0]}")
    for case in cases:
        print(f"== {case.__doc__}")
        try:
            case()
        except Exception:
            traceback.print_exc()
            check(f"{case.__name__} 执行完成", False)
    print("ALL PASS" if not FAILURES else f"失败 {len(FAILURES)} 项: {FAILURES}")
    if FAILURES:
        sys.exit(1)
