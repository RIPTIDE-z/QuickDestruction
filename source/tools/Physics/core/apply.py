"""物理应用 - 为资产的碎块建立刚体, 把原点移到体积质心, 记录静止姿态, 按材料逐块写入刚体属性

应用不改变碎块是否在刚体世界里: 原本不在世界里的碎块建立刚体数据后立即移出
何时参与模拟由模拟会话决定
对已有数据的改动先记入写前快照, 新建的数据登记到创建账本; 中途失败时两者一起撤回
"""

from dataclasses import dataclass

from mathutils import Matrix

from ....common import asset, rigidbody
from ....common.blender_context import mesh_edit
from ....common.created_data import CreatedData, WriteLog, run_all
from ....common.geometry import convex_hull_volume, is_not_closed, vertex_mean, volume_centroid
from ..constants import CHILD_SHAPE, MASS_MIN
from ..errors import PhysicsError
from ..settings import SCHEMA

# 质心偏移不超过碎块尺寸乘以该比例时视为已在质心, 不再移动原点, 重复应用因此不改动几何
ORIGIN_EPSILON = 1e-7


@dataclass(frozen=True)
class PhysicsResult:
    """一次应用的结果"""

    # 设置了刚体的碎块数
    shards: int
    # 全部碎块的总质量, kg
    mass: float
    # 网格不封闭, 改用凸包体积的碎块数
    open_meshes: int
    # 体积接近零, 质心退回顶点平均位置的碎块数
    degenerate: int


@dataclass
class _Tally:
    """应用过程中的累计量"""

    # 已处理的碎块数
    shards: int = 0
    # 总质量
    mass: float = 0.0
    # 不封闭的碎块数
    open_meshes: int = 0
    # 退化的碎块数
    degenerate: int = 0


def _measure(shard):
    """在网格局部坐标里量出体积与质心; 不封闭的网格用凸包体积与顶点平均位置

    shard 碎块物体
    返回 (体积, 质心, 是否封闭, 是否退化)
    """
    with mesh_edit(shard.data) as bm:
        closed = not is_not_closed(bm)
        volume, centroid = volume_centroid(bm)
        # 封闭但体积接近零: 质心不可信, 记为退化
        degenerate = closed and centroid is None
        # 不封闭的网格没有可靠的带符号体积, 改用凸包体积近似
        if not closed:
            volume = convex_hull_volume(bm)
        # 质心不可信时退回顶点平均位置
        if not closed or centroid is None:
            centroid = vertex_mean(bm)
        return abs(volume), centroid.copy(), closed, degenerate


def _move_origin(shard, centroid, log):
    """把原点移到质心, 世界几何不变; 网格被多个物体共用或偏移极小时不动

    shard 碎块物体
    centroid 局部坐标里的质心
    log 写前快照
    """
    size = max(shard.dimensions) or 1.0
    # 共用网格时改网格会连带改动别的物体; 偏移极小时再移动只会累积浮点误差
    if shard.data.users > 1 or centroid.length <= ORIGIN_EPSILON * size:
        return
    mesh = shard.data
    # 网格反向平移, 物体矩阵正向平移, 两者抵消后世界坐标不变; 撤回时先恢复矩阵再平移回网格
    mesh.transform(Matrix.Translation(-centroid))
    log.add(lambda: mesh.transform(Matrix.Translation(centroid)))
    log.set(shard, "matrix_world", shard.matrix_world @ Matrix.Translation(centroid))


def _write_body(shard, mass, values, log):
    """按材料写入刚体属性, 每项先记下原值

    shard 碎块物体, 必须已有刚体
    mass 质量, kg
    values 参数值
    log 写前快照
    """
    body = shard.rigid_body
    log.set(body, "collision_shape", values.collision_shape)
    log.set(body, "mass", mass)
    log.set(body, "friction", values.friction)
    log.set(body, "restitution", values.restitution)
    log.set(body, "linear_damping", values.linear_damping)
    log.set(body, "angular_damping", values.angular_damping)
    # 固定边距时写入边距值; 自动时交给 Blender 默认的内嵌边距
    fixed = values.margin_mode == "FIXED"
    log.set(body, "use_margin", fixed)
    if fixed:
        log.set(body, "collision_margin", values.margin)


