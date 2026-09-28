"""命名计划 - 按切割路径命名与分组, 切割期先用临时名, 收纳保留的父碎块, 全部切割结束后统一命名

路径是从源物体到碎块的逐层序号, 例如 (3, 2) 表示首次切割的第 3 块再细分出的第 2 块
碎块名由前缀, 源物体名与路径组成, 例如 cell_Cube_003_002, 去掉最后一段就是父碎块的名字
"""

from dataclasses import dataclass

import bpy

from ....common import shards

# 切割期临时名的前缀, 最终名字由 apply_names 统一分配
TEMP_NAME_PREFIX = "QDTMP_"
# 保留的父碎块所在集合名的后缀: <源物体>_Parents 下按层分为 <源物体>_Parents_L1 等
PARENTS_SUFFIX = "_Parents"
# 名字里源物体名最多占的 UTF-8 字节数: 默认前缀与序号下, 五层以内的碎块网格名不超过 Blender 4.2 的 63 字节上限
# 超出时 Blender 会截断名字, 截断后撞名由 Blender 加后缀区分
SOURCE_NAME_BYTES = 32


@dataclass(frozen=True)
class ShardBatch:
    """一次切割产出的碎块的身份与落点"""

    # 所属源物体的命名计划, 也提供回收账本
    naming: "NamingPlan"
    # 被切物体的路径: 空元组是源物体本身, 否则是父碎块的路径
    path: tuple
    # 碎块实际落进的集合: 分组时是以被切物体命名的组集合, 否则是输出集合
    collection: object

    @property
    def first(self):
        """是否为源物体的首次切割"""
        return not self.path

    def new_mesh(self):
        """新建一个临时名的网格并登记到回收账本"""
        return self.naming.tracker.track("meshes", bpy.data.meshes.new(self.naming.temp_name()))

    def new_object(self, mesh, location):
        """用网格建碎块物体并链接进本批集合; 标记在切割结束后由 number 写入"""
        obj = self.naming.tracker.track("objects", bpy.data.objects.new(mesh.name, mesh))
        self.collection.objects.link(obj)
        obj.location = location
        return obj

    def number(self, cells):
        """按顺序给本批碎块编号, 在被切物体的路径后追加序号并写入碎块标记"""
        for index, cell in enumerate(cells, 1):
            shards.mark(cell, self.naming.source_name, (*self.path, index))


class NamingPlan:
    """一个源物体的命名与分组状态 - 每次切割的产物按被切物体分组, 保留的父碎块按层收纳"""

    def __init__(self, source_name, collection, settings, tracker):
        """记录源物体名, 输出集合与命名参数"""
        # 源物体名在首次切割前固定, 标记里记录完整名
        self.source_name = source_name
        # 名字里用的源物体名, 按 UTF-8 字节截短, 不截断多字节字符
        self.label = source_name.encode("utf-8")[:SOURCE_NAME_BYTES].decode("utf-8", "ignore")
        # 输出集合, 组集合与父碎块集合都建在它下面
        self.collection = collection
        # 碎块名前缀, 只用于碎块名
        self.prefix = settings["shard_prefix"]
        # 路径里每段序号的最小位数
        self.padding = settings["shard_index_padding"]
        # 是否把每次切割的产物放进以被切物体命名的组集合
        self.use_collections = settings["use_shard_collections"]
        # 本次运行的回收账本
        self.tracker = tracker
        # 切割期临时名计数器
        self.temp_counter = 0
        # 建过的组集合, 收尾时清理被搬空的
        self.created_collections = []
        # 已被细分但保留下来的父碎块, 与碎块一起统一命名
        self.parents = []
        # 父碎块的总集合与各层集合, 第一次保留父碎块时才建立
        self.parents_root = None
        self.parent_levels = {}

    def name_of(self, path):
        """路径对应的物体名: 空路径是源物体名, 否则由前缀, 源物体名与逐层序号用下划线连接"""
        if not path:
            return self.label
        numbers = (f"{index:0{self.padding}d}" for index in path)
        return "_".join(part for part in (self.prefix, self.label, *numbers) if part)

    def _new_collection(self, name, parent):
        """新建集合, 登记到回收账本并链接到 parent 下"""
        collection = self.tracker.track("collections", bpy.data.collections.new(name))
        parent.children.link(collection)
        return collection

    def _batch(self, path):
        """为一次切割建立批次; 分组时新建以被切物体命名的组集合"""
        collection = self.collection
        if self.use_collections:
            collection = self._new_collection(self.name_of(path), self.collection)
            self.created_collections.append(collection)
        return ShardBatch(self, path, collection)

    def first_batch(self):
        """源物体首次切割的批次"""
        return self._batch(())

    def next_batch(self, parent):
        """递归细分 parent 的批次, 路径取自它的碎块标记"""
        return self._batch(tuple(parent[shards.PATH_MARK]))

    def drop(self, batch):
        """放弃没有产出的批次: 删除刚建的空组集合"""
        if batch.collection is self.collection:
            return
        # 同时从列表里摘掉, 收尾清理时才不会访问已经删除的集合
        self.created_collections.remove(batch.collection)
        if not batch.collection.objects and not batch.collection.children:
            bpy.data.collections.remove(batch.collection)

    def _parent_collection(self, level):
        """第 level 层父碎块的集合, 按需建立总集合与层集合"""
        if self.parents_root is None:
            self.parents_root = self._new_collection(
                f"{self.label}{PARENTS_SUFFIX}", self.collection
            )
        if level not in self.parent_levels:
            name = f"{self.label}{PARENTS_SUFFIX}_L{level}"
            self.parent_levels[level] = self._new_collection(name, self.parents_root)
        return self.parent_levels[level]

    def keep_parent(self, parent):
        """把已被细分的碎块移进它所在层的父碎块集合并加上标记, 此后它不再参与递归"""
        holder = self._parent_collection(parent[shards.LEVEL_MARK])
        # 先链接再解除原集合, 物体始终至少属于一个集合
        holder.objects.link(parent)
        for collection in parent.users_collection:
            if collection is not holder:
                collection.objects.unlink(parent)
        shards.mark_parent(parent)
        self.parents.append(parent)

    def prune(self):
        """收回被递归替换后腾空的组集合"""
        for collection in self.created_collections:
            if not collection.objects and not collection.children:
                bpy.data.collections.remove(collection)

    def temp_name(self):
        """切割期的临时名, 最终名字由路径决定"""
        self.temp_counter += 1
        return f"{TEMP_NAME_PREFIX}{self.temp_counter:06d}"

    def apply_names(self, objects):
        """全部切割结束后按路径统一命名, 网格名跟随物体名; 路径互不相同, 名字不会重复"""
        for obj in objects:
            name = self.name_of(tuple(obj[shards.PATH_MARK]))
            obj.name = name
            obj.data.name = name + "_mesh"
