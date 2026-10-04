"""MeshFracture 几何回归测试 - 在后台 Blender 里直接调用阶段 API, 核对切割结果的不变量

测试目的:
    1. 体积守恒: 不同尺度, 非等轴权重, 两轮递归, 凹体加孤岛拆分后碎块体积之和等于源体积
    2. 可复现: 同一输入两次切割的几何完全一致
    3. 断面收尾: Interior 顶点组, 锐边, 隐藏面清理与断面材质索引, 标记锐边后碎块仍然封闭
    4. 命名: 多源输出不重名, 名字由源物体名, 后缀与逐层序号组成,
        每个源物体一个集合且碎块集中在 <源>_Shards,
       超长中文源名也不会撞名
    5. 失败处理: 注入异常后本次新建的数据全部回收, 源物体状态还原
    6. 输入边界: 碎块硬上限与未知参数被拒绝, 链接库网格只在标记断面时被拒绝
    7. 重设原点: 碎块原点等于切割时顶点的平均位置, 世界几何不动
    8. 保留父碎块: 2 到 5 轮递归下父块只被切一次, 按层移进 Src_Parents_L<层>,
        名字能从路径对应到父级,
       最终碎块与删除父块时一致
    9. 碎块总数上限: 放不下整轮细分时总数不超过上限; 平均细分时每块都被细分,
        大碎块优先时被切的正是最大的几块
    10. 点接触的部分: 默认参数切割猴头, 封闭父块的体积都被子块接住, 没有没有面的空碎块
    11. 首次切割种子数: 为 0 时跟随碎块总数上限, 超过总数上限被拒绝, 上限之内的结果不变
    12. 孤岛拆分: 平板状的胞体与圆环相交成两段, 打开时拆成两块, 关闭时留在同一块里
    13. 共面面合并: 网格化的立方体切割后碎块里没有残留的共面边
    14. 种子扰动: 扰动改变结果, 同一随机种子可复现
    15. 材质与数据层: 碎块保留材质槽与链接方式, UV 与颜色层, 断面材质只落在断面上
    16. 布尔策略: 封闭网格不启用容错求解, 越界与非有限坐标的结果被拒绝, 没有面的网格无法切割
    17. 求交结果整理: 焊接重合顶点, 清掉散边散点, 立方体的面保持不变
    18. 依赖图隔离: 递归期间已完成的碎块移出视图层, 结果与不隔离时完全一致,
        结束后不留排除与工作集合,
        中途失败回滚干净, 粒子来源加递归可以运行
    19. 分批切割: 每批父块数不同结果完全一致, 待删数据批量删除后不留孤儿数据, 孤岛都被拆开
    20. 提前预判硬上限: 每轮开始前按上界估算总数, 必然超限时这一轮不动手
    21. 非等轴权重剪枝: 胞体与不剪枝时一致, 并且确实少试了邻居
    22. 资产身份: 每次切割都是新资产, 清单齐全, 从碎块与源物体都能解析到资产; 复制资产, 复制碎块,
        旧版本碎块与未知格式都被拒绝, 重建副本身份后原资产与副本各自可用; 源物体改名后引用仍有效
    23. 资产持久性: 保存并重新打开文件后, 资产标识, 源物体引用与碎块标记都还在
测试过程:
    每个场景先清空数据, 建立源物体与子物体种子, 经 MeshFracture.api.generate_fragments 切割,
    不注册插件界面; 每条判据输出 [PASS] 或 [FAIL]
运行方式 (仓库根目录, 需要 -b 后台模式):
    <Blender 可执行文件> -b --factory-startup --python-exit-code 1 --python tests\\blender\\verify_fracture.py
实测环境: Blender 5.2.2 (Python 3.13)
"""

import random
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness
from harness import check, cube, reset, seeds, select, volume

from source.common import asset, created_data
from source.common import shards as shard_contract
from source.common.blender_context import mesh_edit
from source.common.errors import InvalidParameterError
from source.tools.MeshFracture.api import generate_fragments, make_asset_unique
from source.tools.MeshFracture.core import boolean, cleanup, interior, intersect, pipeline
from source.tools.MeshFracture.core import cells as cell_builder
from source.tools.MeshFracture.core.naming import SOURCE_NAME_BYTES
from source.tools.MeshFracture.errors import IntersectionBoundsError, MeshFractureError

# 子物体顶点做种子, 关闭种子扰动与内缩, 其余按场景覆盖
BASE = {"source": {"VERT_CHILD"}, "first_cut_seeds": 0, "source_noise": 0.0, "margin": 0.0}
# 体积比允许的偏差: 凸包阶段的抖动与焊接带来约 1e-5 的误差
VOLUME_TOLERANCE = 1e-4
# 默认参数切割猴头时单个父块允许丢失的体积: 高于切面内缩造成的缝隙, 远低于求交出错时的损失
TOUCH_LOSS_LIMIT = 0.005


