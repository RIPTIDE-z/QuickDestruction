"""创建账本 - 记录本次新建的 objects, meshes 与 collections, 失败时只回收这些条目"""

import bpy


class CreatedData:
    """只记录本次显式创建的数据, 便于失败时精确回收"""

    def __init__(self):
        """保存构造参数并初始化本实例状态"""
        # 键与 bpy.data 的集合名一致, 回滚时可直接 getattr(bpy.data, kind)
        self.ids = {"objects": [], "meshes": [], "collections": []}

    def track(self, kind, item):
        """记录本次创建的数据并返回原对象"""
        # 返回原对象, 便于写成 item = tracker.track(...) 的链式用法
        self.ids[kind].append(item)
        return item

    def rollback(self):
        """删除本次创建的数据; 已被替换或因递归移除的项直接跳过"""
        # 按 objects / meshes / collections 的插入顺序遍历: 先删物体,
        # 网格与集合随后才可能变成无用户状态而被安全回收
        for kind, items in self.ids.items():
            data = getattr(bpy.data, kind)
            # 输出集合先创建/子集合后创建;逆序才能先清空孩子再回收父集合
            for item in reversed(items):
                try:
                    # 名字被新数据占用说明该项已不是原来的对象, 不再处理
                    if data.get(item.name) != item:
                        continue
                    # 物体必须 do_unlink, 否则场景中的引用会让删除失败
                    if kind == "objects":
                        data.remove(item, do_unlink=True)
                    # 网格与集合只在没有使用者或没有内容时才能安全删除
                    elif (kind == "meshes" and item.users == 0) or (
                        kind == "collections" and not item.objects and not item.children
                    ):
                        data.remove(item)
                except ReferenceError:
                    # 递归删除后可能残留失效引用, 直接跳过
                    continue


def track(tracker, kind, item):
    """tracker 允许为空, 便于单独调用底层函数做测试"""
    return tracker.track(kind, item) if tracker is not None else item
