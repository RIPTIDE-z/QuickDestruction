"""碎块契约 - 生成碎块的工具写入这些标记, 其他工具据此识别碎块, 不必导入生成它的工具

碎块身份 = 资产标识 + 路径; 名字只用于显示, 不作为身份
本模块只读写下标访问的标记, 不依赖 Blender, 单元测试可以用字典充当物体
"""

# 碎块来自哪个源物体, 记录生成时的源物体名, 只用于显示
SOURCE_MARK = "_qd_source"
# 切割层级, 1 为首次切割, 等于路径长度
LEVEL_MARK = "_qd_level"
# 从源物体到碎块的逐层序号, 去掉最后一项就是父碎块的路径
PATH_MARK = "_qd_path"
# 已被细分但保留下来的父碎块, 与子块几何重叠, 不算作碎块
PARENT_MARK = "_qd_parent"
# 所属资产的标识, 与资产根集合上的同名标记相同
ASSET_MARK = "_qd_asset"
# 静止姿态: 首次应用物理时记录的世界矩阵, 按行展开的 16 个浮点数
REST_MARK = "_qd_rest"
# 碎块网格上的断面面属性: 布尔型, 面域, 为真的面是切割时新生成的内部断面; 下游据此给断面铺 UV 与材质
INTERIOR_ATTRIBUTE = "qd_interior"
# 碎块网格上的断面已展开标记: 布尔型, 面域, 为真的断面已经有自己的 UV (例如按 UV 岛拆分时展开的侧壁长条),
# 绑定构建的盒投影跳过它们
INTERIOR_UV_ATTRIBUTE = "qd_interior_uv"


def mark(shard, source_name, path, asset_id):
    """给碎块写入来源, 层级, 路径与所属资产; 下划线开头的自定义属性在界面中隐藏

    shard 碎块物体
    source_name 源物体名, 只用于显示
    path 从源物体到碎块的逐层序号, 层级等于它的长度
    asset_id 所属资产的标识
    """
    shard[SOURCE_MARK] = source_name
    shard[LEVEL_MARK] = len(path)
    # 自定义属性不支持元组, 存成列表, 读取时再转回元组
    shard[PATH_MARK] = list(path)
    shard[ASSET_MARK] = asset_id


def mark_parent(shard):
    """把已被细分的碎块标记为父碎块, 它保留来源, 层级, 路径与所属资产

    shard 被细分后保留下来的碎块
    """
    shard[PARENT_MARK] = True


def is_parent(shard):
    """是否为保留下来的父碎块

    shard 碎块物体
    返回 是父碎块为真
    """
    return bool(shard.get(PARENT_MARK, False))


def asset_of(item):
    """碎块或资产根集合所属的资产标识

    item 碎块物体或资产根集合
    返回 资产标识, 没有标记时为 None
    """
    return item.get(ASSET_MARK)


def path_of(shard):
    """碎块的路径, 也是它在资产内的身份

    shard 碎块物体
    返回 路径元组
    """
    return tuple(shard[PATH_MARK])


def parent_path(shard):
    """碎块的父碎块路径

    shard 碎块物体
    返回 父碎块的路径元组; 首次切割的碎块返回空元组, 表示父级是源物体
    """
    return path_of(shard)[:-1]


def shards_of(objects, source_name):
    """筛选出某个源物体生成的全部碎块, 不含保留下来的父碎块

    objects 候选物体序列
    source_name 源物体名
    返回 碎块物体列表, 保持输入顺序
    """
    return [obj for obj in objects if obj.get(SOURCE_MARK) == source_name and not is_parent(obj)]


def rest_of(shard):
    """碎块的静止姿态

    shard 碎块物体
    返回 按行展开的 16 个浮点数元组; 还没有记录时为 None
    """
    values = shard.get(REST_MARK)
    return tuple(values) if values is not None else None


def set_rest(shard, values):
    """记录碎块的静止姿态

    shard 碎块物体
    values 按行展开的 16 个浮点数
    """
    # 统一转成 float: 自定义属性按类型存储, 整数与浮点混用会让读回的类型不一致
    shard[REST_MARK] = [float(value) for value in values]


def clear_rest(shard):
    """移除碎块的静止姿态记录

    shard 碎块物体, 没有记录时什么也不做
    """
    shard.pop(REST_MARK, None)
