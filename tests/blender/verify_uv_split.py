"""按 UV 岛拆分验证 - 在合成的薄壳上逐条核对配对, 桥接, 选择与提示

测试目的:
    按 UV 岛拆分要把薄壳内外两层对应的 UV 岛配成一对, 沿裂缝补侧壁成为封闭碎块;
    审查发现过几类隐患, 这里各用一个合成网格复现并核对:
    1. 碎片小于壳厚的二十倍时也要配上对 (配对间距按连通块而不是按岛计算)
    2. 一个岛有同心的外轮廓与孔时, 侧壁要接在对应的环之间 (按整条环的距离配环而不是按中心)
    3. 并入的不封闭部件不能被桥接到壳的裂缝上 (只在配对的两个岛之间桥接)
    4. 同向叠放的衬里与重合的双面几何不是薄壳, 不能配对
    5. 三层结构只选一层时, 每一层都要拆, 每个面只出现在一块碎块里
    6. 一层有孔而另一层没有时, 没能封闭的碎块要出现在结果的 open_shards 里
    7. 编辑模式下点按钮失败时回到编辑模式
    8. 面数多, 小部件多时耗时可接受
    9. 每段侧壁展开成独立的长条 UV: U 长度等于裂缝长度, V 等于壳厚, 密度均匀, 条带互不重叠,
       写进每一个 UV 层, 闭合条带的接缝处理正确, 绑定构建的盒投影不覆盖它们
    10. 断面材质索引指向最后一个槽之后时, 碎块自动多一个 <材质>_Interior 槽, 侧壁用它, 再拆复用
测试过程:
    用 bmesh 直接造网格: 每个连通块给出顶点, 面与每个面的岛标签, UV 取顶点坐标加按标签的平移,
    同一标签内共享顶点的 UV 相同, 不同标签之间 UV 不连续, 这样 UV 岛就是标签
    调用阶段 API convert_uv_islands 或界面算子, 检查碎块数, 封闭性, 侧壁, 面数守恒与提示
运行方式 (仓库根目录):
    <Blender 可执行文件> -b --factory-startup --python-exit-code 1 --python tests\\blender\\verify_uv_split.py
"""

import math
import sys
from itertools import pairwise
from pathlib import Path
from time import perf_counter

import bmesh
import bpy
from mathutils import Vector

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness
from harness import check

from source import registration
from source.common import shards
from source.tools.MeshFracture.api import convert_uv_islands


class Part:
    """一个连通块: 顶点坐标, 面 (顶点下标), 每个面的岛标签"""

    def __init__(self, verts, faces, labels):
        """保存几何与标签"""
        self.verts = verts
        self.faces = faces
        self.labels = labels


def grid(x0, x1, y0, y1, count, z, up, label):
    """z 高度上的矩形网格, count x count 个四边形; up 为假时面朝下; label(中心) 给出岛标签"""
    verts = [
        Vector((x0 + (x1 - x0) * i / count, y0 + (y1 - y0) * j / count, z))
        for j in range(count + 1)
        for i in range(count + 1)
    ]
    faces, labels = [], []
    for j in range(count):
        for i in range(count):
            a = j * (count + 1) + i
            quad = [a, a + 1, a + count + 2, a + count + 1]
            faces.append(quad if up else quad[::-1])
            center = sum((verts[k] for k in quad), Vector()) / 4
            labels.append(label(center))
    return Part(verts, faces, labels)


def sphere(radius, subdivisions, outward, label):
    """二十面体球; outward 为假时法线朝内; label(中心) 给出岛标签"""
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=subdivisions, radius=radius)
    verts = [v.co.copy() for v in bm.verts]
    faces = [[v.index for v in f.verts] for f in bm.faces]
    bm.free()
    if not outward:
        faces = [face[::-1] for face in faces]
    labels = [label(sum((verts[k] for k in face), Vector()) / len(face)) for face in faces]
    return Part(verts, faces, labels)