def cut(sources, config, collection=""):
    """经阶段 API 切割; 输出集合名默认为空, 即当前集合"""
    return generate_fragments(bpy.context, sources, {**config, "collection_name": collection})


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
    config = {**BASE, "recursion": 2, "sub_cut_pieces": 4, "recursion_clamp": 0}
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
    config = {**BASE, "recursion": 1, "sub_cut_pieces": 4, "use_island_split": True}
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
        "sub_cut_pieces": 2,
        "recursion_clamp": 0,
        "use_keep_parents": not remove,
    }
    return cut([source], config, "Out")


def expected_name(obj):
    """按路径推出的名字: 源物体名, 后缀, 每层三位序号"""
    return "_".join(("Src", "cell", *(f"{index:03d}" for index in obj[shard_contract.PATH_MARK])))


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
        "sub_cut_pieces": 8,
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


def torus_source(seed_count=None):
    """圆环源物体; 给定 seed_count 时另加子物体种子"""
    bpy.ops.mesh.primitive_torus_add(major_radius=1.0, minor_radius=0.4)
    torus = bpy.context.active_object
    if seed_count:
        seeds(torus, seed_count, 1.2, 9)
    return torus


def case_first_cut_seeds():
    """首次切割种子数为 0 时跟随碎块总数上限, 超过总数上限被拒绝: 圆环用全部顶点做种子"""
    base = {"source": {"VERT_OWN"}, "source_noise": 0.0, "use_island_split": False}
    for clamp, limit, rounds, expected in (
        (0, 0, 0, 576),
        (100, 0, 0, 100),
        (100, 0, 1, 100),
        (1000, 0, 0, 576),
        (100, 30, 0, 30),
        (100, 100, 0, 100),
        (0, 30, 0, 30),
    ):
        reset()
        config = {**base, "first_cut_seeds": limit, "recursion_clamp": clamp, "recursion": rounds}
        count = len(cut([torus_source()], config).shards)
        check(
            f"总数上限 {clamp} 首次切割种子数 {limit} 递归 {rounds} 轮",
            count == expected,
            f"{count} 块, 期望 {expected}",
        )
    reset()
    expect_error(
        "首次切割种子数超过碎块总数上限被拒绝",
        InvalidParameterError,
        lambda: cut(
            [torus_source()],
            {**base, "first_cut_seeds": 100, "recursion_clamp": 30, "recursion": 0},
        ),
    )


def components(obj):
    """碎块网格按共享边连通的部分数"""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    seen, count = set(), 0
    for face in bm.faces:
        if face in seen:
            continue
        count += 1
        stack = [face]
        seen.add(face)
        while stack:
            for edge in stack.pop().edges:
                for other in edge.link_faces:
                    if other not in seen:
                        seen.add(other)
                        stack.append(other)
    bm.free()
    return count


def case_island_split():
    """孤岛拆分: 三个种子沿 x 轴排开, 中间的胞体是一块平板, 与圆环相交成互不连通的两段"""
    config = {**BASE, "recursion": 0}
    results = {}
    for split in (True, False):
        reset()
        torus = torus_source()
        mesh = bpy.data.meshes.new("axis_seeds")
        mesh.from_pydata([(-0.6, 0.0, 0.0), (0.0, 0.0, 0.0), (0.6, 0.0, 0.0)], [], [])
        holder = bpy.data.objects.new("axis_seeds", mesh)
        bpy.context.collection.objects.link(holder)
        holder.parent = torus
        result = cut([torus], {**config, "use_island_split": split}, "")
        results[split] = [components(shard) for shard in result.shards]
        print(f"   拆分孤岛 {split}: {len(result.shards)} 块, 连通块数 {results[split]}")
    check("打开时每块碎块都只有一个连通块", set(results[True]) == {1})
    check("关闭时中间的胞体含两个连通块", max(results[False]) == 2, str(results[False]))
    check("拆分后块数多一块", len(results[True]) == len(results[False]) + 1)


