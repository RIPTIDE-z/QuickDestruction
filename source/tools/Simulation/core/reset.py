"""复位 - 删除资产的模拟成果, 碎块回到静止姿态并移出刚体世界; 这是用户的显式操作, 与失败撤回不同"""

import bpy

from ....common import asset, rigidbody, shards
from ..constants import STAGE
from . import flight


def _clear_shard(shard, asset_id):
    """碎块去掉本插件的模拟动作并回到静止姿态; 不是本插件的动作不动"""
    data = shard.animation_data
    if data is not None and data.action is not None and asset.owned(data.action, asset_id, STAGE):
        data.action = None
    rest = asset.rest_matrix(shard)
    if rest is not None:
        shard.matrix_world = rest


def reset(context, roots):
    """复位一组资产, 返回处理的碎块数"""
    scene = context.scene
    world = scene.rigidbody_world
    count = 0
    for root in roots:
        asset_id = shards.asset_of(root)
        items = asset.shards_of(root)
        for shard in items:
            _clear_shard(shard, asset_id)
        if world is not None and world.collection is not None:
            rigidbody.unlink(world, items)
        action = asset.sim_action(root)
        asset.clear_sim_result(root)
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