def build(name, parts):
    """把若干连通块建成一个网格物体; UV = 顶点 xy 加按标签的平移, 标签就是 UV 岛
    第二个 UV 层 Lightmap 与第一个相同, 用来检查侧壁条带是否写进了每一个 UV 层"""
    bm = bmesh.new()
    layer = bm.loops.layers.uv.new("UVMap")
    second = bm.loops.layers.uv.new("Lightmap")
    for part in parts:
        made = [bm.verts.new(co) for co in part.verts]
        for face_verts, label in zip(part.faces, part.labels, strict=True):
            face = bm.faces.new([made[k] for k in face_verts])
            for loop in face.loops:
                co = loop.vert.co
                loop[layer].uv = (co.x * 0.1 + 0.37 * co.z + 3.0 * label, co.y * 0.1)
                loop[second].uv = loop[layer].uv
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj


def regions(part):
    """连通块里按共享边且标签相同求出的区域数, 即这一层的 UV 岛数; 同一标签可能分成几片"""
    owner = {}
    for index, face in enumerate(part.faces):
        for k in range(len(face)):
            key = tuple(sorted((face[k], face[(k + 1) % len(face)])))
            owner.setdefault(key, []).append(index)
    parent = list(range(len(part.faces)))

    def root(i):
        """并查集的根"""
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for faces in owner.values():
        for a, b in pairwise(faces):
            if part.labels[a] == part.labels[b]:
                parent[root(a)] = root(b)
    return len({root(i) for i in range(len(part.faces))})


def stats(obj):
    """碎块的封闭性, 侧壁面数, 面数"""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    closed = all(len(e.link_faces) == 2 and e.is_contiguous for e in bm.edges)
    bm.free()
    layer = obj.data.attributes.get(shards.INTERIOR_ATTRIBUTE)
    walls = sum(1 for d in layer.data if d.value) if layer else 0
    return closed, walls, len(obj.data.polygons)


def summary(result):
    """打印一次拆分的结果, 返回每块的 (封闭, 侧壁面数, 面数)"""
    rows = [stats(s) for s in result.shards]
    print(
        f"  info: shards {len(rows)} closed {sum(r[0] for r in rows)} walls {[r[1] for r in rows]} "
        f"open_shards {[s.name for s in result.open_shards]} split {result.split_parts}/{result.total_parts}"
    )
    return rows


def case_small_pieces():
    """壳厚 0.03, 碎片对角线约 0.3: 旧规则 (岛对角线的 5%, 约 0.015) 配不上对, 现在要全部配上并封闭"""
    harness.reset()
    seeds = [
        Vector((math.cos(a) * math.sin(b), math.sin(a) * math.sin(b), math.cos(b)))
        for a in [k * 0.7 for k in range(9)]
        for b in [0.3 + k * 0.4 for k in range(7)]
    ]

    def label(center):
        """离得最近的种子序号"""
        return min(range(len(seeds)), key=lambda i: (center.normalized() - seeds[i]).length)

    obj = build("SmallShell", [sphere(1.0, 4, True, label), sphere(0.97, 4, False, label)])
    result = convert_uv_islands(bpy.context, [obj], {"collection_name": "Small"})
    rows = summary(result)
    islands = regions(sphere(1.0, 4, True, label))
    check("小碎片也全部配对: 碎块数等于外层岛数", len(rows) == islands, f"{len(rows)} vs {islands}")
    check("小碎片全部封闭", all(r[0] for r in rows) and not result.open_shards)
    check("每块都有侧壁", all(r[1] > 0 for r in rows))


def plate(z, up, label, count=24):
    """边长 2 的方板一层"""
    return grid(-1.0, 1.0, -1.0, 1.0, count, z, up, label)


def ring_label(center):
    """同心标签: 中心圆盘 0, 圆环 1, 外圈 2; 圆环的两条边界环中心重合"""
    r = math.hypot(center.x, center.y)
    return 0 if r < 0.3 else (1 if r < 0.65 else 2)


def case_concentric():
    """圆环岛的外轮廓与孔同心: 侧壁要接在对应的环之间, 三块都封闭, 体积等于面积乘厚度"""
    harness.reset()
    obj = build("Ring", [plate(0.02, True, ring_label), plate(0.0, False, ring_label)])
    result = convert_uv_islands(bpy.context, [obj], {"collection_name": "Ring"})
    rows = summary(result)
    check("同心岛: 3 块", len(rows) == 3, str(len(rows)))
    check("同心岛: 全部封闭", all(r[0] for r in rows) and not result.open_shards)
    volume = sum(harness.volume(s) for s in result.shards)
    check("同心岛: 体积等于面积乘厚度", abs(volume - 4.0 * 0.02) < 1e-4, f"{volume:.5f}")


