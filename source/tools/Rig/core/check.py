"""检查 - 绑定验收: 碎块守恒, 逐帧蒙皮几何, 骨骼名与上限, 变换, 断面 UV, 结算时间

检查只读数据; 每条问题带级别, 代码与英文消息键, 消息按本工具的翻译上下文翻译
错误级问题会让构建失败而不提交, 警告级只提示
"""

from dataclasses import dataclass, field

from mathutils import Matrix

from ....common import asset
from ....common.names import first_non_ascii
from ....common.status import CURRENT, rig_status
from ....common.translate import tr_report
from ..constants import (
    CONTEXT,
    FACE_SHARD_ATTRIBUTE,
    SETTLE_SECONDS,
    SHARD_ATTRIBUTE,
    VERIFY_TOLERANCE,
    VERTEX_ATTRIBUTE,
)
from ..errors import RigError
from . import interior
from .partition import BONE_NAME_BYTES
from .swap import is_intact

# 问题级别: 错误会让导出停止
ERROR = "ERROR"
# 问题级别: 警告只提示
WARNING = "WARNING"

# 碎块与蒙皮网格里属于它的部分不一致
SHARD_GEOMETRY_MESSAGE = 'Shard "{name}" does not match the skinned mesh: {detail}'
# 骨骼名超过字节上限或含非 ASCII 字符
NAME_BYTES_MESSAGE = 'Bone "{name}" has a name that is too long or not ASCII'
# 一段网格引用的骨骼数超过上限
BONE_PALETTE_MESSAGE = 'Mesh "{name}" uses {count} bones, above the limit of {limit}'
# 一个骨架的骨骼总数超过上限
BONE_TOTAL_MESSAGE = 'Skeleton "{name}" has {count} bones, above the limit of {limit}'
# 绑定已过期
STALE_MESSAGE = "The rig is outdated; build it again"
# 导出集合里的物体不是单位变换
SCALE_MESSAGE = 'Object "{name}" has a transform that is not the identity'
# 断面没有 UV 层
INTERIOR_UV_MESSAGE = 'The interior faces of "{name}" have no UV'
# 碎块没有断面标记, 无法生成断面 UV
INTERIOR_MARK_MESSAGE = 'The shards of "{name}" have no interior marker, so the cut faces got no UV'
# 最后一次起动距离结束太近, 碎块可能还没落定
SETTLE_MESSAGE = (
    "The last start is only {seconds:.1f} s before the end; shards may not have settled"
)
# 蒙皮网格与碎块动画逐帧对不上
SKIN_MISMATCH_MESSAGE = (
    "The skinned mesh differs from the shard animation by {error:.2e} at frame {frame}"
)
# 还没有构建绑定
NOT_BUILT_MESSAGE = "Build the rig first"
# 开启仅允许 ASCII 名称时, 名字里出现了非 ASCII 字符
NON_ASCII_MESSAGE = 'Name "{name}" is not ASCII; rename it or turn off ASCII Names Only'


@dataclass(frozen=True)
class Issue:
    """一条检查发现的问题"""

    # ERROR 或 WARNING
    level: str
    # 代码, 例如 SHARD_GEOMETRY
    code: str
    # 英文消息键
    message: str
    # 消息的插值参数
    values: dict = field(default_factory=dict)


def issue_text(issue):
    """问题的译文, 参数已填入

    issue 一条问题
    返回 已翻译的文字
    """
    return tr_report(CONTEXT, issue.message, **issue.values)


def require_ascii(names):
    """要求全部名字只含 ASCII 字符, 否则抛 RigError 并指出第一个不合规的名字

    names 要检查的名字序列
    """
    bad = first_non_ascii(names)
    if bad is not None:
        raise RigError(NON_ASCII_MESSAGE, name=bad)


def export_objects(root):
    """取资产导出集合里的骨架与蒙皮网格

    root 资产根集合
    返回 (骨架列表, 蒙皮网格列表), 各自按名字排序; 没有导出集合时抛 RigError
    """
    found = asset.rig_objects(root)
    if found is None:
        raise RigError(NOT_BUILT_MESSAGE)
    return found


