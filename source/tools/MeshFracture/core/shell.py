"""壳体操作数 - 源网格含嵌套的封闭曲面时, 为布尔准备一个把内腔曲面翻向朝内的临时副本

空心壳常由外层与内层两张封闭曲面组成, 且两张面的法线都朝外
Exact 布尔按绕数判断内外, 两层都朝外时内腔的绕数为 2, 会被当成实体填满
只有内层朝内, 内腔的绕数才回到 0, 布尔得到的才是外层与内层之间的壳层
所以这里按嵌套深度把深度为奇数的封闭曲面翻向朝内, 偶数的保持朝外, 源物体本身不改动
"""

import bmesh
import bpy
from mathutils.bvhtree import BVHTree

from .boolean import RAY_DIRECTIONS, RAY_HITS
from .shard_objects import discard_object

# 体积绝对值相对包围盒立方低于它的曲面视为退化, 朝向不可信, 不翻转
DEGENERATE_VOLUME_RATIO = 1.0e-12
# 射线越过命中点的距离 = 网格包围盒对角线乘以该比例, 防止浮点误差让射线反复命中同一个面
RAY_STEP_RATIO = 1.0e-5
# 临时操作数的物体名前缀之后跟切割期临时名, 由回收账本负责回收
OPERAND_LABEL = "operand"


def _face_groups(bm):
    """按共边把面分成连通块, 同时判断每一块是不是封闭曲面

    bm 源网格的 BMesh
    返回 (块列表, 封闭标记列表); 块是面列表, 封闭指每条边都正好连接两个面
    """
    seen = set()
    groups, closed = [], []
    for start in bm.faces:
        if start in seen:
            continue
        seen.add(start)
        stack, members, is_closed = [start], [], True
        while stack:
            face = stack.pop()
            members.append(face)
            for edge in face.edges:
                linked = edge.link_faces
                # 开放边或多面共边: 这一块没有明确的内外
                if len(linked) != 2:
                    is_closed = False
                for other in linked:
                    if other not in seen:
                        seen.add(other)
                        stack.append(other)
        groups.append(members)
        closed.append(is_closed)
    return groups, closed


def _signed_volumes(bm, owner, count):
    """每个连通块的带符号体积, 朝外为正

    bm 源网格的 BMesh
    owner 面索引到块序号的列表
    count 块的数量
    返回 体积列表, 与块同序
    """
    volumes = [0.0] * count
    for loops in bm.calc_loop_triangles():
        a, b, c = (loop.vert.co for loop in loops)
        # 三角形与原点围成的四面体的带符号体积, 同一块里求和就是整块的体积
        volumes[owner[loops[0].face.index]] += a.dot(b.cross(c)) / 6.0
    return volumes


def _enclosed(tree, owner, index, point, step):
    """一个块是不是被奇数个别的块包住: 从块表面上一点出发, 数穿过别的块的次数的奇偶, 三条射线按多数票决定

    tree 全部面的求交树
    owner 面索引到块序号的列表
    index 要判断的块序号, 它自己的面不计入穿越
    point 块表面上的一点
    step 射线越过命中点的距离
    返回 被奇数个别的块包住为真
    """
    votes = 0
    for direction in RAY_DIRECTIONS:
        crossings, last, origin = 0, None, point.copy()
        for _ in range(RAY_HITS):
            location, _normal, face, _distance = tree.ray_cast(origin, direction)
            if location is None:
                break
            # 自己的面不计; 挪过交点后浮点误差仍可能再次命中同一个面, 这样的命中也不计
            if face != last and owner[face] != index:
                crossings += 1
            last = face
            origin = location + direction * step
        votes += crossings % 2
    return votes * 2 > len(RAY_DIRECTIONS)


def reversed_faces(bm):
    """找出朝向与嵌套深度不符的面: 深度为偶数的封闭曲面应当朝外, 为奇数的应当朝内

    只有两个以上的封闭曲面才可能嵌套, 否则不处理; 不封闭的块不参与判断也不翻转
    bm 源网格的 BMesh
    返回 要翻转的面列表, 没有时为空
    """
    groups, closed = _face_groups(bm)
    if sum(closed) < 2:
        return []
    bm.faces.index_update()
    bm.faces.ensure_lookup_table()
    owner = [0] * len(bm.faces)
    for index, members in enumerate(groups):
        for face in members:
            owner[face.index] = index
    volumes = _signed_volumes(bm, owner, len(groups))
    # 射线步长与退化判定都按网格自身的包围盒尺度
    coords = [vert.co for vert in bm.verts]
    sizes = [max(co[axis] for co in coords) - min(co[axis] for co in coords) for axis in range(3)]
    extent = max(sizes)
    step = max(sum(size * size for size in sizes) ** 0.5 * RAY_STEP_RATIO, 1.0e-9)
    tree = BVHTree.FromBMesh(bm)
    flipped = []
    for index, members in enumerate(groups):
        if not closed[index] or abs(volumes[index]) <= DEGENERATE_VOLUME_RATIO * extent**3:
            continue
        hollow = _enclosed(tree, owner, index, members[0].calc_center_median(), step)
        # 朝外时体积为正: 深度为奇数应当朝内 (体积为负), 偶数应当朝外 (体积为正)
        if (volumes[index] > 0) == hollow:
            flipped.extend(members)
    return flipped


def hollow_operand(context, obj, depsgraph, batch):
    """为布尔准备操作数: 源物体含嵌套曲面且朝向不符时返回修正后的临时副本, 否则就是源物体本身

    副本取自求值后的网格, 修改器栈的结果也一并带入; 副本链接在场景根集合里, 才会进入依赖图
    它与它的网格都登记在回收账本里, 失败时随回滚删除, 成功时由调用方用 release_operand 丢弃
    context Blender 上下文
    obj 被切的物体
    depsgraph 求值用的依赖图
    batch ShardBatch, 提供回收账本与临时名
    返回 操作数物体
    """
    evaluated = obj.evaluated_get(depsgraph)
    mesh = bpy.data.meshes.new_from_object(evaluated)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        flipped = reversed_faces(bm)
        if not flipped:
            bpy.data.meshes.remove(mesh)
            return obj
        bmesh.ops.reverse_faces(bm, faces=flipped)
        # 源面的隐藏标记不能带进操作数: 布尔结果里仍然隐藏的面才被当成断面
        for face in bm.faces:
            face.hide = False
        bm.to_mesh(mesh)
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    finally:
        bm.free()
    tracker = batch.naming.tracker
    tracker.track("meshes", mesh)
    name = f"{batch.naming.temp_name()}_{OPERAND_LABEL}"
    mesh.name = name
    operand = tracker.track("objects", bpy.data.objects.new(name, mesh))
    operand.matrix_world = evaluated.matrix_world.copy()
    context.scene.collection.objects.link(operand)
    return operand


def release_operand(tracker, obj, operand):
    """布尔取回结果后丢弃临时副本; 操作数就是源物体本身时什么也不做

    tracker 回收账本
    obj 被切的物体
    operand hollow_operand 返回的操作数
    """
    if operand is not obj:
        discard_object(tracker, operand)