def wall_strips(obj):
    """碎块侧壁的 UV 岛: 侧壁面按共享边且 UV 连续求连通块, 返回每个岛的统计

    每项是 dict: faces 面数, u v 两个方向的 UV 跨度, top 上层边界的世界长度, ratio 各面 UV 面积与世界面积之比的范围,
    face_u 单个面最大的 U 跨度 (接缝处理不对时会横跨整条条带), flagged 是否都带已展开标记, same 两个 UV 层是否相同
    """
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.transform(obj.matrix_world)
    interior = bm.faces.layers.bool.get(shards.INTERIOR_ATTRIBUTE)
    flag = bm.faces.layers.bool.get(shards.INTERIOR_UV_ATTRIBUTE)
    uv, second = bm.loops.layers.uv["UVMap"], bm.loops.layers.uv["Lightmap"]
    walls = {face for face in bm.faces if face[interior]}

    def continuous(edge):
        """边两侧的 UV 是否连续"""
        if len(edge.link_loops) != 2:
            return False
        a, b = edge.link_loops
        return (a[uv].uv - b.link_loop_next[uv].uv).length < 1e-6 and (
            a.link_loop_next[uv].uv - b[uv].uv
        ).length < 1e-6

    found = [
        strip_stats(members, walls, uv, second, flag) for members in face_groups(walls, continuous)
    ]
    bm.free()
    return found


def face_groups(faces, linked_by):
    """面集合按 linked_by(边) 为真的共享边求连通块"""
    seen, out = set(), []
    for start in faces:
        if start in seen:
            continue
        stack, members = [start], []
        seen.add(start)
        while stack:
            face = stack.pop()
            members.append(face)
            for edge in face.edges:
                if not linked_by(edge):
                    continue
                for other in edge.link_faces:
                    if other in faces and other not in seen:
                        seen.add(other)
                        stack.append(other)
        out.append(members)
    return out


def uv_area(face, uv):
    """面在 UV 上的面积 (鞋带公式)"""
    points = [loop[uv].uv for loop in face.loops]
    twice = sum(
        points[i].x * points[i - 1].y - points[i - 1].x * points[i].y for i in range(len(points))
    )
    return abs(twice) / 2


def strip_stats(members, walls, uv, second, flag):
    """一个侧壁 UV 岛的统计, 字段见 wall_strips"""
    corners = [loop for face in members for loop in face.loops]
    us = [loop[uv].uv.x for loop in corners]
    vs = [loop[uv].uv.y for loop in corners]
    ratios = [uv_area(face, uv) / face.calc_area() for face in members]
    # 上层的裂缝边: 两端都在上层, 且与外表面相邻
    top = {
        edge
        for face in members
        for edge in face.edges
        if all(v.co.z > 0.01 for v in edge.verts) and any(f not in walls for f in edge.link_faces)
    }
    face_spans = [
        max(loop[uv].uv.x for loop in face.loops) - min(loop[uv].uv.x for loop in face.loops)
        for face in members
    ]
    return {
        "faces": len(members),
        "u": max(us) - min(us),
        "v": max(vs) - min(vs),
        "box": (min(us), max(us), min(vs), max(vs)),
        "top": sum(edge.calc_length() for edge in top),
        "ratio": (min(ratios), max(ratios)),
        "face_u": max(face_spans),
        "flagged": all(face[flag] for face in members),
        "same": all((loop[uv].uv - loop[second].uv).length < 1e-9 for loop in corners),
    }


