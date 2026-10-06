"""资产契约 - 每个源物体的破碎结果是一个资产, 以源物体集合为根

身份, 清单与各阶段的生效记录都写在根集合上
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
# 根集合上的绑定记录: 构建绑定时的模拟会话, 物理修订号, 构建参数摘要与产物统计
RIG_MARK = "_qd_rig"
# 根集合上的导出集合, ID 指针型自定义属性: 里面是骨架与蒙皮网格
RIG_COLLECTION_MARK = "_qd_rig_collection"
# 根集合上的导出记录: 对应的绑定与已写出的文件
EXPORT_MARK = "_qd_export"
# 绑定产物 (导出集合, 骨架, 蒙皮网格, 动作) 的阶段标记值
RIG_STAGE = "rig"
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
    """给新建的源物体集合写入资产清单

    root 新建的资产根集合
    source 源物体
    gap 切割间隙, 等于切面内缩量的 2 倍
    返回 新的资产标识
    """
    asset_id = uuid.uuid4().hex
    root[shards.ASSET_MARK] = asset_id
    root[FORMAT_MARK] = FORMAT
    # ID 指针引用源物体: 改名后仍然有效; 同时记下名字用于显示和旧数据识别
    root[SOURCE_REF_MARK] = source
    root[shards.SOURCE_MARK] = source.name
    root[GAP_MARK] = float(gap)
    return asset_id


def roots_by_id():
    """全部资产根集合, 按资产标识分组

    返回 {资产标识: [根集合, ...]}; 一个标识对应多个集合说明资产被复制过
    """
    index = {}
    # 资产根集合的特征是带资产标识的集合; 导出集合不带标识, 所以不会混进来
    for collection in bpy.data.collections:
        asset_id = shards.asset_of(collection)
        if asset_id is not None:
            index.setdefault(asset_id, []).append(collection)
    return index


def _root_holding(obj, roots):
    """在同一标识的几个根集合里找出真正包含该物体的那个

    obj 物体
    roots 共用同一标识的根集合列表
    返回 包含物体的根集合; 只有一个时直接返回, 都不包含时取第一个
    """
    # 只有一个根时不必遍历集合内容
    if len(roots) == 1:
        return roots[0]
    return next((root for root in roots if obj.name in root.all_objects), roots[0])


def _check_root(root, index):
    """核对资产根集合: 格式版本必须已知, 标识不能被别的集合共用

    root 资产根集合
    index roots_by_id 的结果
    """
    if root.get(FORMAT_MARK) != FORMAT:
        raise AssetError(FORMAT_MESSAGE, name=root.name)
    # 标识被多个集合共用说明整个资产被复制过, 不知道该操作哪一份
    if len(index[shards.asset_of(root)]) > 1:
        raise AssetError(DUPLICATE_ASSET_MESSAGE, name=root.name)


def _source_roots(obj, index):
    """以该物体为源的资产根集合

    obj 源物体
    index roots_by_id 的结果
    返回 根集合列表
    """
    return [root for roots in index.values() for root in roots if root.get(SOURCE_REF_MARK) == obj]


def _root_of_object(obj, index):
    """一个选中物体对应的资产根集合

    obj 选中的物体
    index roots_by_id 的结果
    返回 根集合; 与资产无关的物体返回 None
    """
    asset_id = shards.asset_of(obj)
    # 带资产标识的物体 (碎块, 父碎块) 直接按标识找根集合
    if asset_id is not None:
        roots = index.get(asset_id)
        # 标识有但根集合不在了: 碎块被从资产里搬走了
        if not roots:
            raise AssetError(ORPHAN_MESSAGE, name=obj.name)
        return _root_holding(obj, roots)
    # 有来源标记但没有资产标识: 旧版本生成的碎块, 不知道属于哪个资产
    if shards.SOURCE_MARK in obj:
        raise AssetError(LEGACY_MESSAGE, name=obj.name)
    # 其余物体只有在恰好是某个资产的源物体时才算相关
    owners = _source_roots(obj, index)
    if len(owners) > 1:
        raise AssetError(AMBIGUOUS_SOURCE_MESSAGE, name=obj.name)
    return owners[0] if owners else None


def resolve(objects):
    """由选中的物体得到资产根集合列表

    选中碎块, 父碎块或唯一对应的源物体都能找到资产
    资产被复制, 格式未知, 旧版本碎块与失去根集合的碎块都直接报错, 不做含糊的推断
    objects 选中的物体序列
    返回 按名字排序的根集合列表; 没有找到任何资产时抛 AssetError
    """
    index = roots_by_id()
    found = {}
    for obj in objects:
        root = _root_of_object(obj, index)
        if root is not None:
            found[root.name] = root
    if not found:
        raise AssetError(NO_ASSET_MESSAGE)
    # 全部找到后再统一核对, 报错时指向的是出问题的资产而不是顺序靠前的物体
    for root in found.values():
        _check_root(root, index)
    return [found[name] for name in sorted(found)]


def validate(roots):
    """核对显式给出的根集合: 必须是资产, 清单格式已知, 标识没有被别的集合共用

    阶段 API 直接接收根集合, 不经过 resolve, 所以在入口处补上这道检查
    roots 根集合序列
    """
    index = roots_by_id()
    for root in roots:
        if shards.asset_of(root) is None:
            raise AssetError(NO_ASSET_MESSAGE)
        _check_root(root, index)


def duplicated_root(obj):
    """判断物体所在的资产是否与别的集合共用标识

    obj 物体, 可以为 None
    返回 共用时是物体所在的根集合, 否则为 None
    """
    if obj is None:
        return None
    asset_id = shards.asset_of(obj)
    roots = roots_by_id().get(asset_id, []) if asset_id is not None else []
    return _root_holding(obj, roots) if len(roots) > 1 else None


def make_unique(root):
    """给复制出来的资产换一个新标识, 根集合与其中全部带旧标识的物体一起改写

    root 复制出来的资产根集合
    返回 新标识
    """
    old_id = shards.asset_of(root)
    new_id = uuid.uuid4().hex
    # 只改写带旧标识的物体: 根集合里混进来的其他物体不属于这个资产
    for obj in root.all_objects:
        if shards.asset_of(obj) == old_id:
            obj[shards.ASSET_MARK] = new_id
    root[shards.ASSET_MARK] = new_id
    # 复制品不继承原资产的各阶段成果, 需要重新应用
    for key in (
        PHYSICS_MARK,
        SIM_MARK,
        SIM_ACTION_MARK,
        RIG_MARK,
        RIG_COLLECTION_MARK,
        EXPORT_MARK,
    ):
        root.pop(key, None)
    return new_id


def shards_of(root):
    """资产的最终碎块, 不含保留下来的父碎块与调试物体

    root 资产根集合
    返回 按路径排序的碎块列表; 路径重复时抛 AssetError
    """
    asset_id = shards.asset_of(root)
    # 必须同时带本资产标识与路径标记: 排除调试点这类带标识但不是碎块的物体
    items = [
        obj
        for obj in root.all_objects
        if shards.asset_of(obj) == asset_id
        and shards.PATH_MARK in obj
        and not shards.is_parent(obj)
    ]
    items.sort(key=shards.path_of)
    # 排序后路径相同的碎块相邻, 逐对比较即可发现复制品
    for previous, current in pairwise(items):
        if shards.path_of(previous) == shards.path_of(current):
            raise AssetError(DUPLICATE_SHARDS_MESSAGE, name=root.name)
    return items


def source_of(root):
    """资产的源物体

    root 资产根集合
    返回 源物体; 源物体已被删除时为 None
    """
    return root.get(SOURCE_REF_MARK)


def flight_rest(obj):
    """弹体子物体飞行动画写入前的 location

    obj 弹体子物体
    返回 位置元组; 还没有记录时为 None
    """
    value = obj.get(FLIGHT_REST_MARK)
    return None if value is None else tuple(value)


def set_flight_rest(obj, location):
    """记下弹体子物体的原 location

    obj 弹体子物体
    location 动画写入前的位置
    """
    obj[FLIGHT_REST_MARK] = tuple(location)


def clear_flight_rest(obj):
    """去掉弹体子物体的原位置记录

    obj 弹体子物体
    """
    obj.pop(FLIGHT_REST_MARK, None)


def flight_rest_objects():
    """有原位置记录的全部物体

    返回 物体列表
    """
    return [obj for obj in bpy.data.objects if FLIGHT_REST_MARK in obj]


def gap_of(root):
    """资产的切割间隙

    root 资产根集合
    返回 间隙, 没有记录时为 0
    """
    return float(root.get(GAP_MARK, 0.0))


def rest_matrix(shard):
    """碎块的静止姿态矩阵

    shard 碎块物体
    返回 4x4 世界矩阵; 还没有记录时为 None
    """
    values = shards.rest_of(shard)
    if values is None:
        return None
    # 记录是按行展开的 16 个数, 每 4 个一行还原成矩阵
    return Matrix([values[0:4], values[4:8], values[8:12], values[12:16]])


def store_rest(shard, matrix):
    """把世界矩阵记为碎块的静止姿态

    shard 碎块物体
    matrix 世界矩阵
    """
    shards.set_rest(shard, [value for row in matrix for value in row])


def clear_rest(shard):
    """移除碎块的静止姿态记录, 用于撤回本次新记录的静止姿态

    shard 碎块物体
    """
    shards.clear_rest(shard)


def _record(holder, key):
    """读出一条字典型记录的副本

    holder 持有记录的数据块
    key 自定义属性名
    返回 普通字典; 没有记录时为 None
    """
    value = holder.get(key)
    return value.to_dict() if value is not None else None


def physics_record(root):
    """资产的物理生效记录: 应用时实际使用的配置与修订号

    root 资产根集合
    返回 记录字典; 没有应用过时为 None
    """
    return _record(root, PHYSICS_MARK)


def set_physics_record(root, record):
    """写入资产的物理生效记录

    root 资产根集合
    record 记录字典
    """
    root[PHYSICS_MARK] = record


def clear_physics_record(root):
    """移除物理生效记录; 依赖它的模拟成果随之失效

    root 资产根集合
    """
    root.pop(PHYSICS_MARK, None)


def sim_record(root):
    """资产的模拟成果记录

    root 资产根集合
    返回 记录字典; 没有成果时为 None
    """
    return _record(root, SIM_MARK)


def sim_action(root):
    """资产的模拟成果动作

    root 资产根集合
    返回 动作; 没有成果或动作已被删除时为 None
    """
    return root.get(SIM_ACTION_MARK)


def set_sim_result(root, record, action):
    """写入资产的模拟成果记录与动作引用

    root 资产根集合
    record 记录字典
    action 成果动作
    """
    root[SIM_MARK] = record
    root[SIM_ACTION_MARK] = action


def clear_sim_result(root):
    """移除资产的模拟成果记录与动作引用

    root 资产根集合
    """
    for key in (SIM_MARK, SIM_ACTION_MARK):
        root.pop(key, None)


def rig_record(root):
    """资产的绑定记录

    root 资产根集合
    返回 记录字典; 没有构建过时为 None
    """
    return _record(root, RIG_MARK)


def rig_collection(root):
    """资产的导出集合

    root 资产根集合
    返回 集合; 没有构建过或集合已被删除时为 None
    """
    return root.get(RIG_COLLECTION_MARK)


def set_rig_result(root, record, collection):
    """写入资产的绑定记录与导出集合引用

    root 资产根集合
    record 记录字典
    collection 导出集合
    """
    root[RIG_MARK] = record
    root[RIG_COLLECTION_MARK] = collection


def clear_rig_result(root):
    """移除绑定记录, 导出集合引用与导出记录: 导出记录依赖绑定, 一起失效

    root 资产根集合
    """
    for key in (RIG_MARK, RIG_COLLECTION_MARK, EXPORT_MARK):
        root.pop(key, None)


def remove_rig_collection(collection, asset_id):
    """删除导出集合里属于本资产绑定阶段的物体, 以及因此不再有使用者的物体数据与动作

    只删带本资产绑定阶段标记的物体: 用户自己放进导出集合的物体与它们的数据原样保留;
    集合删空后一并删除, 还有用户的内容时留下
    collection 导出集合
    asset_id 资产标识
    """
    objects = [obj for obj in collection.objects if owned(obj, asset_id, RIG_STAGE)]
    linked = {}
    for obj in objects:
        animation = obj.animation_data.action if obj.animation_data else None
        # 字典去重并保持顺序: 几个物体可能共用同一份数据
        linked.update(dict.fromkeys(item for item in (obj.data, animation) if item is not None))
    # 先删物体, 物体数据与动作的使用者随之减少, 之后才能判断它们是否还被别处使用
    # 批量删除: 逐个删除数据块的代价随数据库规模增长
    if objects:
        bpy.data.batch_remove(objects)
    orphans = [item for item in linked if item.users == 0]
    if not collection.objects and not collection.children:
        orphans.append(collection)
    if orphans:
        bpy.data.batch_remove(orphans)


def discard_rig(root):
    """删除资产导出集合里的绑定产物, 并清除绑定与导出记录

    模拟成果重新烘焙或复位后, 旧绑定里的动画已经与碎块对不上, 留着只会被误当成最新数据导出;
    已经写到磁盘上的 FBX 文件不动, 只是导出记录随之清除; 没有绑定时什么也不做
    root 资产根集合
    """
    collection = rig_collection(root)
    # 先清记录再删数据: 记录里的集合指针在删除后会失效
    clear_rig_result(root)
    if collection is not None:
        remove_rig_collection(collection, shards.asset_of(root))


def export_record(root):
    """资产的导出记录

    root 资产根集合
    返回 记录字典; 没有导出过时为 None
    """
    return _record(root, EXPORT_MARK)


def set_export_record(root, record):
    """写入资产的导出记录

    root 资产根集合
    record 记录字典
    """
    root[EXPORT_MARK] = record


def owned(item, asset_id, stage):
    """判断插件自有的数据块是否属于给定资产与阶段; 删除或替换前用它核对归属

    item 数据块, 例如动作
    asset_id 资产标识
    stage 阶段标记值
    返回 属于为真
    """
    return shards.asset_of(item) == asset_id and item.get(STAGE_MARK) == stage


def claim(item, asset_id, stage):
    """把新建的数据块标记为属于给定资产与阶段

    item 数据块
    asset_id 资产标识
    stage 阶段标记值
    """
    item[shards.ASSET_MARK] = asset_id
    item[STAGE_MARK] = stage


def runs(scene):
    """场景上的模拟会话记录

    scene 场景
    返回 {会话标识: 记录}; 没有时为空字典
    """
    return _record(scene, RUNS_MARK) or {}


def set_runs(scene, records):
    """整体写回场景的模拟会话记录

    scene 场景
    records {会话标识: 记录}
    """
    scene[RUNS_MARK] = records


def add_run(scene, run_id, record):
    """登记一次模拟会话, 同时清掉已经没有资产引用的旧会话

    scene 场景
    run_id 会话标识
    record 会话记录
    """
    records = runs(scene)
    records[run_id] = record
    set_runs(scene, records)
    prune_runs(scene)


def prune_runs(scene):
    """只保留仍被某个资产的模拟记录引用的会话

    scene 场景
    """
    used = set()
    # 遍历全部资产根集合, 收集它们引用的会话标识
    for roots in roots_by_id().values():
        for root in roots:
            record = sim_record(root)
            if record is not None:
                used.add(record.get("run"))
    set_runs(scene, {run_id: run for run_id, run in runs(scene).items() if run_id in used})
