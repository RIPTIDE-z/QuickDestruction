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
from .core import diagnosis, parts, uv_split
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
    # 应当封闭却没有封闭的碎块; 只有按 UV 岛拆分会填写, 用于提示桥接侧壁失败
    open_shards: tuple = ()
    # 按 UV 岛拆分时被拆开的连通块数与连通块总数, 让用户看到选择实际拆了多少; 其他方式为 0
    split_parts: int = 0
    total_parts: int = 0
    # 互相重叠的碎块对 [(碎块, 碎块), ...]; 只有部件转碎块会填写, 只检测不修复, 用于提示用户
    overlaps: tuple = ()


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
    # 全部成功后才隐藏: 失败时源物体保持原样; 放在恢复选择之后, 隐藏的物体不会被重新选中
    if settings.hide_source:
        hide_sources(context, by_source)
    # 映射包成只读视图, 调用方不能改动结果
    return MeshFractureResult(
        tuple(shards), MappingProxyType(by_source), MappingProxyType(by_asset), seconds
    )


def convert_parts(context, sources, config):
    """把模型自带的部件直接当作碎块, 不切割; 输出与 generate_fragments 一致的资产

    一个网格物体时按连通块拆成部件, 选中多个物体时每个物体是一个部件, 多个物体时另合成一个源物体
    部件的几何原样保留; 部件互相重叠时不做任何修补, 只在结果的 overlaps 里列出
    config 可以只给一部分字段, 用到断面材质, 断面顶点组, 输出集合, 命名与重设原点
    context Blender 上下文, 物体模式
    sources 源物体序列, 非网格物体被忽略
    config 参数字典
    返回 MeshFractureResult; overlaps 是互相重叠的碎块对
    """
    settings = SCHEMA.resolve(config)
    sources = tuple(sources)
    with preserve_selection(context):
        outcome = parts.run(context, sources, settings)
    # 全部成功之后才隐藏源物体, 与切割一致
    if settings.hide_source:
        hide_sources(context, outcome.source_to_shards)
    return MeshFractureResult(
        tuple(outcome.shards),
        MappingProxyType(outcome.source_to_shards),
        MappingProxyType(outcome.source_to_asset),
        outcome.seconds,
        overlaps=outcome.overlaps,
    )


def convert_uv_islands(context, sources, config):
    """把每个源物体按 UV 岛拆成碎块, 不切割; 输出与 generate_fragments 一致的资产

    外层与内层的 UV 岛一一对应时配成一对并桥接侧壁, 可以只拆选中的部件 (use_uv_selected),
    不拆分的部件按 uv_unsplit 处理
    config 可以只给一部分字段, 用到 UV 层, 选中部件, 未拆分部件, 断面, 输出集合, 命名与重设原点
    context Blender 上下文, 物体模式
    sources 源物体序列, 非网格物体被忽略, 每个网格物体各成一个资产
    config 参数字典
    返回 MeshFractureResult; open_shards 列出应当封闭却没有封闭的碎块, split_parts 与 total_parts
    是被拆开的连通块数与连通块总数
    """
    settings = SCHEMA.resolve(config)
    sources = tuple(sources)
    with preserve_selection(context):
        outcome = uv_split.run(context, sources, settings)
    # 全部成功之后才隐藏源物体, 与切割一致
    if settings.hide_source:
        hide_sources(context, outcome.source_to_shards)
    return MeshFractureResult(
        tuple(outcome.shards),
        MappingProxyType(outcome.source_to_shards),
        MappingProxyType(outcome.source_to_asset),
        outcome.seconds,
        outcome.open_shards,
        outcome.split_parts,
        outcome.total_parts,
    )


def hide_sources(context, sources):
    """在视口与渲染里隐藏源物体; 不在当前视图层里的源物体只隐藏渲染

    视口隐藏用 hide_set, 与大纲里的眼睛图标一致, 用户点一下就能重新显示;
    不用 hide_viewport: 它对全部视图层生效, 而且大纲默认不显示它的开关, 用户不容易找回
    context Blender 上下文, 提供视图层
    sources 源物体序列
    """
    layer = context.view_layer.objects
    for obj in sources:
        obj.hide_render = True
        # 不在当前视图层里的物体不能 hide_set, 它本来也不显示
        if obj.name in layer:
            obj.hide_set(True)


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