def case_strips():
    """侧壁展开成独立长条: 每段侧壁一个 UV 岛, U 长度等于裂缝长度, V 等于壳厚, 密度均匀, 互不重叠, 写进每个 UV 层;
    绑定构建的盒投影跳过它们"""
    from source.tools.Rig.core import interior as rig_interior

    harness.reset()
    obj = build("Strips", [plate(0.02, True, ring_label), plate(0.0, False, ring_label)])
    result = convert_uv_islands(
        bpy.context, [obj], {"collection_name": "Strips", "strip_uv_scale": 1.0}
    )
    total = 0
    for shard in result.shards:
        strips = wall_strips(shard)
        total += len(strips)
        for strip in strips:
            print(
                f"  info: {shard.name} strip faces {strip['faces']} u {strip['u']:.4f} top {strip['top']:.4f} v {strip['v']:.4f} "
                f"density {strip['ratio'][0]:.3f}-{strip['ratio'][1]:.3f} face_u {strip['face_u']:.4f}"
            )
        check(
            f"{shard.name}: 条带 U 长度等于裂缝长度",
            all(abs(s["u"] - s["top"]) < 0.01 * s["top"] for s in strips),
        )
        check(f"{shard.name}: 条带 V 宽度等于壳厚", all(abs(s["v"] - 0.02) < 1e-4 for s in strips))
        check(
            f"{shard.name}: 贴图密度处处为 1",
            all(0.99 < s["ratio"][0] and s["ratio"][1] < 1.01 for s in strips),
        )
        check(
            f"{shard.name}: 闭合条带的接缝处没有面横跨整条", all(s["face_u"] < 0.2 for s in strips)
        )
        check(
            f"{shard.name}: 侧壁都带已展开标记, 两个 UV 层相同",
            all(s["flagged"] and s["same"] for s in strips),
        )
        boxes = [s["box"] for s in strips]
        overlap = any(
            a[2] < b[3] and b[2] < a[3] for i, a in enumerate(boxes) for b in boxes[i + 1 :]
        )
        check(f"{shard.name}: 条带在 UV 上互不重叠", not overlap)
        # 绑定构建的盒投影: 已展开的侧壁不应被改写
        copy = shard.data.copy()
        before = [tuple(d.uv) for d in copy.uv_layers.active.data]
        written = rig_interior.box_project(copy, 1.0)
        after = [tuple(d.uv) for d in copy.uv_layers.active.data]
        bpy.data.meshes.remove(copy)
        check(
            f"{shard.name}: 盒投影跳过已展开的侧壁", written == 0 and before == after, str(written)
        )
    # 圆盘一圈, 圆环内外两圈, 外圈与板边两圈
    check("每段侧壁各成一条: 共 5 条", total == 5, str(total))


def case_auto_interior_material():
    """按 UV 岛拆分的自动断面材质: 源物体只有一个材质时断面材质索引填 1, 碎块多一个 <材质>_Interior 槽,
    侧壁都用它, 外表面不用; 源物体的槽不变; 再拆一次复用同一个材质"""
    harness.reset()
    for material in list(bpy.data.materials):
        bpy.data.materials.remove(material)
    obj = build("Mat", [plate(0.02, True, ring_label), plate(0.0, False, ring_label)])
    obj.data.materials.append(bpy.data.materials.new("Gold"))
    result = convert_uv_islands(
        bpy.context, [obj], {"collection_name": "Mat", "material_index": 1, "hide_source": False}
    )
    slots = {tuple(s.material.name for s in shard.material_slots) for shard in result.shards}
    check("碎块的槽是 Gold 加 Gold_Interior", slots == {("Gold", "Gold_Interior")}, str(slots))
    right = all(
        (face.material_index == 1)
        == shard.data.attributes[shards.INTERIOR_ATTRIBUTE].data[face.index].value
        for shard in result.shards
        for face in shard.data.polygons
    )
    check("侧壁都用断面材质, 外表面都不用", right)
    check("源物体的材质槽不变", [s.material.name for s in obj.material_slots] == ["Gold"])
    convert_uv_islands(bpy.context, [obj], {"collection_name": "Mat2", "material_index": 1})
    names = sorted(m.name for m in bpy.data.materials)
    check("再拆一次复用同一个断面材质", names == ["Gold", "Gold_Interior"], str(names))