def subdivided_cube(cuts=2):
    """每个面都切成 (cuts+1)^2 个共面小面的立方体"""
    reset()
    mesh = bpy.data.meshes.new("Grid")
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=2.0)
    bmesh.ops.subdivide_edges(bm, edges=list(bm.edges), cuts=cuts, use_grid_fill=True)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new("Grid", mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def coplanar_edges(shards, limit=5e-4):
    """碎块内两侧面几乎共面的边数; 这样的边应当已经被合并掉"""
    total = 0
    for shard in shards:
        bm = bmesh.new()
        bm.from_mesh(shard.data)
        total += sum(
            1
            for edge in bm.edges
            if len(edge.link_faces) == 2 and edge.calc_face_angle(0.0) < limit
        )
        bm.free()
    return total


def case_final_merge():
    """最终合并共面面: 网格化的立方体切割后, 碎块里不再有两侧几乎共面的边"""
    source = subdivided_cube()
    faces = len(source.data.polygons)
    seeds(source, 6, 0.9, 4)
    result = cut([source], {**BASE, "recursion": 0}, "")
    left = coplanar_edges(result.shards)
    print(f"   源 {faces} 个面, 切成 {len(result.shards)} 块, 剩余共面边 {left}")
    check("没有残留的共面边", left == 0, str(left))


def case_seed_jitter():
    """种子扰动: 为 0 与不为 0 的结果不同, 同一随机种子下可复现, 体积仍然守恒"""
    prints = {}
    for label, noise in (("none", 0.0), ("jitter", 0.5), ("jitter again", 0.5)):
        reset()
        source = cube("Src")
        seeds(source, 12, 0.9, 6)
        result = cut([source], {**BASE, "source_noise": noise, "recursion": 0}, "")
        prints[label] = fingerprint(result)
        if noise:
            ratio = volume_ratio(result, 8.0)
            check(f"{label} 体积守恒", abs(ratio - 1) < VOLUME_TOLERANCE, f"{ratio:.6f}")
    check("扰动改变了切割结果", prints["none"] != prints["jitter"])
    check("同一随机种子的扰动可复现", prints["jitter"] == prints["jitter again"])


def case_materials_layers():
    """材质与数据层: 碎块保留材质槽与链接方式, UV 与颜色层; 断面材质只落在断面上"""
    reset()
    source = cube("Src")
    inner, outer, override = (
        bpy.data.materials.new(name) for name in ("Outer", "Inner", "Override")
    )
    source.data.materials.append(inner)
    source.data.materials.append(outer)
    source.material_slots[1].link = "OBJECT"
    source.material_slots[1].material = override
    for index, name in enumerate(("UVA", "UVB")):
        layer = source.data.uv_layers.new(name=name)
        for item in layer.data:
            item.uv = (index + 0.25, index + 0.75)
    source.data.uv_layers.active_index = 1
    source.data.uv_layers["UVB"].active_render = True
    source.data.uv_layers["UVB"].active_clone = True
    for name in ("Tint", "TintB"):
        color = source.data.color_attributes.new(name=name, type="FLOAT_COLOR", domain="POINT")
        for item in color.data:
            item.color = (0.2, 0.4, 0.6, 1.0)
    source.data.color_attributes.active_color_index = 1
    source.data.color_attributes.render_color_index = 1
    seeds(source, 8, 0.9, 5)
    result = cut([source], {**BASE, "recursion": 0, "material_index": 1}, "")
    shard = result.shards[0]
    slots = [
        (slot.link, slot.material.name if slot.material else None) for slot in shard.material_slots
    ]
    check("保留材质槽与链接方式", slots == [("DATA", "Outer"), ("OBJECT", "Override")], str(slots))
    check("保留 UV 层", [layer.name for layer in shard.data.uv_layers] == ["UVA", "UVB"])
    check(
        "保留颜色层",
        [(a.name, a.domain, a.data_type) for a in shard.data.color_attributes]
        == [("Tint", "POINT", "FLOAT_COLOR"), ("TintB", "POINT", "FLOAT_COLOR")],
    )
    indices = {face.material_index for shard in result.shards for face in shard.data.polygons}
    check("断面用索引 1, 外表面沿用源物体的 0", indices == {0, 1}, str(indices))
    exterior = [
        face.material_index
        for shard in result.shards
        for face in shard.data.polygons
        if max(abs(c) for c in (shard.matrix_world @ face.center)) > 0.999
    ]
    check("贴在源表面上的面都是索引 0", exterior and set(exterior) == {0}, str(set(exterior)))
    # 不只检查层名 - 非首层选择必须在普通递归和保留布尔模式中保持
    for label, options in (
        ("普通", None),
        ("递归", {"recursion": 1, "sub_cut_pieces": 2}),
        ("保留布尔", {"use_debug_bool": True}),
    ):
        tested = result if options is None else cut([source], {**BASE, **options})
        check(
            f"{label} 保留 UV 活动渲染克隆层",
            all(
                obj.data.uv_layers.active.name == "UVB"
                and obj.data.uv_layers["UVB"].active_render
                and obj.data.uv_layers["UVB"].active_clone
                for obj in tested.shards
            ),
        )
        check(
            f"{label} 保留颜色活动和渲染层",
            all(
                obj.data.color_attributes.active_color.name == "TintB"
                and obj.data.color_attributes[obj.data.color_attributes.render_color_index].name
                == "TintB"
                for obj in tested.shards
            ),
        )
    check(
        "源 UV 数据未改写",
        all(
            tuple(item.uv) == (index + 0.25, index + 0.75)
            for index, layer in enumerate(source.data.uv_layers)
            for item in layer.data
        ),
    )


def case_boolean_policy():
    """布尔策略与前置检查: 求解策略, 越界与非有限坐标的校验, 没有面的网格"""
    reset()
    depsgraph_of = bpy.context.evaluated_depsgraph_get
    small = cube("Small")
    big = cube("Big", half=2.0)
    bpy.ops.mesh.primitive_plane_add()
    plane = bpy.context.active_object
    bpy.context.view_layer.update()
    snapshot = boolean.inspect_source(small, depsgraph_of())
    check(
        "立方体的对角线与焊接距离",
        abs(snapshot.diagonal - 12**0.5) < 1e-6,
        f"{snapshot.diagonal:.6f}",
    )
    check("焊接距离随尺度", abs(snapshot.weld_distance - snapshot.diagonal * 1e-4) < 1e-12)
    check("封闭的立方体不启用容错求解", not snapshot.use_hole_tolerant)
    check("开放的平面启用容错求解", boolean.inspect_source(plane, depsgraph_of()).use_hole_tolerant)

    boolean.validate_intersection(small.data, small.matrix_world, snapshot)
    check("范围之内的结果通过校验", True)
    expect_error(
        "超出源包围盒的结果被拒绝",
        IntersectionBoundsError,
        lambda: boolean.validate_intersection(big.data, big.matrix_world, snapshot),
    )
    broken = cube("Broken")
    broken.data.vertices[0].co.x = float("nan")
    expect_error(
        "含非有限坐标的结果被拒绝",
        MeshFractureError,
        lambda: boolean.validate_intersection(broken.data, broken.matrix_world, snapshot),
    )

    faceless = bpy.data.objects.new("Faceless", bpy.data.meshes.new("Faceless"))
    faceless.data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [])
    bpy.context.collection.objects.link(faceless)
    select([faceless])
    bpy.context.view_layer.update()
    expect_error("没有面的网格无法切割", MeshFractureError, lambda: cut([faceless], {}, ""))
    check("失败后没有碎块", not [o for o in bpy.data.objects if o.get("_qd_source")])