def _record_rest(shard, log):
    """首次应用时把当前世界矩阵记为静止姿态; 已有静止姿态的碎块保持不变

    shard 碎块物体
    log 写前快照
    """
    # 已有记录: 静止姿态只在首次应用时确定, 之后保持, 模拟才有稳定的起点
    if asset.rest_matrix(shard) is not None:
        return
    asset.store_rest(shard, shard.matrix_world)
    log.add(lambda: asset.clear_rest(shard))


def _compound_parts(shards):
    """复合形状要拼合的子块: 碎块的直接子物体里能当刚体的网格

    Blender 的复合形状只拼直接子物体的形状, 更深的层级不参与
    shards 碎块列表
    返回 子块物体列表, 保持碎块与子物体的顺序
    """
    return [child for shard in shards for child in shard.children if child.type == "MESH"]


def _check_shape(shards, values):
    """要求复合形状的碎块都有子块: 没有子块时刚体没有形状, 碎块会直接穿过一切

    shards 全部碎块
    values 参数值
    """
    if values.collision_shape != "COMPOUND":
        return
    for shard in shards:
        if not _compound_parts([shard]):
            raise PhysicsError(
                'Shard "{name}" has no child mesh for the compound shape', name=shard.name
            )


def _build_parts(context, world, shards, log):
    """为复合形状的子块建立刚体数据: 链接进刚体世界让 Blender 建立, 再移出以免它们单独参与模拟

    刚体数据移出后仍然保留, 父级据此取到子块的形状
    context Blender 上下文
    world 刚体世界
    shards 碎块列表
    log 写前快照
    """
    parts = _compound_parts(shards)
    if not parts:
        return
    rigidbody.link(world, parts)
    rigidbody.unlink(world, parts)
    log.add(lambda: rigidbody.remove_bodies(context, parts))
    for part in parts:
        body = part.rigid_body
        if body is not None:
            log.set(body, "collision_shape", CHILD_SHAPE)


def _shard_mass(shard, volume, values, unit_cube):
    """碎块质量: 局部体积乘以物体变换的体积比与单位比例的立方, 再乘以密度

    shard 碎块物体
    volume 局部体积
    values 参数值, 提供密度
    unit_cube 每个 Blender 单位对应米数的立方
    返回 质量, kg, 不小于下限
    """
    stretch = abs(shard.matrix_world.to_3x3().determinant())
    return max(MASS_MIN, volume * stretch * unit_cube * values.density)


def _record(root, shards, values, log, summary):
    """写入资产的物理生效记录, 修订号在上一次的基础上加一

    root 资产根集合
    shards 资产的碎块列表
    values 参数值
    log 写前快照
    summary (总质量, 不封闭的碎块数)
    """
    previous = asset.physics_record(root)
    record = {key: getattr(values, key) for key in SCHEMA.specs}
    record["rev"] = (previous or {}).get("rev", 0) + 1
    record["shards"] = len(shards)
    record["mass"], record["open_meshes"] = summary
    # 撤回: 原来没有记录就清除, 有就写回旧记录
    if previous is None:
        log.add(lambda: asset.clear_physics_record(root))
    else:
        log.add(lambda: asset.set_physics_record(root, previous))
    asset.set_physics_record(root, record)


