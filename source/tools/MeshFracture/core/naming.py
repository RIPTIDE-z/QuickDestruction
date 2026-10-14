"""命名计划 - 按切割路径命名, 每个源物体一个集合, 最终碎块集中放置, 保留的父碎块按层收纳

路径是从源物体到碎块的逐层序号, 例如 (3, 2) 表示首次切割的第 3 块再细分出的第 2 块
碎块名由源物体名, 后缀与路径组成, 例如 Cube_cell_003_002, 去掉最后一段就是父碎块的名字
集合结构: <源物体> 下是 <源物体>_Shards
保留父碎块时另有 <源物体>_Parents 与其中的 <源物体>_Parents_L<层>
"""

from dataclasses import dataclass

import bpy

from ....common import asset, shards
from ..settings import hollows_source

# 切割期临时名的前缀, 最终名字由 apply_names 统一分配
TEMP_NAME_PREFIX = "QDTMP_"
# 源物体集合下碎块集合名的后缀
SHARDS_SUFFIX = "_Shards"
# 源物体集合下父碎块集合名的后缀
PARENTS_SUFFIX = "_Parents"
# 名字里源物体名最多占的 UTF-8 字节数
# Blender 5.2 的数据块名上限是 255 字节, 留足后缀与逐层序号的空间
# 超出时 Blender 会截断名字, 截断后撞名由 Blender 加后缀区分
SOURCE_NAME_BYTES = 64


@dataclass(frozen=True)
class ShardBatch:
    """一次切割产出的碎块的身份"""

    # 所属源物体的命名计划, 也提供回收账本与集合
    naming: "NamingPlan"
    # 被切物体的路径: 空元组是源物体本身, 否则是父碎块的路径
    path: tuple

    @property
    def first(self):
        """是否为源物体的首次切割

        返回 首次切割为真
        """
        return not self.path

    def new_mesh(self):
        """新建一个临时名的网格并登记到回收账本

        返回 网格数据
        """
        return self.naming.tracker.track("meshes", bpy.data.meshes.new(self.naming.temp_name()))

    def new_object(self, mesh, location):
        """用网格建碎块物体并链接进落点集合; 标记在切割结束后由 number 写入

        mesh 碎块的网格数据
        location 物体的位置
        返回 碎块物体
        """
        obj = self.naming.tracker.track("objects", bpy.data.objects.new(mesh.name, mesh))
        self.naming.landing.objects.link(obj)
        obj.location = location
        return obj

    def number(self, cells):
        """按顺序给本批碎块编号, 在被切物体的路径后追加序号并写入碎块标记与资产标识

        cells 本批碎块物体, 序号从 1 起按顺序给
        """
        for index, cell in enumerate(cells, 1):
            naming = self.naming
            shards.mark(cell, naming.source_name, (*self.path, index), naming.asset_id)


