"""命名计划 - 统一分配碎块名, 层与组集合, 并写入碎块身份标记"""

import bpy

from ...common.created_data import track

_SOURCE_MARK = "_qd_source"

_LEVEL_MARK = "_qd_level"

_GROUP_MARK = "_qd_group"

TEMP_NAME_PREFIX = "QDTMP_"


class NamingPlan:
    """本次切割的命名与集合状态

    首次切割使用层号 0 并直接输出
    递归每个父块的子块对应一个 Ln_Gm 组
    完成切割后仅对保留的碎块连续编号"""

    def __init__(
        self,
        source_object,
        collection,
        use_collections=True,
        prefix="cell",
        padding=3,
        tracker=None,
    ):
        """保存构造参数并初始化本实例状态"""
        self.source_name = source_object.name
        self.collection = collection
        # 碎块名前缀, 只用于碎块名, 不进集合名
        self.prefix = prefix or ""
        self.padding = max(0, int(padding))
        self.use_collections = use_collections
        self.tracker = tracker
        # 每层已经用到第几组, 用于给新组编号
        self.group_counts = {}
        # 切割期的临时名计数器; 最终名字由 apply_names() 统一分配
        self.temp_counter = 0
        self.temp_padding = 6
        # 本次操作建过的全部组集合, 供收尾时清理空壳
        self.created_collections = []

    @staticmethod
    def group_id(tier, group):
        """集合与碎块名共用的层组标识层组之间加分隔符, 避免纯数字拼接的歧义"""
        return f"L{tier}_G{group}"

    def next_group(self, tier):
        """取该层下一个组号, 并把该层的组数推进一位"""
        # 组号在层内从 1 开始递增, 与层号一起构成 L<层>_G<组>
        group = self.group_counts.get(tier, 0) + 1
        self.group_counts[tier] = group
        return group

    def next_collection(self, tier, group):
        """建立一个组集合; 集合名只用层组号, 不加用户前缀"""
        # 不分组时返回 None, 调用方据此把碎块直接放进输出集合
        if not self.use_collections:
            return None
        coll = track(
            self.tracker, "collections", bpy.data.collections.new(self.group_id(tier, group))
        )
        self.collection.children.link(coll)
        self.created_collections.append(coll)
        return coll

    def drop_collection(self, coll, tier):
        """放弃刚建立/最终没有内容的组集合, 并把该层的组号退回去

        必须同时把引用从 created_collections 里摘掉: 否则收尾清理时会访问
        已经释放的集合, 抛 "StructRNA of type Collection has been removed"
        """
        # 平铺模式也分配了逻辑组号,空结果同样需要退回
        self.group_counts[tier] = max(0, self.group_counts.get(tier, 1) - 1)
        if coll is None:
            return
        if coll in self.created_collections:
            self.created_collections.remove(coll)
        if not coll.objects and not coll.children:
            bpy.data.collections.remove(coll)

    def prune_empty_collections(self):
        """收回被递归替换后腾空的组集合, 避免大纲里留下空壳"""
        # drop_collection 已经把放弃的集合移出列表, 这里只需处理内容被搬空的
        for coll in self.created_collections:
            if coll is not None and not coll.objects and not coll.children:
                bpy.data.collections.remove(coll)

    def temp_name(self):
        """切割过程中使用的临时名, 保证同一批里互不重名

        最终名字一律由 apply_names() 在全部切割结束后统一分配, 原因是:
        切割中途会有胞体被判空而丢弃, 甚至父碎块被递归替换后删除,
        若边切边编号, 这些被丢弃的中间结果会占掉序号, 让最终名字出现断号
        """
        self.temp_counter += 1
        # 补零宽度由 temp_padding 决定, 因此格式说明里再嵌一层宽度字段
        return f"{TEMP_NAME_PREFIX}{self.temp_counter:0{self.temp_padding}d}"

    def apply_names(self, cells):
        """全部切割结束后统一命名, 序号只按保留下来的碎块连续编

        分组时按 (层, 组) 重排, 让每组从 001 起; 不分组时整批连续编号
        cells 的顺序就是创建顺序, 因此重排是稳定的

        首次切割的碎块层号是 0 -- 它们不属于任何组 (只是暂时留在顶层集合),
        因此这类碎块一律按整批连续编号, 不会出现 L0G0 这种层组标识
        """
        if self.use_collections:
            ordered = sorted(
                range(len(cells)), key=lambda i: (cells[i][_LEVEL_MARK], cells[i][_GROUP_MARK], i)
            )
        else:
            ordered = list(range(len(cells)))
        counters = {}
        for index in ordered:
            cell = cells[index]
            tier, group = cell[_LEVEL_MARK], cell[_GROUP_MARK]
            if self.use_collections and tier > 0:
                # 分组时每个 (层, 组) 各自计数, 因此每组都从 001 起
                key = (tier, group)
                counters[key] = counters.get(key, 0) + 1
                name = f"{self.prefix}_L{tier}G{group}_{counters[key]:0{self.padding}d}"
            else:
                # tier 0 的碎块不属于任何组, 与"不分组"共用同一个连续计数
                counters[None] = counters.get(None, 0) + 1
                name = f"{self.prefix}_{counters[None]:0{self.padding}d}"
            cell.name = name
            # 网格数据跟随物体名, 便于在大纲与属性面板里对应
            if cell.data is not None:
                cell.data.name = name + "_mesh"

    @staticmethod
    def mark(shard, source_name, tier, group):
        """给碎块打上来源/层号与组号标记, 便于按属性筛选而不依赖名字"""
        # 名字可能被 Blender 因重名改写, 属性不会, 因此下游按属性筛选更可靠
        shard[_SOURCE_MARK] = source_name
        shard[_LEVEL_MARK] = tier
        shard[_GROUP_MARK] = group