def case_weld_cleanup():
    """求交结果整理: 焊接重合顶点, 清掉散边散点, 立方体的面保持不变"""
    reset()
    mesh = cube("Weld").data
    with mesh_edit(mesh) as bm:
        # 一个重合顶点, 一个散点, 一条散边
        bm.verts.ensure_lookup_table()
        bm.verts.new(bm.verts[0].co)
        bm.verts.new((5.0, 5.0, 5.0))
        bm.edges.new((bm.verts.new((6.0, 0.0, 0.0)), bm.verts.new((7.0, 0.0, 0.0))))
        bm.to_mesh(mesh)
    cleanup.weld_intersection(mesh, 1e-5)
    check("整理后只剩立方体的 8 个顶点", len(mesh.vertices) == 8, str(len(mesh.vertices)))
    check("整理后没有散边", not any(edge.is_loose for edge in mesh.edges))
    check("立方体的 6 个面保持不变", len(mesh.polygons) == 6, str(len(mesh.polygons)))


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
            name.startswith(("A_cell_", "B_cell_", "非常")) and name.count("_") >= 3
            for name in names
        ),
    )
    check("集合没有重名后缀", groups and not any("." in name for name in groups), str(groups))
    output = bpy.data.collections["Out"]

    def placed(shard):
        """碎块只在 输出集合/<源>/<源>_Shards 里, <源> 是按字节截短的源物体名"""
        label = shard["_qd_source"].encode("utf-8")[:SOURCE_NAME_BYTES].decode("utf-8", "ignore")
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
    original = interior.finish_interior

    def boom(*_args):
        """模拟收尾阶段的意外错误"""
        raise RuntimeError("injected failure")

    interior.finish_interior = boom
    try:
        config = {**BASE, "recursion": 1, "sub_cut_pieces": 4, "use_debug_redraw": True}
        cut([source], config, "RollbackOut")
        check("注入的异常被抛出", False)
    except RuntimeError:
        after = {kind: set(getattr(bpy.data, kind).keys()) for kind in before}
        check("新建数据全部回收", after == before, str({k: after[k] - before[k] for k in after}))
        check("源物体面隐藏状态还原", source.data.polygons[0].hide)
        check("源物体显示方式还原", source.display_type == "TEXTURED")
    finally:
        interior.finish_interior = original


def expect_error(label, error_type, call):
    """调用必须抛出指定类型的异常"""
    try:
        call()
        check(label, False, "没有抛错")
    except error_type as error:
        check(label, True, repr(error))


def case_cleanup_failures():
    """最终清理失败仍回滚 - 次要错误不覆盖主错误或跳过进度结束"""
    for stage in ("release", "progress_end", "rollback"):
        reset()
        source = cube("Src")
        select([source])
        before = {
            kind: set(getattr(bpy.data, kind).keys())
            for kind in ("objects", "meshes", "collections")
        }
        wm = SimpleNamespace(progress_begin=Mock(), progress_update=Mock(), progress_end=Mock())
        context = SimpleNamespace(**bpy.context.copy())
        context.window_manager = wm
        context.evaluated_depsgraph_get = bpy.context.evaluated_depsgraph_get
        primary = RuntimeError(f"injected {stage}")
        secondary = RuntimeError("secondary cleanup failure")
        if stage == "progress_end":
            wm.progress_end.side_effect = [primary, None]
        with ExitStack() as patches:
            if stage == "release":
                patches.enter_context(
                    patch.object(pipeline.Isolation, "release", side_effect=[primary, None])
                )
            elif stage == "rollback":
                patches.enter_context(
                    patch.object(interior, "finish_interior", side_effect=primary)
                )
                patches.enter_context(
                    patch.object(created_data.CreatedData, "rollback", side_effect=secondary)
                )
            try:
                generate_fragments(context, [source], {**BASE, "recursion": 0})
                check(f"{stage} 注入故障被抛出", False)
            except RuntimeError as error:
                check(f"{stage} 保留主异常", error is primary)
        check(f"{stage} 仍尝试结束进度", wm.progress_end.call_count >= 1)
        if stage == "rollback":
            # 故障解除后原账本行为仍可由现有回归用例验证
            check("回收失败后原选择仍恢复", bpy.context.selected_objects == [source])
        else:
            after = {kind: set(getattr(bpy.data, kind).keys()) for kind in before}
            check(f"{stage} 清理失败后回滚干净", after == before)


