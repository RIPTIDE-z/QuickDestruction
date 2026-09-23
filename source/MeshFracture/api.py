"""阶段入口 - generate_fragments 接受显式输入并隔离 Blender 交互状态, 返回只读结果对象"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from ..common.blender_context import preserve_selection
from .settings import PROPERTY_SPECS, frozen_values


@dataclass(frozen=True)
class MeshFractureResult:
    """产物与来源映射 - 对象由 Blender 数据库持有"""

    # 本次生成的碎块物体; 容器只读, 物体本身仍可编辑
    shards: tuple
    # 源物体到它名下碎块的映射, 由调用方包成只读视图
    source_to_shards: Mapping
    # 编排耗时, 不含此后的界面刷新与选择恢复
    seconds: float
    # 预留诊断字段, 当前恒为空元组: 失败一律通过异常报告
    diagnostics: tuple = ()


def complete_config(config):
    """从唯一规格补齐默认值并冻结运行配置

    默认值与调用方字段一起校验一次; 多选枚举在冻结时转成升序元组
    """
    # 先铺默认值再用调用方字段覆盖, 因此 config 允许只给一部分
    values = {key: options.get("default", "") for key, (_kind, options) in PROPERTY_SPECS.items()}
    values.update(config)
    # 只读映射: 计算层据此读取, 不允许回写配置
    return MappingProxyType(frozen_values(values))


def generate_fragments(context, sources, config, output_collection, *, rng=None):
    """切割显式 sources 并在返回或失败时恢复原选择

    output_collection 接受集合对象或集合名 - None 或空名使用当前集合
    config 仅包含用户字段 - rng 与资源所有权由运行层独立管理
    """
    # 延迟导入计算层: 导入本模块不应连带加载几何依赖
    from .core.pipeline import run

    settings = complete_config(config)
    # 固定输入快照 - 后续 Blender 算子改变选择时不会改变阶段输入
    sources = tuple(sources)
    # 选择恢复由 API 自己负责, 交互算子只负责在成功后选中产物
    with preserve_selection(context):
        shards, by_source, seconds = run(context, sources, settings, output_collection, rng=rng)
    return MeshFractureResult(tuple(shards), MappingProxyType(by_source), seconds)
