"""碎块契约 - 生成碎块的工具写入这些标记, 其他工具据此识别碎块, 不必导入生成它的工具"""

# 碎块来自哪个源物体, 记录源物体名
SOURCE_MARK = "_qd_source"
# 切割层级, 1 为首次切割, 等于路径长度
LEVEL_MARK = "_qd_level"
# 从源物体到碎块的逐层序号, 去掉最后一项就是父碎块的路径
PATH_MARK = "_qd_path"
# 已被细分但保留下来的上级碎块, 与子块几何重叠, 不算作碎块
PARENT_MARK = "_qd_parent"


def mark(shard, source_name, path):
    """给碎块写入来源, 层级与路径; 下划线开头的自定义属性在界面中隐藏"""
    shard[SOURCE_MARK] = source_name
    shard[LEVEL_MARK] = len(path)
    shard[PATH_MARK] = list(path)


def mark_parent(shard):
    """把已被细分的碎块标记为上级碎块, 它保留来源, 层级与路径"""
    shard[PARENT_MARK] = True


def parent_path(shard):
    """碎块的父碎块路径; 首次切割的碎块返回空元组, 表示父级是源物体"""
    return tuple(shard[PATH_MARK])[:-1]


def shards_of(objects, source_name):
    """筛选出某个源物体生成的全部碎块, 不含保留下来的上级碎块"""
    return [
        obj
        for obj in objects
        if obj.get(SOURCE_MARK) == source_name and not obj.get(PARENT_MARK, False)
    ]