def case_created_data_failures():
    """单项回收失败继续其他条目 - 批量失败保留待删记录"""
    tracker = created_data.CreatedData()
    items = {name: SimpleNamespace(name=name) for name in ("a", "b")}
    calls, blocked = [], [True]

    def remove(item, **_kwargs):
        """模拟数据集合的单项删除故障"""
        calls.append(item.name)
        if item.name == "b" and blocked[0]:
            raise RuntimeError("injected delete")
        del items[item.name]

    data = SimpleNamespace(
        objects=SimpleNamespace(get=items.get, remove=remove), meshes={}, collections={}
    )
    for item in items.values():
        tracker.track("objects", item)
    with patch.object(created_data, "bpy", SimpleNamespace(data=data)):
        expect_error("单项回收失败明确抛错", RuntimeError, tracker.rollback)
        check("单项失败不阻止其他回收", calls == ["b", "a"] and list(items) == ["b"])
        blocked[0] = False
        tracker.rollback()
        tracker.rollback()
        check("剩余条目可重试且不会重复删除", not items)
    reset()
    obj = cube("Pending")
    tracker = created_data.CreatedData()
    tracker.bury(obj, obj.data)
    fake = SimpleNamespace(
        data=SimpleNamespace(batch_remove=Mock(side_effect=RuntimeError("injected batch")))
    )
    with patch.object(created_data, "bpy", fake):
        expect_error("批量删除失败明确抛错", RuntimeError, tracker.flush)
        check("失败后仍保留待删记录", len(tracker.graveyard) == 2)
    tracker.flush()
    check(
        "批量删除重试后完成",
        not tracker.graveyard
        and "Pending" not in bpy.data.objects
        and "Pending" not in bpy.data.meshes,
    )


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


def excluded_collections():
    """视图层里被排除的集合名"""
    names = []

    def visit(layer):
        """递归收集被排除的层节点"""
        for child in layer.children:
            if child.exclude:
                names.append(child.name)
            visit(child)

    visit(bpy.context.view_layer.layer_collection)
    return names


def owned_names():
    """全部带来源标记的物体名, 含保留的父碎块"""
    return sorted(obj.name for obj in bpy.data.objects if obj.get("_qd_source"))


def case_isolation():
    """依赖图隔离: 结果与不隔离时完全一致, 结束后不留排除与工作集合, 失败时回滚干净"""
    config = {
        **BASE,
        "recursion": 2,
        "sub_cut_pieces": 4,
        "recursion_clamp": 0,
        "use_keep_parents": True,
    }
    outcomes = []
    # 实时重绘不隔离, 是对照组
    for debug in (False, True):
        reset()
        source = cube("Src")
        seeds(source, 10, 0.9, 5)
        result = cut([source], {**config, "use_debug_redraw": debug}, "Iso")
        outcomes.append((fingerprint(result), owned_names(), len(result.shards)))
        if not debug:
            check("结束后没有被排除的集合", not excluded_collections(), str(excluded_collections()))
            check("结束后没有遗留工作集合", bpy.data.collections.get("QD_Work") is None)
            visible = bpy.context.view_layer.objects
            check("全部物体回到视图层", all(o.name in visible for o in bpy.data.objects))
            check("碎块可以选中", all(not shard.select_set(True) for shard in result.shards))
    check("隔离不改变碎块几何", outcomes[0][0] == outcomes[1][0], f"{outcomes[0][2]} 块")
    check("隔离不改变物体命名", outcomes[0][1] == outcomes[1][1], f"{len(outcomes[0][1])} 个")

    # 细分到一半抛错: 新建数据含工作集合在内全部回收, 不留被排除的集合
    reset()
    source = cube("Src")
    seeds(source, 10, 0.9, 5)
    before = {
        kind: set(getattr(bpy.data, kind).keys()) for kind in ("objects", "meshes", "collections")
    }
    original = intersect.collect
    calls = []
    hidden = []

    def flaky(*args):
        """第四次切割时抛错, 此时已处于隔离状态"""
        calls.append(1)
        if len(calls) == 4:
            hidden.extend(excluded_collections())
            raise RuntimeError("injected failure")
        return original(*args)

    intersect.collect = flaky
    try:
        cut([source], config, "IsoFail")
        check("注入的异常被抛出", False)
    except RuntimeError:
        after = {kind: set(getattr(bpy.data, kind).keys()) for kind in before}
        check(
            "递归细分时碎块集合处于排除状态",
            any(name.endswith("_Shards") for name in hidden),
            str(hidden),
        )
        check(
            "隔离中途失败后数据全部回收",
            after == before,
            str({k: after[k] - before[k] for k in after}),
        )
        check("隔离中途失败后没有被排除的集合", not excluded_collections())
    finally:
        intersect.collect = original

    # 递归时父块不在依赖图里: 粒子来源不能对它们求值
    reset()
    source = cube("Src")
    shards = cut(
        [source], {**BASE, "source": {"PARTICLE_OWN"}, "recursion": 1, "recursion_clamp": 40}
    ).shards
    check("粒子来源加递归可以运行", len(shards) > 1, f"{len(shards)} 块")