def case_attached_open_part():
    """板上方贴一片不封闭的小面片: 默认并入最近的碎块, 它的边不能被桥接到板的裂缝上"""
    harness.reset()
    patch = grid(-0.1, 0.1, -0.1, 0.1, 2, 0.05, True, lambda _c: 9)
    obj = build("Patch", [plate(0.02, True, ring_label), plate(0.0, False, ring_label), patch])
    result = convert_uv_islands(bpy.context, [obj], {"collection_name": "Patch"})
    rows = summary(result)
    check("面片并入: 仍是 3 块", len(rows) == 3, str(len(rows)))
    holder = max(
        result.shards,
        key=lambda s: max(v.co.z for v in s.data.vertices) + s.matrix_world.translation.z,
    )
    bm = bmesh.new()
    bm.from_mesh(holder.data)
    bm.transform(holder.matrix_world)
    patch_open = [
        e for e in bm.edges if len(e.link_faces) == 1 and min(v.co.z for v in e.verts) > 0.04
    ]
    patch_walls = [
        e
        for e in bm.edges
        if min(v.co.z for v in e.verts) > 0.04
        and len(e.link_faces) == 2
        and any(abs(f.normal.z) < 0.5 for f in e.link_faces)
    ]
    bm.free()
    check(
        "面片的边保持开放, 没有被桥接",
        len(patch_open) == 8 and not patch_walls,
        f"open {len(patch_open)} walls {len(patch_walls)}",
    )
    check(
        "面片不算应当封闭的碎块", not result.open_shards, str([s.name for s in result.open_shards])
    )


def case_not_a_shell():
    """同向叠放的衬里, 重合的双面几何: 都不是薄壳, 不配对, 按单层曲面每个岛一块且没有侧壁"""
    for label_text, second_z, second_up in (("同向衬里", 0.0, True), ("重合双面", 0.02, False)):
        harness.reset()
        obj = build(
            "Lining", [plate(0.02, True, ring_label), plate(second_z, second_up, ring_label)]
        )
        result = convert_uv_islands(bpy.context, [obj], {"collection_name": "Lining"})
        rows = summary(result)
        check(f"{label_text}: 不配对, 每个岛一块", len(rows) == 6, str(len(rows)))
        check(f"{label_text}: 没有侧壁", all(r[1] == 0 for r in rows), str([r[1] for r in rows]))


def quadrant(center):
    """象限标签"""
    return (0 if center.x < 0 else 1) + (0 if center.y < 0 else 2)


def case_three_layers():
    """三层: 上层左半 A, 上层右半 C, 下层整块 B; B 的左半与 A 配对, 右半与 C 配对
    只选 A 时 B 与 C 也要拆, 每个面只出现在一块碎块里"""
    harness.reset()
    left = grid(-1.0, 0.0, -1.0, 1.0, 12, 0.02, True, quadrant)
    right = grid(0.0, 1.0, -1.0, 1.0, 12, 0.02, True, quadrant)
    # A 与 C 之间留一条缝, 让它们是两个连通块
    right.verts = [v + Vector((0.001, 0.0, 0.0)) for v in right.verts]
    bottom = grid(-1.0, 1.001, -1.0, 1.0, 24, 0.0, False, quadrant)
    obj = build("Layers", [left, right, bottom])
    total = len(obj.data.polygons)
    for poly in obj.data.polygons:
        poly.select = poly.index == 0
    result = convert_uv_islands(
        bpy.context, [obj], {"collection_name": "Layers", "use_uv_selected": True}
    )
    rows = summary(result)
    check(
        "三层只选一层: 每个面只出现一次",
        sum(r[2] for r in rows) - sum(r[1] for r in rows) == total,
        f"{sum(r[2] - r[1] for r in rows)} vs {total}",
    )
    check(
        "三层只选一层: 三层都被拆开",
        result.split_parts == 3,
        f"{result.split_parts}/{result.total_parts}",
    )
    check("三层只选一层: 4 块都封闭", len(rows) == 4 and all(r[0] for r in rows))


