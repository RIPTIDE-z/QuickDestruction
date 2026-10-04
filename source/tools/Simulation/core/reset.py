"""复位 - 删除资产的模拟成果, 碎块回到静止姿态并移出刚体世界; 这是用户的显式操作, 与失败撤回不同"""

import bpy

from ....common import asset, rigidbody, shards
from ..constants import STAGE
from . import flight


def _clear_shard(shard, asset_id):
    """碎块去掉本插件的模拟动作并回到静止姿态; 不是本插件的动作不动

    shard 碎块物体
    asset_id 资产标识, 用来核对动作的归属
    """
    data = shard.animation_data
    # 只清本插件写的动作: 用户自己给碎块做的动画不能被复位带走
    if data is not None and data.action is not None and asset.owned(data.action, asset_id, STAGE):
        data.action = None
    # 还没有记录静止姿态时无处可回, 保持原样
    rest = asset.rest_matrix(shard)
    if rest is not None:
        shard.matrix_world = rest


def reset(context, roots):
    """复位一组资产

    context Blender 上下文
    roots 资产根集合序列
    返回 处理的碎块数
    """
    scene = context.scene
    world = scene.rigidbody_world
    count = 0
    for root in roots:
        asset_id = shards.asset_of(root)
        items = asset.shards_of(root)
        for shard in items:
            _clear_shard(shard, asset_id)
        # 碎块移出刚体世界: 模拟结束后它们由关键帧驱动, 不应继续参与模拟
        if world is not None and world.collection is not None:
            rigidbody.unlink(world, items)
        action = asset.sim_action(root)
        asset.clear_sim_result(root)
        # 成果动作删除前核对归属, 不删用户自己的动作
        if action is not None and asset.owned(action, asset_id, STAGE):
            bpy.data.actions.remove(action)
        # 模拟成果没了, 依赖它的绑定与导出集合一起清除
        asset.discard_rig(root)
        count += len(items)
    asset.prune_runs(scene)
    # 弹体的飞行动画属于驱动而不是某个资产: 没有任何模拟成果时才一并清理
    if not any(asset.sim_record(item[0]) for item in asset.roots_by_id().values()):
        flight.clear()
    return count