class NamingPlan:
    """一个源物体的命名与集合 - 最终碎块都在碎块集合里, 保留的父碎块按层收纳"""

    def __init__(self, source, collection, settings, tracker, gap=None, hollow=None):
        """记录命名参数, 在输出集合下建立源物体集合与碎块集合, 并把源物体集合登记为资产根

        source 源物体
        collection 输出集合, 源物体集合建在它下面
        settings 参数值
        tracker 回收账本
        gap 资产记录的切割间隙, 为 None 时取切面内缩量的 2 倍
        hollow 资产是否把内层曲面当作空腔, 为 None 时由参数决定
        """
        source_name = source.name
        # 源物体名在首次切割前固定, 标记里记录完整名
        self.source_name = source_name
        # 名字里用的源物体名, 按 UTF-8 字节截短, 不截断多字节字符
        self.label = source_name.encode("utf-8")[:SOURCE_NAME_BYTES].decode("utf-8", "ignore")
        # 碎块名后缀, 只用于碎块名
        self.suffix = settings.shard_suffix
        # 路径里每段序号的最小位数
        self.padding = settings.shard_index_padding
        # 本次运行的回收账本
        self.tracker = tracker
        # 切割期临时名计数器
        self.temp_counter = 0
        # 源物体集合, 本源的全部产物都在它下面; 调试几何也放在这里
        self.root = self._new_collection(self.label, collection)
        # 资产标识: 源物体集合就是资产根, 切割间隙是切面内缩量的 2 倍
        # 同时记下是否把内层曲面当作空腔: 绑定构建从源物体取静止整体时要按同样的方式理解它
        # 部件转碎块没有切割间隙, 也不涉及空腔, 由调用方指定
        gap = 2.0 * settings.margin if gap is None else gap
        hollow = hollows_source(settings) if hollow is None else hollow
        self.asset_id = asset.create_asset(self.root, source, gap, hollow)
        # 碎块集合, 切割中与最终的碎块都在这里
        self.shards = self._new_collection(f"{self.label}{SHARDS_SUFFIX}", self.root)
        # 新建的碎块落进的集合, 默认是碎块集合; 递归期间隔离模块会临时改指工作集合
        self.landing = self.shards
        # 已被细分但保留下来的父碎块, 与碎块一起统一命名
        self.parents = []
        # 父碎块的总集合, 第一次保留父碎块时才建立
        self.parents_root = None
        # 各层父碎块的集合: 层号 -> 集合, 按需建立
        self.parent_levels = {}

    def _new_collection(self, name, parent):
        """新建集合, 登记到回收账本并链接到 parent 下

        name 集合名
        parent 父集合
        返回 新集合
        """
        collection = self.tracker.track("collections", bpy.data.collections.new(name))
        parent.children.link(collection)
        return collection

    def name_of(self, path):
        """路径对应的碎块名: 源物体名, 后缀与逐层序号用下划线连接

        path 碎块路径
        返回 碎块名, 空后缀被省略
        """
        numbers = (f"{index:0{self.padding}d}" for index in path)
        return "_".join(part for part in (self.label, self.suffix, *numbers) if part)

    def first_batch(self):
        """源物体首次切割的批次

        返回 ShardBatch
        """
        return ShardBatch(self, ())

    def next_batch(self, parent):
        """递归细分 parent 的批次, 路径取自它的碎块标记

        parent 被细分的父碎块
        返回 ShardBatch
        """
        return ShardBatch(self, tuple(parent[shards.PATH_MARK]))

    def _parent_collection(self, level):
        """第 level 层父碎块的集合, 按需建立总集合与层集合

        level 层级, 首次切割为 1
        返回 集合
        """
        if self.parents_root is None:
            self.parents_root = self._new_collection(f"{self.label}{PARENTS_SUFFIX}", self.root)
        if level not in self.parent_levels:
            name = f"{self.label}{PARENTS_SUFFIX}_L{level}"
            self.parent_levels[level] = self._new_collection(name, self.parents_root)
        return self.parent_levels[level]

    def keep_parent(self, parent):
        """把已被细分的碎块移进它所在层的父碎块集合并加上标记, 此后它不再参与递归

        parent 被细分后保留下来的碎块
        """
        holder = self._parent_collection(parent[shards.LEVEL_MARK])
        # 先链接再解除原集合, 物体始终至少属于一个集合
        holder.objects.link(parent)
        for collection in parent.users_collection:
            if collection is not holder:
                collection.objects.unlink(parent)
        shards.mark_parent(parent)
        self.parents.append(parent)

    def temp_name(self):
        """切割期的临时名, 最终名字由路径决定

        返回 形如 QDTMP_000001 的名字
        """
        self.temp_counter += 1
        return f"{TEMP_NAME_PREFIX}{self.temp_counter:06d}"

    def apply_names(self, objects):
        """全部切割结束后按路径统一命名, 网格名跟随物体名; 路径互不相同, 名字不会重复

        objects 碎块与保留的父碎块
        """
        for obj in objects:
            name = self.name_of(tuple(obj[shards.PATH_MARK]))
            obj.name = name
            obj.data.name = name + "_mesh"