def _int_attribute(mesh, name, domain):
    """读出整数属性的全部值

    mesh 网格数据
    name 属性名
    domain 期望的属性域, POINT 或 FACE
    返回 值列表; 属性不存在或域不符时为 None
    """
    attribute = mesh.attributes.get(name)
    if attribute is None or attribute.domain != domain:
        return None
    values = [0] * len(attribute.data)
    attribute.data.foreach_get("value", values)
    return values


def _material_counts(mesh):
    """统计各材质的面数

    mesh 网格数据
    返回 {材质名: 面数}, 没有材质的槽位记为空名
    """
    indices = [0] * len(mesh.polygons)
    mesh.polygons.foreach_get("material_index", indices)
    names = [slot.name if slot else "" for slot in mesh.materials]
    counts = {}
    for index in indices:
        # 材质索引越界时 (槽位被删) 归到空名, 不抛错
        name = names[index] if index < len(names) else ""
        counts[name] = counts.get(name, 0) + 1
    return counts


def _mesh_tally(meshes):
    """统计蒙皮网格里每块碎块的几何量

    meshes 蒙皮网格物体列表
    返回 {碎块序号: [顶点数, 面数, {材质: 面数}]}; 同一块碎块出现在多个网格里时累加
    """
    tally = {}
    for obj in meshes:
        mesh = obj.data
        vertex_ids = _int_attribute(mesh, SHARD_ATTRIBUTE, "POINT")
        face_ids = _int_attribute(mesh, FACE_SHARD_ATTRIBUTE, "FACE")
        # 没有来源属性的网格无法对回碎块, 之后会表现为碎块缺失
        if vertex_ids is None or face_ids is None:
            continue
        indices = [0] * len(mesh.polygons)
        mesh.polygons.foreach_get("material_index", indices)
        names = [slot.name if slot else "" for slot in mesh.materials]
        for ordinal in vertex_ids:
            entry = tally.setdefault(ordinal, [0, 0, {}])
            entry[0] += 1
        for ordinal, index in zip(face_ids, indices, strict=True):
            entry = tally.setdefault(ordinal, [0, 0, {}])
            entry[1] += 1
            name = names[index] if index < len(names) else ""
            entry[2][name] = entry[2].get(name, 0) + 1
    return tally


def _shard_expectation(shard):
    """碎块本身的几何量

    shard 碎块物体
    返回 (顶点数, 面数, {材质: 面数})
    """
    return len(shard.data.vertices), len(shard.data.polygons), _material_counts(shard.data)


def _geometry_issues(items, meshes):
    """碎块守恒: 逐块核对蒙皮网格里属于它的顶点数, 面数与各材质的面数

    items 资产的碎块列表, 序号即来源属性里的碎块序号
    meshes 蒙皮网格物体列表
    返回 Issue 列表
    """
    tally = _mesh_tally(meshes)
    issues = []
    for ordinal, shard in enumerate(items):
        expected = _shard_expectation(shard)
        found = tuple(tally.get(ordinal, (0, 0, {})))
        if found == expected:
            continue
        detail = f"{found[0]}/{expected[0]} vertices, {found[1]}/{expected[1]} faces"
        issues.append(
            Issue(
                ERROR,
                "SHARD_GEOMETRY",
                SHARD_GEOMETRY_MESSAGE,
                {"name": shard.name, "detail": detail},
            )
        )
    return issues


def _bone_issues(armatures, meshes, config):
    """骨骼名合规, 每段引用的骨骼数与每个骨架的骨骼总数不超过上限

    armatures 骨架物体列表
    meshes 蒙皮网格物体列表
    config 参数值, 提供两个上限, 0 表示不限
    返回 Issue 列表
    """
    issues = []
    for armature in armatures:
        # 骨骼名必须是 ASCII 且不超过字节上限: 引擎与 FBX 工具链按它截断
        for bone in armature.data.bones:
            name = bone.name
            if not name.isascii() or len(name.encode("utf-8")) > BONE_NAME_BYTES:
                issues.append(Issue(ERROR, "NAME_BYTES", NAME_BYTES_MESSAGE, {"name": name}))
        count, limit = len(armature.data.bones), config.max_bones_total
        if limit and count > limit:
            values = {"name": armature.name, "count": count, "limit": limit}
            issues.append(Issue(ERROR, "BONE_TOTAL", BONE_TOTAL_MESSAGE, values))
    # 一段网格引用的骨骼数就是它的顶点组数
    for obj in meshes:
        count, limit = len(obj.vertex_groups), config.max_bones_per_mesh
        if limit and count > limit:
            values = {"name": obj.name, "count": count, "limit": limit}
            issues.append(Issue(ERROR, "BONE_PALETTE", BONE_PALETTE_MESSAGE, values))
    return issues