def case_open_reported():
    """上层的一个岛中间少了几个面 (有孔), 下层完整: 这块没法封闭, 要出现在 open_shards 里

    孔不能碰到板的边缘: 碰到时它只是边界环上的一个缺口, 照样能和下层的边界环桥接封闭
    """
    harness.reset()
    top = plate(0.02, True, ring_label)

    def hole(face):
        """板内部靠右的两个面, 不碰板边也不碰圆环"""
        center = sum((top.verts[k] for k in face), Vector()) / 4
        return 0.75 < center.x < 0.85 and abs(center.y) < 0.1

    keep = [i for i, f in enumerate(top.faces) if not hole(f)]
    top.faces = [top.faces[i] for i in keep]
    top.labels = [top.labels[i] for i in keep]
    obj = build("Holed", [top, plate(0.0, False, ring_label)])
    result = convert_uv_islands(bpy.context, [obj], {"collection_name": "Holed"})
    rows = summary(result)
    names = [s.name for s in result.open_shards]
    check("有孔的那块被报告为没有封闭", len(names) == 1, str(names))
    check("其余碎块封闭", sum(r[0] for r in rows) == len(rows) - 1, str([r[0] for r in rows]))


def case_edit_mode_failure():
    """编辑模式下没有选中任何面又开启仅拆选中: 算子失败后回到编辑模式"""
    harness.reset()
    obj = build("EditFail", [plate(0.02, True, ring_label), plate(0.0, False, ring_label)])
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    settings = bpy.context.scene.qd_settings.mesh_fracture
    settings.method, settings.use_uv_selected = "UV_ISLANDS", True
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    # 后台模式下算子报错取消时 bpy.ops 会抛 RuntimeError, 消息就是报告的文字
    try:
        status = bpy.ops.qd_mesh_fracture.uv_islands()
    except RuntimeError as error:
        status = {"CANCELLED"}
        print(f"  info: {error}")
    try:
        check("没有选中面时算子取消", status == {"CANCELLED"}, str(status))
        check("失败后回到编辑模式", bpy.context.mode == "EDIT_MESH", bpy.context.mode)
    finally:
        # 无论判据如何都回到物体模式, 否则后面的场景与退出时的清理都会受影响
        if bpy.context.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
        settings.use_uv_selected = False


def case_speed():
    """两层各 20480 面 (bmesh 二十面体细分 6 级), 约 120 个岛, 外加 300 个小方块装饰: 记录耗时, 应在数秒内完成"""
    harness.reset()
    seeds = [
        Vector((math.cos(a) * math.sin(b), math.sin(a) * math.sin(b), math.cos(b)))
        for a in [k * 0.52 for k in range(12)]
        for b in [0.2 + k * 0.28 for k in range(10)]
    ]

    def label(center):
        """离得最近的种子序号"""
        return min(range(len(seeds)), key=lambda i: (center.normalized() - seeds[i]).length)

    parts = [sphere(1.0, 6, True, label), sphere(0.99, 6, False, label)]
    for k in range(300):
        a, b = k * 2.399, math.acos(1 - 2 * (k + 0.5) / 300)
        c = Vector((math.cos(a) * math.sin(b), math.sin(a) * math.sin(b), math.cos(b))) * 1.02
        cube = bmesh.new()
        bmesh.ops.create_cube(cube, size=0.02)
        verts = [v.co + c for v in cube.verts]
        faces = [[v.index for v in f.verts] for f in cube.faces]
        cube.free()
        parts.append(Part(verts, faces, [500 + k] * len(faces)))
    obj = build("Speed", parts)
    started = perf_counter()
    result = convert_uv_islands(bpy.context, [obj], {"collection_name": "Speed"})
    seconds = perf_counter() - started
    rows = summary(result)
    print(f"  info: faces {len(obj.data.polygons)} time {seconds:.2f}s")
    islands = regions(parts[0])
    check("大网格: 小方块并入, 碎块数等于岛数", len(rows) == islands, f"{len(rows)} vs {islands}")
    check("大网格: 全部封闭", not result.open_shards)
    check("大网格: 30 秒内完成", seconds < 30.0, f"{seconds:.2f}s")


def main():
    """注册插件后运行全部场景"""
    registration.register()
    harness.run_cases(
        [
            case_small_pieces,
            case_concentric,
            case_strips,
            case_auto_interior_material,
            case_attached_open_part,
            case_not_a_shell,
            case_three_layers,
            case_open_reported,
            case_edit_mode_failure,
            case_speed,
        ]
    )


main()
