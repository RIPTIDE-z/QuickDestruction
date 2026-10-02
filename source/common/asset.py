"""资产契约 - 每个源物体的破碎结果是一个资产, 以源物体集合为根; 身份, 清单与各阶段的生效记录都写在根集合上

各工具只经由本模块解析, 校验与读写资产, 不直接拼写自定义属性名
资产身份是根集合上的 UUID, 碎块身份是 UUID 加路径; 名字只用于显示
"""

import uuid
from itertools import pairwise

import bpy
from mathutils import Matrix

from . import shards
from .errors import DomainError

# 根集合上的清单格式版本
FORMAT_MARK = "_qd_format"
# 根集合上的源物体引用, ID 指针型自定义属性, 源物体改名后仍然有效
SOURCE_REF_MARK = "_qd_source_ref"
# 根集合上的切割间隙, 等于切面内缩量的 2 倍
GAP_MARK = "_qd_gap"
# 根集合上的物理生效记录: 应用物理时实际使用的配置与修订号
PHYSICS_MARK = "_qd_physics"
# 根集合上的模拟成果记录: 所属会话与当时的物理修订号
SIM_MARK = "_qd_sim"
# 根集合上的模拟成果动作, ID 指针型自定义属性; 它同时让动作在没有碎块使用时也不会被保存时丢弃
SIM_ACTION_MARK = "_qd_sim_action"
# 场景上的模拟会话记录: 会话标识 -> 参与者与输入快照
RUNS_MARK = "_qd_sim_runs"
# 插件自有数据块上的阶段标记, 删除或替换前按资产标识与阶段一起核对归属
STAGE_MARK = "_qd_stage"
# 弹体子物体上记下的原位置, 飞行动画写入前保存, 复位时放回
FLIGHT_REST_MARK = "_qd_flight_rest"
# 当前的清单格式版本
FORMAT = 1

# 没有选中任何资产时的消息
NO_ASSET_MESSAGE = "Select shards of a fractured asset"
# 资产集合被复制, 两个资产共用同一个标识时的消息
DUPLICATE_ASSET_MESSAGE = 'Asset "{name}" was duplicated; use Make Asset Unique in MeshFracture'
# 清单格式版本未知时的消息
FORMAT_MESSAGE = 'Asset "{name}" uses an unknown format; generate it again'
# 旧版本生成, 没有资产标识的碎块
LEGACY_MESSAGE = 'Shard "{name}" was made by an older version; generate it again'
# 碎块的资产根集合已经不存在
ORPHAN_MESSAGE = 'Shard "{name}" has lost its asset collection; generate it again'
# 一个源物体对应多个资产, 不按名字猜测
AMBIGUOUS_SOURCE_MESSAGE = 'Several assets come from "{name}"; select shards of one of them'
# 同一资产里有路径相同的碎块, 通常是复制了碎块
DUPLICATE_SHARDS_MESSAGE = 'Asset "{name}" contains copied shards; delete the copies first'


class AssetError(DomainError):
    """资产解析或校验失败 - 消息按框架上下文翻译"""


def create_asset(root, source, gap):
    """给新建的源物体集合写入资产清单, 返回新的资产标识"""
    asset_id = uuid.uuid4().hex
    root[shards.ASSET_MARK] = asset_id
    root[FORMAT_MARK] = FORMAT
    root[SOURCE_REF_MARK] = source
    root[shards.SOURCE_MARK] = source.name
    root[GAP_MARK] = float(gap)
    return asset_id


def roots_by_id():
    """全部资产根集合, 按资产标识分组; 一个标识对应多个集合说明资产被复制过"""
    index = {}
    for collection in bpy.data.collections:
        asset_id = shards.asset_of(collection)
        if asset_id is not None:
            index.setdefault(asset_id, []).append(collection)
    return index


def _root_holding(obj, roots):
    """在同一标识的几个根集合里找出真正包含该物体的那个; 只有一个时直接返回, 都不包含时取第一个"""
    if len(roots) == 1:
        return roots[0]
    return next((root for root in roots if obj.name in root.all_objects), roots[0])