def _transform_issues(armatures, meshes):
    """导出集合里的物体应当是单位变换: 骨架在世界原点, 网格已经在世界坐标里

    armatures 骨架物体列表
    meshes 蒙皮网格物体列表
    返回 Issue 列表
    """
    identity = Matrix.Identity(4)
    issues = []
    for obj in (*armatures, *meshes):
        # 世界矩阵与单位矩阵逐元素比较, 最大偏差超过容差就算不是单位变换
        deviation = max(abs(value) for row in (obj.matrix_world - identity) for value in row)
        if deviation > 1e-6:
            issues.append(Issue(WARNING, "SCALE", SCALE_MESSAGE, {"name": obj.name}))
    return issues


def _interior_issues(meshes, config):
    """断面 UV: 盒投影但碎块没有断面标记, 或有断面而网格没有 UV 层

    meshes 蒙皮网格物体列表
    config 参数值, 提供断面 UV 方式
    返回 Issue 列表
    """
    issues = []
    for obj in meshes:
        # 原物体网格本来就没有断面
        if is_intact(obj):
            continue
        faces = interior.interior_faces(obj.data)
        # 没有断面标记: 选了盒投影才需要提示, 保持不变时本来就不处理
        if faces is None:
            if config.interior_uv == "BOX":
                issues.append(
                    Issue(WARNING, "INTERIOR_UV", INTERIOR_MARK_MESSAGE, {"name": obj.name})
                )
        # 有断面却没有 UV 层: 引擎里断面无法单独铺材质
        elif faces and not obj.data.uv_layers:
            issues.append(Issue(WARNING, "INTERIOR_UV", INTERIOR_UV_MESSAGE, {"name": obj.name}))
    return issues


def _settle_issues(context, root):
    """最后一次起动之后留的时间不足一秒时提示

    context Blender 上下文, 提供帧率
    root 资产根集合
    返回 Issue 列表
    """
    record = asset.rig_record(root)
    result = asset.sim_record(root)
    last = None if record is None else record.get("last_start")
    # 没有起动帧 (没有碎块动过) 或没有模拟成果时没有可比较的
    if last is None or result is None:
        return []
    scene = context.scene
    seconds = (result["frame_end"] - last) * scene.render.fps_base / scene.render.fps
    if seconds >= SETTLE_SECONDS:
        return []
    return [Issue(WARNING, "SETTLE", SETTLE_MESSAGE, {"seconds": seconds})]


def frame_samples(first, last, count):
    """首尾加上均匀取的若干帧, 去重后升序

    first 起始帧
    last 结束帧
    count 取的帧数
    返回 帧号列表; 范围退化或只取一帧时只有起始帧
    """
    if last <= first or count <= 1:
        return [first]
    # 集合去重: 帧数少于取样数时均匀取点会落到同一帧
    return sorted({first + round((last - first) * step / (count - 1)) for step in range(count)})


def _visible_frames(record, frames):
    """碎块以原尺寸出现的核对帧: 原物体切换时去掉出现帧之前的帧, 并补上出现帧

    record 绑定记录, 可以为 None
    frames 要核对的帧号列表
    返回 帧号列表, 升序
    """
    show = None if record is None else record.get("show_frame")
    if show is None:
        return list(frames)
    first, last = record["frames"]
    # 出现帧落在模拟范围里时一定核对: 它是碎块第一次出现的帧, 最容易暴露缩放开关写错
    extra = {show} if first <= show <= last else set()
    return sorted({*(frame for frame in frames if frame >= show), *extra})