def case_batching():
    """分批切割: 每批父块数不同结果完全一致

    不留没有用户的网格与不在任何集合里的物体, 孤岛都被拆开
    """
    config = {
        **BASE,
        "recursion": 2,
        "sub_cut_pieces": 4,
        "recursion_clamp": 0,
        "use_island_split": True,
    }
    original = pipeline.BATCH_PARENTS
    outcomes = {}
    try:
        for size in (1, 7, 32):
            pipeline.BATCH_PARENTS = size
            reset()
            torus = torus_source(seed_count=14)
            result = cut([torus], {**config, "source": {"VERT_CHILD"}}, "Batch")
            outcomes[size] = (fingerprint(result), owned_names())
            if size == 7:
                orphans = [mesh.name for mesh in bpy.data.meshes if mesh.users == 0]
                floating = [obj.name for obj in bpy.data.objects if not obj.users_collection]
                check(
                    "待删数据都已批量删除",
                    not orphans and not floating,
                    str((orphans + floating)[:3]),
                )
                # 被替换的父块与空胞体都必须离开场景
                # 带来源标记的物体正是最终碎块, 也没有临时名的物体
                leftovers = set(owned_names()) - {shard.name for shard in result.shards}
                temporary = [obj.name for obj in bpy.data.objects if obj.name.startswith("QDTMP_")]
                check(
                    "场景里只有最终碎块",
                    not leftovers and not temporary,
                    str((*leftovers, *temporary)[:3]),
                )
                split = [components(shard) for shard in result.shards]
                check("每个碎块都是单一孤岛", set(split) == {1}, f"{len(split)} 块")
    finally:
        pipeline.BATCH_PARENTS = original
    check(
        "每批 7 个父块与逐个切割的碎块一致",
        outcomes[7][0] == outcomes[1][0],
        f"{len(outcomes[1][1])} 个",
    )
    check("每批 32 个父块与逐个切割的碎块一致", outcomes[32][0] == outcomes[1][0])
    check("分批不改变物体命名", outcomes[7][1] == outcomes[1][1] == outcomes[32][1])


def case_early_limit():
    """每轮开始前按上界估算总数: 必然超过硬上限时在这一轮动手之前就报错"""
    reset()
    source = cube("Src")
    seeds(source, 8, 0.9, 3)
    original_limit, original_cut = pipeline.SHARD_LIMIT, intersect.collect
    calls = []

    def counted(*args):
        """统计求交次数"""
        calls.append(1)
        return original_cut(*args)

    pipeline.SHARD_LIMIT = 60
    intersect.collect = counted
    try:
        config = {**BASE, "recursion": 3, "sub_cut_pieces": 6, "recursion_clamp": 0}
        expect_error("预估超过硬上限时报错", MeshFractureError, lambda: cut([source], config))
    finally:
        pipeline.SHARD_LIMIT, intersect.collect = original_limit, original_cut
    # 首次切割一次, 第一轮最多 8 个父块各一次; 第二轮预估会超限, 一次都不该切
    check("超限的那一轮没有动手", len(calls) <= 9, f"{len(calls)} 次求交")
    check("超限后没有遗留碎块", not [obj for obj in bpy.data.objects if obj.get("_qd_source")])


