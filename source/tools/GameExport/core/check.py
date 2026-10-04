"""检查 - 绑定验收: 碎块守恒, 逐帧蒙皮几何, 骨骼名与上限, 变换, 断面 UV, 结算时间

检查只读数据; 每条问题带级别, 代码与英文消息键, 消息按本工具的翻译上下文翻译
错误级问题会让导出停止, 警告级只提示
"""

from dataclasses import dataclass, field

from mathutils import Matrix

from ....common import asset
from ....common.status import CURRENT, rig_status
from ....common.translate import tr_report
from ..constants import (
    CONTEXT,
    FACE_SHARD_ATTRIBUTE,
    SHARD_ATTRIBUTE,
    VERIFY_TOLERANCE,
    VERTEX_ATTRIBUTE,
)
from ..errors import ExportError
from . import interior
from .partition import BONE_NAME_BYTES

ERROR = "ERROR"
WARNING = "WARNING"

# 问题消息键
SHARD_GEOMETRY_MESSAGE = 'Shard "{name}" does not match the skinned mesh: {detail}'
NAME_BYTES_MESSAGE = 'Bone "{name}" has a name that is too long or not ASCII'
BONE_PALETTE_MESSAGE = 'Mesh "{name}" uses {count} bones, above the limit of {limit}'
BONE_TOTAL_MESSAGE = 'Skeleton "{name}" has {count} bones, above the limit of {limit}'
STALE_MESSAGE = "The rig is outdated; build it again"
SCALE_MESSAGE = 'Object "{name}" has a transform that is not the identity'
INTERIOR_UV_MESSAGE = 'The interior faces of "{name}" have no UV'
INTERIOR_MARK_MESSAGE = 'The shards of "{name}" have no interior marker, so the cut faces got no UV'
SETTLE_MESSAGE = (
    "The last start is only {seconds:.1f} s before the end; shards may not have settled"
)
SKIN_MISMATCH_MESSAGE = (
    "The skinned mesh differs from the shard animation by {error:.2e} at frame {frame}"
)
NOT_BUILT_MESSAGE = "Build the rig first"


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
    """问题的译文, 参数已填入"""
    return tr_report(CONTEXT, issue.message, **issue.values)


def export_objects(root):
    """资产的导出集合里的 (骨架列表, 蒙皮网格列表), 按名字排序; 没有导出集合时报错"""
    collection = asset.rig_collection(root)
    if collection is None:
        raise ExportError(NOT_BUILT_MESSAGE)
    objects = sorted(collection.objects, key=lambda obj: obj.name)
    return (
        [obj for obj in objects if obj.type == "ARMATURE"],
        [obj for obj in objects if obj.type == "MESH"],
    )


def _int_attribute(mesh, name, domain):
    """整数属性的全部值; 属性不存在或域不符时为 None"""
    attribute = mesh.attributes.get(name)
    if attribute is None or attribute.domain != domain:
        return None
    values = [0] * len(attribute.data)
    attribute.data.foreach_get("value", values)
    return values


def _material_counts(mesh):
    """各材质名的面数"""
    indices = [0] * len(mesh.polygons)
    mesh.polygons.foreach_get("material_index", indices)
    names = [slot.name if slot else "" for slot in mesh.materials]
    counts = {}
    for index in indices:
        name = names[index] if index < len(names) else ""
        counts[name] = counts.get(name, 0) + 1
    return counts


def _mesh_tally(meshes):
    """蒙皮网格里每块碎块的 (顶点数, 面数, {材质: 面数}); 同一块碎块出现在多个网格里时累加"""
    tally = {}
    for obj in meshes:
        mesh = obj.data
        vertex_ids = _int_attribute(mesh, SHARD_ATTRIBUTE, "POINT")
        face_ids = _int_attribute(mesh, FACE_SHARD_ATTRIBUTE, "FACE")
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
    """碎块本身的 (顶点数, 面数, {材质: 面数})"""
    return len(shard.data.vertices), len(shard.data.polygons), _material_counts(shard.data)


def _geometry_issues(items, meshes):
    """碎块守恒: 逐块核对蒙皮网格里属于它的顶点数, 面数与各材质的面数"""
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
    """骨骼名合规, 每段引用的骨骼数与每个骨架的骨骼总数不超过上限"""
    issues = []
    for armature in armatures:
        for bone in armature.data.bones:
            name = bone.name
            if not name.isascii() or len(name.encode("utf-8")) > BONE_NAME_BYTES:
                issues.append(Issue(ERROR, "NAME_BYTES", NAME_BYTES_MESSAGE, {"name": name}))
        count, limit = len(armature.data.bones), config.max_bones_total
        if limit and count > limit:
            values = {"name": armature.name, "count": count, "limit": limit}
            issues.append(Issue(ERROR, "BONE_TOTAL", BONE_TOTAL_MESSAGE, values))
    for obj in meshes:
        count, limit = len(obj.vertex_groups), config.max_bones_per_mesh
        if limit and count > limit:
            values = {"name": obj.name, "count": count, "limit": limit}
            issues.append(Issue(ERROR, "BONE_PALETTE", BONE_PALETTE_MESSAGE, values))
    return issues