def _apply_asset(context, world, root, shards, values, log, tally):
    """处理一个资产: 建立刚体, 移原点, 记静止姿态, 写属性, 写生效记录

    context Blender 上下文
    world 刚体世界
    root 资产根集合
    shards 资产的碎块列表
    values 参数值
    log 写前快照
    tally 累计量, 本函数往里累加
    """
    # 没有刚体的碎块要新建 (链接进世界集合时由 Blender 自动建立); 撤回时整体删除
    fresh = [shard for shard in shards if shard.rigid_body is None]
    outside = [shard for shard in shards if not rigidbody.in_world(world, shard)]
    rigidbody.link(world, fresh)
    log.add(lambda: rigidbody.remove_bodies(context, fresh))
    # 不改变世界成员: 原本不在世界里的碎块移出, 撤回时它们的刚体会被整体删除
    rigidbody.unlink(world, outside)
    # 复合形状: 子块只要有刚体数据即可, 它们本身不参与模拟
    _build_parts(context, world, shards, log)
    unit_cube = rigidbody.unit_scale(context.scene) ** 3
    mass_total, open_total = 0.0, 0
    for shard in shards:
        volume, centroid, closed, degenerate = _measure(shard)
        _move_origin(shard, centroid, log)
        _record_rest(shard, log)
        mass = _shard_mass(shard, volume, values, unit_cube)
        _write_body(shard, mass, values, log)
        tally.shards += 1
        tally.mass += mass
        tally.open_meshes += not closed
        tally.degenerate += degenerate
        mass_total += mass
        open_total += not closed
    _record(root, shards, values, log, (mass_total, open_total))


def apply(context, roots, values):
    """对一组资产应用物理; 失败时撤回本次全部改动

    context Blender 上下文
    roots 资产根集合列表
    values 参数值
    返回 PhysicsResult
    """
    asset.validate(roots)
    rigidbody.check_cache(context.scene)
    # 脚本改过的变换要先求值, 下面读到的 matrix_world 才是最新的
    context.view_layer.update()
    targets = [(root, asset.shards_of(root)) for root in roots]
    # 没有碎块的资产没有东西可应用, 在改动场景之前报错
    for root, shards in targets:
        if not shards:
            raise PhysicsError('Asset "{name}" has no shards', name=root.name)
        # 复合形状缺子块时刚体没有形状, 在改动场景之前报错
        _check_shape(shards, values)
    tracker = CreatedData()
    log = WriteLog()
    try:
        world, created = rigidbody.ensure_world(context, tracker)
        if created:
            log.add(lambda: rigidbody.remove_world(context))
        tally = _Tally()
        for root, shards in targets:
            _apply_asset(context, world, root, shards, values, log, tally)
    except Exception:
        # 撤回写前快照并回收新建数据
        run_all((log.undo, tracker.rollback))
        raise
    return PhysicsResult(tally.shards, tally.mass, tally.open_meshes, tally.degenerate)


def remove(context, roots):
    """删除资产碎块的刚体数据与物理生效记录; 原点与静止姿态保留

    context Blender 上下文
    roots 资产根集合列表
    返回 处理的碎块数
    """
    asset.validate(roots)
    shards = [shard for root in roots for shard in asset.shards_of(root)]
    # 子块的刚体数据是复合形状用的, 随碎块一起删除
    parts = _compound_parts(shards)
    rigidbody.remove_bodies(context, [*shards, *parts])
    for root in roots:
        asset.clear_physics_record(root)
    return len(shards)


def reset_rest(context, roots):
    """把碎块当前的世界矩阵记为新的静止姿态, 物理修订号加一, 依赖它的模拟随之过期

    context Blender 上下文
    roots 资产根集合列表
    返回 处理的碎块数
    """
    asset.validate(roots)
    # 必须先应用过物理, 且没有模拟成果: 有成果时碎块的位置是动画给出的, 不能记为静止姿态
    for root in roots:
        if asset.physics_record(root) is None:
            raise PhysicsError('Apply physics to "{name}" first', name=root.name)
        if asset.sim_record(root) is not None:
            raise PhysicsError('Reset the simulation of "{name}" first', name=root.name)
    # 脚本改过的变换要先求值, 否则记下的是改动前的世界矩阵
    context.view_layer.update()
    count = 0
    for root in roots:
        shards = asset.shards_of(root)
        for shard in shards:
            asset.store_rest(shard, shard.matrix_world)
        # 修订号加一: 依赖旧静止姿态的模拟与绑定据此判为过期
        record = asset.physics_record(root)
        record["rev"] += 1
        asset.set_physics_record(root, record)
        count += len(shards)
    return count