def _check_root(root, index):
    """格式版本必须已知, 标识不能被别的集合共用"""
    if root.get(FORMAT_MARK) != FORMAT:
        raise AssetError(FORMAT_MESSAGE, name=root.name)
    if len(index[shards.asset_of(root)]) > 1:
        raise AssetError(DUPLICATE_ASSET_MESSAGE, name=root.name)


def _source_roots(obj, index):
    """以该物体为源的资产根集合"""
    return [root for roots in index.values() for root in roots if root.get(SOURCE_REF_MARK) == obj]


def _root_of_object(obj, index):
    """一个选中物体对应的资产根集合; 与资产无关的物体返回 None"""
    asset_id = shards.asset_of(obj)
    if asset_id is not None:
        roots = index.get(asset_id)
        if not roots:
            raise AssetError(ORPHAN_MESSAGE, name=obj.name)
        return _root_holding(obj, roots)
    if shards.SOURCE_MARK in obj:
        raise AssetError(LEGACY_MESSAGE, name=obj.name)
    owners = _source_roots(obj, index)
    if len(owners) > 1:
        raise AssetError(AMBIGUOUS_SOURCE_MESSAGE, name=obj.name)
    return owners[0] if owners else None


def resolve(objects):
    """由选中的物体得到资产根集合列表, 按名字排序; 选中碎块, 父碎块或唯一对应的源物体都能找到资产

    资产被复制, 格式未知, 旧版本碎块与失去根集合的碎块都直接报错, 不做含糊的推断
    """
    index = roots_by_id()
    found = {}
    for obj in objects:
        root = _root_of_object(obj, index)
        if root is not None:
            found[root.name] = root
    if not found:
        raise AssetError(NO_ASSET_MESSAGE)
    for root in found.values():
        _check_root(root, index)
    return [found[name] for name in sorted(found)]


def validate(roots):
    """核对显式给出的根集合: 必须是资产, 清单格式已知, 标识没有被别的集合共用

    阶段 API 直接接收根集合, 不经过 resolve, 所以在入口处补上这道检查
    """
    index = roots_by_id()
    for root in roots:
        if shards.asset_of(root) is None:
            raise AssetError(NO_ASSET_MESSAGE)
        _check_root(root, index)


def duplicated_root(obj):
    """物体所在的资产是否与别的集合共用标识; 是则返回该物体所在的根集合, 否则返回 None"""
    if obj is None:
        return None
    asset_id = shards.asset_of(obj)
    roots = roots_by_id().get(asset_id, []) if asset_id is not None else []
    return _root_holding(obj, roots) if len(roots) > 1 else None


def make_unique(root):
    """给复制出来的资产换一个新标识, 根集合与其中全部带旧标识的物体一起改写, 返回新标识"""
    old_id = shards.asset_of(root)
    new_id = uuid.uuid4().hex
    for obj in root.all_objects:
        if shards.asset_of(obj) == old_id:
            obj[shards.ASSET_MARK] = new_id
    root[shards.ASSET_MARK] = new_id
    # 复制品不继承原资产的各阶段成果, 需要重新应用
    for key in (PHYSICS_MARK, SIM_MARK, SIM_ACTION_MARK):
        root.pop(key, None)
    return new_id


def shards_of(root):
    """资产的最终碎块, 按路径排序, 不含保留下来的父碎块与调试物体; 路径重复时报错"""
    asset_id = shards.asset_of(root)
    items = [
        obj
        for obj in root.all_objects
        if shards.asset_of(obj) == asset_id
        and shards.PATH_MARK in obj
        and not shards.is_parent(obj)
    ]
    items.sort(key=shards.path_of)
    for previous, current in pairwise(items):
        if shards.path_of(previous) == shards.path_of(current):
            raise AssetError(DUPLICATE_SHARDS_MESSAGE, name=root.name)
    return items