def _transform_issues(armatures, meshes):
    """导出集合里的物体应当是单位变换: 骨架在世界原点, 网格已经在世界坐标里"""
    identity = Matrix.Identity(4)
    issues = []
    for obj in (*armatures, *meshes):
        deviation = max(abs(value) for row in (obj.matrix_world - identity) for value in row)
        if deviation > 1e-6:
            issues.append(Issue(WARNING, "SCALE", SCALE_MESSAGE, {"name": obj.name}))
    return issues


def _interior_issues(meshes, config):
    """断面 UV: 盒投影但碎块没有断面标记, 或有断面而网格没有 UV 层"""
    issues = []
    for obj in meshes:
        faces = interior.interior_faces(obj.data)
        if faces is None:
            if config.interior_uv == "BOX":
                issues.append(
                    Issue(WARNING, "INTERIOR_UV", INTERIOR_MARK_MESSAGE, {"name": obj.name})
                )
        elif faces and not obj.data.uv_layers:
            issues.append(Issue(WARNING, "INTERIOR_UV", INTERIOR_UV_MESSAGE, {"name": obj.name}))
    return issues


def _settle_issues(context, root):
    """最后一次起动之后留的时间不足一秒时提示"""
    record = asset.rig_record(root)
    result = asset.sim_record(root)
    last = None if record is None else record.get("last_start")
    if last is None or result is None:
        return []
    scene = context.scene
    seconds = (result["frame_end"] - last) * scene.render.fps_base / scene.render.fps
    if seconds >= 1.0:
        return []
    return [Issue(WARNING, "SETTLE", SETTLE_MESSAGE, {"seconds": seconds})]


def _frame_samples(first, last, count):
    """首尾加上均匀取的若干帧, 去重后升序"""
    if last <= first or count <= 1:
        return [first]
    return sorted({first + round((last - first) * step / (count - 1)) for step in range(count)})


def verify_skin(context, root, frames):
    """逐帧核对蒙皮网格与碎块动画: 返回 (最大误差, 最大误差所在帧)

    蒙皮网格求值后的顶点与碎块动画给出的世界顶点逐个比较; 对应关系由来源属性给出, 不用最近点
    """
    items = asset.shards_of(root)
    _armatures, meshes = export_objects(root)
    mapping = []
    for obj in meshes:
        vertex_ids = _int_attribute(obj.data, SHARD_ATTRIBUTE, "POINT")
        local_ids = _int_attribute(obj.data, VERTEX_ATTRIBUTE, "POINT")
        mapping.append((obj, vertex_ids, local_ids))
    scene = context.scene
    original = scene.frame_current
    worst, worst_frame = 0.0, frames[0] if frames else 0
    try:
        for frame in frames:
            scene.frame_set(frame)
            depsgraph = context.evaluated_depsgraph_get()
            for obj, vertex_ids, local_ids in mapping:
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
        scene.frame_set(original)
    return worst, worst_frame


def skin_tolerance(record):
    """蒙皮误差的容差: 基础容差加上不动阈值允许的位移与转角带来的顶点位移"""
    return VERIFY_TOLERANCE + record.get("tolerance", 0.0)


def check_rig(context, root, config, sample_frames=3):
    """对资产的绑定做全部检查, 返回 Issue 元组; 没有构建过时报错

    sample_frames 是逐帧蒙皮核对抽取的帧数, 0 表示不核对
    """
    record = asset.rig_record(root)
    if record is None:
        raise ExportError(NOT_BUILT_MESSAGE)
    armatures, meshes = export_objects(root)
    items = asset.shards_of(root)
    issues = []
    issues += _geometry_issues(items, meshes)
    issues += _bone_issues(armatures, meshes, config)
    if sample_frames:
        frames = _frame_samples(record["frames"][0], record["frames"][1], sample_frames)
        error, frame = verify_skin(context, root, frames)
        if error > skin_tolerance(record):
            values = {"error": error, "frame": frame}
            issues.append(Issue(ERROR, "SKIN_MISMATCH", SKIN_MISMATCH_MESSAGE, values))
    if rig_status(root, context.scene).state != CURRENT:
        issues.append(Issue(WARNING, "STALE", STALE_MESSAGE))
    issues += _transform_issues(armatures, meshes)
    issues += _interior_issues(meshes, config)
    issues += _settle_issues(context, root)
    return tuple(issues)


# 供 build 抽样帧用
frame_samples = _frame_samples
