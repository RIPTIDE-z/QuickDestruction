"""阶段入口 - generate_fragments 接受显式输入并隔离 Blender 交互状态, 返回只读结果对象

source_issues 做切割前体检

make_asset_unique 给复制出来的资产换一个新标识, 让其他工具可以分别处理原资产与副本
"""

import random
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ...common import asset
from ...common.blender_context import preserve_selection
from .core import diagnosis
from .core.pipeline import run
from .errors import MeshFractureError
from .settings import SCHEMA


@dataclass(frozen=True)
class MeshFractureResult:
    """产物与来源映射 - 对象由 Blender 数据库持有"""

    # 本次生成的碎块物体; 容器只读, 物体本身仍可编辑
    shards: tuple
    # 源物体到它名下碎块的只读映射
    source_to_shards: Mapping
    # 源物体到它的资产根集合的只读映射, 后续工具按资产工作
    source_to_asset: Mapping
    # 编排耗时, 不含此后的界面刷新与选择恢复
    seconds: float


def source_issues(context, sources):
    """切割前的源网格体检

    context Blender 上下文
    sources 源物体序列, 非网格物体被忽略
    返回 {源物体: 问题集合}, 只包含有问题的网格物体
    """
    depsgraph = context.evaluated_depsgraph_get()
    found = {}
    for obj in sources:
        if obj.type != "MESH":
            continue
        issues = diagnosis.mesh_issues(obj, depsgraph)
        if issues:
            found[obj] = issues
    return found


def generate_fragments(context, sources, config, *, rng=None):
    """切割显式 sources 并在返回或失败时恢复原选择

    config 可以只给一部分字段, 缺失字段取规格默认值, 未知字段直接拒绝
    输出集合由 collection_name 决定: 空名用当前集合, 否则按名查找或新建
    context Blender 上下文, 物体模式
    sources 源物体序列, 非网格物体被忽略, 网格按名称排序处理
    config 参数字典
    rng 局部随机流; 为 None 时按 random_seed 建立
    返回 MeshFractureResult
    """
    settings = SCHEMA.resolve(config)
    if rng is None:
        rng = random.Random(settings.random_seed)
    # 固定输入快照: 后续 Blender 算子改变选择时不会改变阶段输入
    sources = tuple(sources)
    with preserve_selection(context):
        shards, by_source, by_asset, seconds = run(context, sources, settings, rng)
    # 映射包成只读视图, 调用方不能改动结果
    return MeshFractureResult(
        tuple(shards), MappingProxyType(by_source), MappingProxyType(by_asset), seconds
    )


def make_asset_unique(obj):
    """obj 所在的资产与别的集合共用标识时, 给 obj 所在的那份换一个新标识

    obj 资产里的物体, 通常是活动物体
    返回 换了标识的根集合; obj 不属于被复制的资产时抛 MeshFractureError
    """
    root = asset.duplicated_root(obj)
    if root is None:
        raise MeshFractureError("The active object is not part of a duplicated asset")
    asset.make_unique(root)
    return root
