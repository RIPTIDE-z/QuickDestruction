"""流程测速 - 在后台 Blender 里按几种碎块规模跑完 切割 -> 应用物理 -> 爆炸驱动 -> 烘焙, 输出各阶段耗时

用途: 开发辅助, 不是测试; 每个里程碑跑一次, 记录在实现追踪文档里, 用来发现性能退化
做法: 立方体加一组随机子物体顶点作为种子, 不递归; 爆炸驱动放在立方体中心, 范围覆盖约一半碎块;
     冻结方式, 有重力, 场景帧范围 1 到 60
运行方式 (仓库根目录):
    <Blender 可执行文件> -b --factory-startup --python-exit-code 1 --python scripts\\bench_pipeline.py -- [种子数 ...]
输出: 每个规模一行 [BENCH], 最后一行 BENCH_DONE
"""

import random
import sys
from pathlib import Path
from time import perf_counter

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from source import registration  # noqa: E402
from source.common import drivers  # noqa: E402
from source.tools.Drivers.api import add_driver  # noqa: E402
from source.tools.MeshFracture.api import generate_fragments  # noqa: E402
from source.tools.Physics.api import apply_physics  # noqa: E402
from source.tools.Simulation.api import bake  # noqa: E402

# 默认测量的种子数
DEFAULT_SIZES = (100, 300, 1000)
# 烘焙的帧数
FRAMES = 60


def clear():
    """清空物体, 网格, 集合与动作"""
    for kind in ("objects", "meshes", "collections", "actions"):
        data = getattr(bpy.data, kind)
        for item in list(data):
            data.remove(item)


def source_with_seeds(count):
    """边长 4 的立方体, 带一个由 count 个随机点组成的子物体作为种子来源"""
    bpy.ops.mesh.primitive_cube_add(size=4.0, location=(0.0, 0.0, 2.0))
    source = bpy.context.active_object
    rng = random.Random(count)
    points = [tuple(rng.uniform(-1.9, 1.9) for _axis in range(3)) for _index in range(count)]
    mesh = bpy.data.meshes.new("Seeds")
    mesh.from_pydata(points, [], [])
    seeds = bpy.data.objects.new("Seeds", mesh)
    bpy.context.collection.objects.link(seeds)
    seeds.parent = source
    return source


def measure(count):
    """一个规模的完整流程, 返回各阶段耗时与碎块数"""
    clear()
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, FRAMES
    source = source_with_seeds(count)
    config = {"source": {"VERT_CHILD"}, "first_cut_seeds": 0, "recursion": 0, "recursion_clamp": 0}
    started = perf_counter()
    result = generate_fragments(bpy.context, [source], config)
    fracture = perf_counter() - started
    root = result.source_to_asset[source]
    started = perf_counter()
    apply_physics(bpy.context, [root], {"material": "CONCRETE"})
    physics = perf_counter() - started
    driver = add_driver(bpy.context, drivers.EXPLOSION, location=(0.0, 0.0, 2.0), frame=5)
    driver.scale = (2.0, 2.0, 2.0)
    bpy.context.view_layer.update()
    baked = bake(bpy.context, [root], {})
    return {
        "seeds": count,
        "shards": len(result.shards),
        "fracture_s": round(fracture, 2),
        "physics_s": round(physics, 2),
        "bake_s": round(baked.seconds, 2),
        "bake_ms_per_frame": round(baked.seconds / FRAMES * 1000, 1),
        "moved": baked.moved,
    }


def main():
    """注册插件后按给定规模依次测量"""
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    sizes = [int(value) for value in argv] or list(DEFAULT_SIZES)
    registration.register()
    print(f"Blender {bpy.app.version_string}")
    for count in sizes:
        print("[BENCH]", measure(count), flush=True)
    print("BENCH_DONE")


main()