def case_weight_pruning():
    """非等轴权重的剪枝: 结果与不剪枝时一致, 并且确实少试了邻居"""
    rng = random.Random(21)
    points = [Vector(tuple(rng.uniform(-0.95, 0.95) for _ in range(3))) for _ in range(120)]
    snapshot = SimpleNamespace(minimum=(-1.0,) * 3, maximum=(1.0,) * 3, diagonal=12**0.5)
    counter = {"calls": 0}
    original_planes, original_shrink = cell_builder.points_in_planes, cell_builder._distance_shrink

    def counted(*args):
        """统计求交平面的次数"""
        counter["calls"] += 1
        return original_planes(*args)

    def summary(built):
        """胞体顶点集合, 与顶点顺序无关"""
        return [sorted(tuple(round(c, 6) for c in v) for v in vertices) for _c, vertices in built]

    cell_builder.points_in_planes = counted
    try:
        for margin in (0.0, 0.01):
            counter["calls"] = 0
            pruned = cell_builder.points_as_bmesh_cells(points, (1.0, 0.5, 0.25), margin, snapshot)
            pruned_calls = counter["calls"]
            # 下界设为 0 就永远不会提前收工, 得到不剪枝的参照
            cell_builder._distance_shrink = lambda _scale: 0.0
            counter["calls"] = 0
            reference = cell_builder.points_as_bmesh_cells(
                points, (1.0, 0.5, 0.25), margin, snapshot
            )
            cell_builder._distance_shrink = original_shrink
            check(
                f"剪枝不改变胞体 内缩量 {margin:g}",
                summary(pruned) == summary(reference),
                f"{len(pruned)} 个胞体",
            )
            check(
                f"剪枝少试了邻居 内缩量 {margin:g}",
                pruned_calls < counter["calls"] * 0.8,
                f"{pruned_calls} 对 {counter['calls']}",
            )
    finally:
        cell_builder.points_in_planes = original_planes
        cell_builder._distance_shrink = original_shrink


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


def asset_error(objects):
    """解析资产时期望失败, 返回报错的消息键; 没有失败时返回 None"""
    try:
        asset.resolve(objects)
    except asset.AssetError as error:
        return error.message
    return None


def duplicate_asset(root):
    """模拟在大纲里复制资产集合: 根集合的自定义属性与其中的物体都被复制, 物体是新的副本"""
    copy = bpy.data.collections.new(f"{root.name}_copy")
    bpy.context.scene.collection.children.link(copy)
    for key in root.keys():
        copy[key] = root[key]
    copies = []
    for obj in root.all_objects:
        twin = obj.copy()
        copy.objects.link(twin)
        copies.append(twin)
    return copy, copies


def case_interior_attribute():
    """断面面属性 qd_interior: 恰好覆盖切割新生成的断面, 外表面 (落在源立方体六个面上的面) 都不带"""
    reset()
    source = cube("Src", half=1.0, location=(0.0, 0.0, 1.0))
    seeds(source, 8, 0.9, 7)
    shards = cut(
        [source],
        {"source": {"VERT_CHILD"}, "first_cut_seeds": 0, "source_noise": 0.0, "recursion": 1},
    ).shards
    wrong, interior, exterior = 0, 0, 0
    for shard in shards:
        attribute = shard.data.attributes.get("qd_interior")
        if attribute is None:
            wrong += len(shard.data.polygons)
            continue
        flags = [item.value for item in attribute.data]
        for polygon, flag in zip(shard.data.polygons, flags, strict=True):
            # 内外交界处布尔求交会留下面积极小的碎三角, 几何上归属含糊, 不参与判定
            if polygon.area < 1e-3:
                continue
            points = [shard.matrix_world @ shard.data.vertices[i].co for i in polygon.vertices]
            # 外表面: 所有顶点落在同一个源立方体面上, 容差随缝隙留出
            outer = any(
                all(
                    abs(abs(point[axis] - (1.0 if axis == 2 else 0.0)) - 1.0) < 2e-3
                    for point in points
                )
                for axis in range(3)
            )
            interior += flag
            exterior += not flag
            wrong += flag == outer
    check("每块碎块都有断面面属性", all(s.data.attributes.get("qd_interior") for s in shards))
    check("断面标记与几何判定一致", wrong == 0, f"{wrong} 个面不一致")
    check("断面与外表面都存在", interior > 0 and exterior > 0, f"{interior} {exterior}")
    check("属性是布尔型的面属性", shards[0].data.attributes["qd_interior"].domain == "FACE")


def case_small_sources():
    """小物体: 切面内缩量, 共面容差与包围盒外扩都随物体尺寸缩放, 块数与体积不随物体变小而丢失

    内缩量是绝对长度, 默认 0.001 在 2 毫米的立方体上会把全部胞体切没
    实际使用的内缩量限制在包围盒对角线的千分之一
    """
    counts, ratios = {}, {}
    for half in (1.0, 0.1, 0.025, 0.005, 0.001):
        reset()
        source = cube("S", half=half, location=(0.0, 0.0, half))
        seeds(source, 8, 0.9 * half, 7)
        config = {"source": {"VERT_CHILD"}, "first_cut_seeds": 0, "source_noise": 0.0}
        shards = cut([source], config).shards
        counts[half] = len(shards)
        ratios[half] = sum(volume(shard) for shard in shards) / (2.0 * half) ** 3
    check("各种尺寸切出的块数相同", len(set(counts.values())) == 1, str(counts))
    check(
        "小物体的体积保留 96% 以上",
        min(ratios.values()) > 0.96,
        str({k: round(v, 4) for k, v in ratios.items()}),
    )
    snapshot = SimpleNamespace(diagonal=0.01)
    check("内缩量不超过对角线的千分之一", cell_builder.effective_margin(0.001, snapshot) == 1e-5)
    check("内缩量设为 0 时仍是 0", cell_builder.effective_margin(0.0, snapshot) == 0.0)
    check(
        "大物体不受限制",
        cell_builder.effective_margin(0.001, SimpleNamespace(diagonal=3.0)) == 0.001,
    )