def verify_skin(context, root, frames):
    """逐帧核对蒙皮网格与碎块动画

    蒙皮网格求值后的顶点与碎块动画给出的世界顶点逐个比较; 对应关系由来源属性给出, 不用最近点
    原物体切换时碎块在出现帧之前是隐藏缩放, 只核对出现帧及之后; 原物体网格没有来源属性, 不核对
    context Blender 上下文
    root 资产根集合
    frames 要核对的帧号列表
    返回 (最大误差, 最大误差所在帧)
    """
    items = asset.shards_of(root)
    _armatures, meshes = export_objects(root)
    frames = _visible_frames(asset.rig_record(root), frames)
    # 每个网格的顶点到 (碎块序号, 碎块内原顶点序号) 的对应
    mapping = []
    for obj in meshes:
        vertex_ids = _int_attribute(obj.data, SHARD_ATTRIBUTE, "POINT")
        local_ids = _int_attribute(obj.data, VERTEX_ATTRIBUTE, "POINT")
        # 没有来源属性的网格 (原物体网格) 无法对回碎块
        if vertex_ids is not None and local_ids is not None:
            mapping.append((obj, vertex_ids, local_ids))
    scene = context.scene
    original = scene.frame_current
    worst, worst_frame = 0.0, frames[0] if frames else 0
    try:
        for frame in frames:
            scene.frame_set(frame)
            depsgraph = context.evaluated_depsgraph_get()
            for obj, vertex_ids, local_ids in mapping:
                # 求值后的网格带着骨架形变, 与碎块动画给出的期望位置比较
                evaluated = obj.evaluated_get(depsgraph)
                mesh = evaluated.to_mesh()
                try:
                    for index, (ordinal, local) in enumerate(
                        zip(vertex_ids, local_ids, strict=True)
                    ):
                        shard = items[ordinal]
                        expected = shard.matrix_world @ shard.data.vertices[local].co
                        error = (mesh.vertices[index].co - expected).length
                        if error > worst:
                            worst, worst_frame = error, frame
                finally:
                    evaluated.to_mesh_clear()
    finally:
        # 无论成败都回到原来的帧, 核对不应改变用户的时间轴位置
        scene.frame_set(original)
    return worst, worst_frame


def skin_tolerance(record):
    """蒙皮误差的容差: 基础容差加上不动阈值允许的位移与转角带来的顶点位移

    record 绑定记录, 其中的 tolerance 由构建时按不动阈值算出
    返回 容差, Blender 单位
    """
    return VERIFY_TOLERANCE + record.get("tolerance", 0.0)


def check_rig(context, root, config, sample_frames=3):
    """对资产的绑定做全部检查

    context Blender 上下文
    root 资产根集合
    config 参数值
    sample_frames 逐帧蒙皮核对抽取的帧数, 0 表示不核对
    返回 Issue 元组; 没有构建过时抛 RigError
    """
    record = asset.rig_record(root)
    if record is None:
        raise RigError(NOT_BUILT_MESSAGE)
    armatures, meshes = export_objects(root)
    items = asset.shards_of(root)
    issues = []
    issues += _geometry_issues(items, meshes)
    issues += _bone_issues(armatures, meshes, config)
    # 逐帧蒙皮核对最耗时, 构建时已经核对过, 这里可以关闭
    if sample_frames:
        frames = frame_samples(record["frames"][0], record["frames"][1], sample_frames)
        error, frame = verify_skin(context, root, frames)
        if error > skin_tolerance(record):
            values = {"error": error, "frame": frame}
            issues.append(Issue(ERROR, "SKIN_MISMATCH", SKIN_MISMATCH_MESSAGE, values))
    # 绑定过期只是警告, 导出入口另外会拒绝过期的绑定
    if rig_status(root, context.scene).state != CURRENT:
        issues.append(Issue(WARNING, "STALE", STALE_MESSAGE))
    issues += _transform_issues(armatures, meshes)
    issues += _interior_issues(meshes, config)
    issues += _settle_issues(context, root)
    return tuple(issues)
