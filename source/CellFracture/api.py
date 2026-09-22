"""预切割阶段公共入口 - 显式输入与交互状态隔离"""
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..common.blender_context import preserve_selection
from .settings import PROPERTY_SPECS, validated_values


@dataclass(frozen=True)
class FractureResult:
    """产物与来源映射 - 对象由 Blender 数据库持有"""

    shards: tuple
    source_to_shards: Mapping
    seconds: float
    diagnostics: tuple = ()


def complete_config(config):
    """从唯一规格补齐默认值并冻结运行配置"""
    values = {key: options.get('default', '') for key, (_kind, options) in PROPERTY_SPECS.items()}
    values.update(validated_values(dict(config)))
    # 复制向量和枚举集合 - 避免运行时改写调用方容器或规格默认值
    values = {key: tuple(sorted(value)) if isinstance(value, set) else value
              for key, value in validated_values(values).items()}
    return MappingProxyType(values)


def fracture(context, sources, config, output_collection, *, rng=None):
    """切割显式 sources 并在返回或失败时恢复原选择

    output_collection 接受集合对象或集合名 - None 或空名使用当前集合
    config 仅包含用户字段 - rng 与资源所有权由运行层独立管理
    """
    from .pipeline import run
    settings = complete_config(config)
    # 固定输入快照 - 后续 Blender 算子改变选择时不会改变阶段输入
    sources = tuple(sources)
    with preserve_selection(context):
        shards, by_source, seconds = run(context, sources, settings, output_collection, rng=rng)
    return FractureResult(tuple(shards), MappingProxyType(by_source), seconds)