def case_asset_identity():
    """资产身份: 新资产, 清单, 解析, 复制与旧数据被拒绝, 重建副本身份, 源物体改名"""
    reset()
    source = cube("Src")
    seeds(source, 6, 0.9, 3)
    first = cut([source], {}, "Out")
    second = cut([source], {}, "Out")
    root_a, root_b = first.source_to_asset[source], second.source_to_asset[source]
    check("同一源切两次得到两个资产", asset.shards_of(root_a) != asset.shards_of(root_b))
    check("两个资产的标识不同", root_a["_qd_asset"] != root_b["_qd_asset"])
    check("清单格式版本为 1", root_a.get(asset.FORMAT_MARK) == asset.FORMAT)
    check("源物体引用指向源物体", asset.source_of(root_a) == source)
    check("切割间隙等于 2 倍切面内缩量", abs(asset.gap_of(root_a) - 2 * 0.001) < 1e-9)
    shard = first.shards[0]
    check("从碎块解析到它的资产", asset.resolve([shard]) == [root_a])
    check(
        "碎块按路径排序且与本次结果一致",
        asset.shards_of(root_a) == sorted(first.shards, key=lambda item: tuple(item["_qd_path"])),
    )
    check("一个源对应两个资产时拒绝猜测", "Several assets" in (asset_error([source]) or ""))
    check("没有资产时报错", "Select shards" in (asset_error([cube("Plain")]) or ""))
    source.name = "Renamed"
    check("源物体改名后引用仍有效", asset.source_of(root_a) == source)
    copy, twins = duplicate_asset(root_a)
    check("复制的资产被拒绝", "was duplicated" in (asset_error([twins[0]]) or ""))
    check("复制之后原资产同样被拒绝", "was duplicated" in (asset_error([shard]) or ""))
    fixed = make_asset_unique(twins[0])
    check("重建身份作用在副本上", fixed == copy)
    check("重建后副本可以解析", asset.resolve([twins[0]]) == [copy])
    check("重建后原资产也可以解析", asset.resolve([shard]) == [root_a])
    check("副本与原资产的标识不同", copy["_qd_asset"] != root_a["_qd_asset"])
    twin = shard.copy()
    root_a.children[0].objects.link(twin)
    check(
        "资产里有复制出来的碎块时拒绝",
        "copied shards" in str(capture(lambda: asset.shards_of(root_a))),
    )
    bpy.data.objects.remove(twin)
    legacy = second.shards[0]
    legacy.pop("_qd_asset")
    check("旧版本碎块被拒绝", "older version" in (asset_error([legacy]) or ""))
    root_b[asset.FORMAT_MARK] = 99
    check("未知格式被拒绝", "unknown format" in (asset_error([second.shards[1]]) or ""))


def capture(call):
    """执行 call, 返回它抛出的异常; 没有抛出时返回 None"""
    try:
        call()
    except Exception as error:
        return error
    return None


def case_asset_persistence():
    """保存并重新打开文件后, 资产标识, 源物体引用与碎块标记都还在"""
    reset()
    source = cube("Keep")
    seeds(source, 5, 0.9, 4)
    result = cut([source], {}, "Out")
    root = result.source_to_asset[source]
    asset_id, root_name, count = root["_qd_asset"], root.name, len(result.shards)
    path = Path(tempfile.mkdtemp(prefix="qd_asset_")) / "asset.blend"
    bpy.ops.wm.save_as_mainfile(filepath=str(path), copy=True)
    bpy.ops.wm.open_mainfile(filepath=str(path))
    reopened = bpy.data.collections[root_name]
    check("重开后资产标识不变", reopened["_qd_asset"] == asset_id)
    check("重开后源物体引用仍指向源物体", asset.source_of(reopened) == bpy.data.objects["Keep"])
    check("重开后碎块数不变", len(asset.shards_of(reopened)) == count)
    check("重开后可以从碎块解析资产", asset.resolve(asset.shards_of(reopened)[:1]) == [reopened])


# 全部场景, 按顺序逐个运行; 会重新打开文件的场景放在最后
CASES = (
    case_volume,
    case_torus,
    case_reproducible,
    case_interior,
    case_naming,
    case_rollback,
    case_cleanup_failures,
    case_created_data_failures,
    case_limits,
    case_recenter,
    case_keep_parents,
    case_max_shards,
    case_touching_parts,
    case_first_cut_seeds,
    case_island_split,
    case_final_merge,
    case_seed_jitter,
    case_materials_layers,
    case_boolean_policy,
    case_weld_cleanup,
    case_isolation,
    case_batching,
    case_early_limit,
    case_weight_pruning,
    case_small_sources,
    case_interior_attribute,
    case_asset_identity,
    # 会重新打开文件, 放在最后
    case_asset_persistence,
)


harness.run_cases(CASES)