def source_of(root):
    """资产的源物体; 源物体已被删除时为 None"""
    return root.get(SOURCE_REF_MARK)


def flight_rest(obj):
    """弹体子物体飞行动画写入前的 location; 还没有记录时为 None"""
    value = obj.get(FLIGHT_REST_MARK)
    return None if value is None else tuple(value)


def set_flight_rest(obj, location):
    """记下弹体子物体的原 location"""
    obj[FLIGHT_REST_MARK] = tuple(location)


def clear_flight_rest(obj):
    """去掉弹体子物体的原位置记录"""
    obj.pop(FLIGHT_REST_MARK, None)


def flight_rest_objects():
    """有原位置记录的全部物体"""
    return [obj for obj in bpy.data.objects if FLIGHT_REST_MARK in obj]


def gap_of(root):
    """资产的切割间隙"""
    return float(root.get(GAP_MARK, 0.0))


def rest_matrix(shard):
    """碎块的静止姿态矩阵, 还没有记录时为 None"""
    values = shards.rest_of(shard)
    if values is None:
        return None
    return Matrix([values[0:4], values[4:8], values[8:12], values[12:16]])


def store_rest(shard, matrix):
    """把世界矩阵记为碎块的静止姿态"""
    shards.set_rest(shard, [value for row in matrix for value in row])


def clear_rest(shard):
    """移除碎块的静止姿态记录, 用于撤回本次新记录的静止姿态"""
    shards.clear_rest(shard)


def _record(holder, key):
    """读出一条字典型记录的副本, 没有时为 None"""
    value = holder.get(key)
    return value.to_dict() if value is not None else None


def physics_record(root):
    """资产的物理生效记录: 应用时实际使用的配置与修订号, 没有应用过时为 None"""
    return _record(root, PHYSICS_MARK)


def set_physics_record(root, record):
    """写入资产的物理生效记录"""
    root[PHYSICS_MARK] = record


def clear_physics_record(root):
    """移除物理生效记录; 依赖它的模拟成果随之失效"""
    root.pop(PHYSICS_MARK, None)


def sim_record(root):
    """资产的模拟成果记录, 没有成果时为 None"""
    return _record(root, SIM_MARK)


def sim_action(root):
    """资产的模拟成果动作, 没有成果或动作已被删除时为 None"""
    return root.get(SIM_ACTION_MARK)


def set_sim_result(root, record, action):
    """写入资产的模拟成果记录与动作引用"""
    root[SIM_MARK] = record
    root[SIM_ACTION_MARK] = action


def clear_sim_result(root):
    """移除资产的模拟成果记录与动作引用"""
    for key in (SIM_MARK, SIM_ACTION_MARK):
        root.pop(key, None)


def owned(item, asset_id, stage):
    """插件自有的数据块是否属于给定资产与阶段; 删除或替换前用它核对归属"""
    return shards.asset_of(item) == asset_id and item.get(STAGE_MARK) == stage


def claim(item, asset_id, stage):
    """把新建的数据块标记为属于给定资产与阶段"""
    item[shards.ASSET_MARK] = asset_id
    item[STAGE_MARK] = stage


def runs(scene):
    """场景上的模拟会话记录: {会话标识: 记录}"""
    return _record(scene, RUNS_MARK) or {}


def set_runs(scene, records):
    """整体写回场景的模拟会话记录"""
    scene[RUNS_MARK] = records


def add_run(scene, run_id, record):
    """登记一次模拟会话, 同时清掉已经没有资产引用的旧会话"""
    records = runs(scene)
    records[run_id] = record
    set_runs(scene, records)
    prune_runs(scene)


def prune_runs(scene):
    """只保留仍被某个资产的模拟记录引用的会话"""
    used = set()
    for roots in roots_by_id().values():
        for root in roots:
            record = sim_record(root)
            if record is not None:
                used.add(record.get("run"))
    set_runs(scene, {run_id: run for run_id, run in runs(scene).items() if run_id in used})
