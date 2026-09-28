"""阶段入口 - generate_fragments 接受显式输入并隔离 Blender 交互状态, 返回只读结果对象"""

import random
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..common.blender_context import preserve_selection
from .core.pipeline import run
from .settings import SCHEMA


@dataclass(frozen=True)
class MeshFractureResult:
    """产物与来源映射 - 对象由 Blender 数据库持有"""

    # 本次生成的碎块物体; 容器只读, 物体本身仍可编辑
    shards: tuple
    # 源物体到它名下碎块的只读映射
    source_to_shards: Mapping
    # 编排耗时, 不含此后的界面刷新与选择恢复
    seconds: float
    # 预留诊断字段, 当前恒为空元组: 失败一律通过异常报告
    diagnostics: tuple = ()


def generate_fragments(context, sources, config, output_collection, *, rng=None):
    """切割显式 sources 并在返回或失败时恢复原选择

    config 可以只给一部分字段, 缺失字段取规格默认值, 未知字段直接拒绝
    output_collection 接受集合对象或集合名, None 或空名使用当前集合
    rng 为 None 时按 random_seed 建立局部随机流
    """
    settings = SCHEMA.validate({**SCHEMA.defaults(), **config})
    if rng is None:
        rng = random.Random(settings["random_seed"])
    # 固定输入快照: 后续 Blender 算子改变选择时不会改变阶段输入
    sources = tuple(sources)
    with preserve_selection(context):
        shards, by_source, seconds = run(context, sources, settings, output_collection, rng)
    return MeshFractureResult(tuple(shards), MappingProxyType(by_source), seconds)
