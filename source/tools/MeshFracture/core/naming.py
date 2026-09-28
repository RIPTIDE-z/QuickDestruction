"""命名计划 - 切割期分配临时名与组集合, 全部切割结束后统一命名, 名字都带上源物体名"""

from dataclasses import dataclass

import bpy

from ....common import shards

# 切割期临时名的前缀, 最终名字由 apply_names 统一分配
TEMP_NAME_PREFIX = "QDTMP_"
# 名字里源物体名最多占的 UTF-8 字节数: 默认前缀与序号下, 碎块网格名不超过 Blender 4.2 的 63 字节上限
# 前缀过长时 Blender 仍会截断名字, 截断后撞名由 Blender 加后缀区分
SOURCE_NAME_BYTES = 32


@dataclass(frozen=True)
class ShardBatch:
    """一次切割产出的碎块的身份与落点"""

    # 所属源物体的命名计划, 也提供回收账本
    naming: "NamingPlan"
    # 层号: 0 是首次切割, 大于 0 是递归细分
    level: int
    # 组号: 同一层内的第几个父块, 首次切割为 0
    group: int
    # 碎块实际落进的集合: 分组时是组集合, 否则是输出集合
    collection: object

    def new_mesh(self):
        """新建一个临时名的网格并登记到回收账本"""
        return self.naming.tracker.track("meshes", bpy.data.meshes.new(self.naming.temp_name()))

    def new_object(self, mesh, location):
        """用网格建碎块物体, 链接进本批集合并写入来源, 层号与组号标记"""
        obj = self.naming.tracker.track("objects", bpy.data.objects.new(mesh.name, mesh))
        self.collection.objects.link(obj)
        obj.location = location
        shards.mark(obj, self.naming.source_name, self.level, self.group)
        return obj


class NamingPlan:
    """一个源物体的命名与分组状态 - 首次切割直接落在输出集合, 递归时每个父块的子块自成一组"""

    def __init__(self, source_name, collection, settings, tracker):
        """记录源物体名, 输出集合与命名参数"""
        # 源物体名在首次切割前固定, 标记里记录完整名
        self.source_name = source_name
        # 名字里用的源物体名, 按 UTF-8 字节截短, 不截断多字节字符
        self.label = source_name.encode("utf-8")[:SOURCE_NAME_BYTES].decode("utf-8", "ignore")
        # 输出集合, 组集合建在它下面
        self.collection = collection
        # 碎块名前缀, 只用于碎块名
        self.prefix = settings["shard_prefix"]
        # 序号的最小位数
        self.padding = settings["shard_index_padding"]
        # 是否为递归的每个父块建立组集合
        self.use_collections = settings["use_shard_collections"]
        # 本次运行的回收账本
        self.tracker = tracker
        # 每层已经用到的组号
        self.group_counts = {}
        # 切割期临时名计数器
        self.temp_counter = 0
        # 建过的组集合, 收尾时清理被搬空的
        self.created_collections = []

    def first_batch(self):
        """首次切割的批次: 层号 0, 组号 0, 直接落在输出集合"""
        return ShardBatch(self, 0, 0, self.collection)

    def next_batch(self, level):
        """为递归的下一个父块分配组号, 按需建立组集合"""
        group = self.group_counts.get(level, 0) + 1
        self.group_counts[level] = group
        collection = self.collection
        if self.use_collections:
            # 集合名全局唯一, 带上源物体名, 多个源的组集合不会撞名
            name = f"{self.label}_L{level}_G{group}"
            collection = self.tracker.track("collections", bpy.data.collections.new(name))
            self.collection.children.link(collection)
            self.created_collections.append(collection)
        return ShardBatch(self, level, group, collection)

    def drop(self, batch):
        """放弃没有产出的批次: 退回组号, 删除刚建的空组集合"""
        self.group_counts[batch.level] -= 1
        if batch.collection is self.collection:
            return
        # 同时从列表里摘掉, 收尾清理时才不会访问已经删除的集合
        self.created_collections.remove(batch.collection)
        if not batch.collection.objects and not batch.collection.children:
            bpy.data.collections.remove(batch.collection)

    def prune(self):
        """收回被递归替换后腾空的组集合"""
        for collection in self.created_collections:
            if not collection.objects and not collection.children:
                bpy.data.collections.remove(collection)

    def temp_name(self):
        """切割期的临时名; 被丢弃的中间结果不占用最终序号"""
        self.temp_counter += 1
        return f"{TEMP_NAME_PREFIX}{self.temp_counter:06d}"

    def apply_names(self, cells):
        """全部切割结束后统一命名, 序号只按保留下来的碎块连续编, 网格名跟随物体名"""
        order = range(len(cells))
        if self.use_collections:
            # 分组时按 (层, 组) 重排, 每组从 1 起编号; 同组内保持创建顺序
            order = sorted(
                order,
                key=lambda i: (cells[i][shards.LEVEL_MARK], cells[i][shards.GROUP_MARK], i),
            )
        counters = {}
        for index in order:
            cell = cells[index]
            level, group = cell[shards.LEVEL_MARK], cell[shards.GROUP_MARK]
            # 首次切割的碎块不属于任何组, 与不分组时共用一个连续计数
            key = (level, group) if self.use_collections and level > 0 else None
            counters[key] = counters.get(key, 0) + 1
            number = f"{counters[key]:0{self.padding}d}"
            tag = f"L{level}G{group}" if key else ""
            # 非空部分用下划线连接, 例如 cell_Cube_001 与 cell_Cube_L1G1_001
            name = "_".join(part for part in (self.prefix, self.label, tag, number) if part)
            cell.name = name
            cell.data.name = name + "_mesh"
