"""本次新建 Blender 数据的所有权与回收"""

import bpy


class CreatedData:
    """只记录本次显式创建的数据, 便于失败时精确回收"""

    def __init__(self):
        """保存构造参数并初始化本实例状态"""
        self.ids = {"objects": [], "meshes": [], "collections": []}

    def track(self, kind, item):
        """记录本次创建的数据并返回原对象"""
        self.ids[kind].append(item)
        return item

    def rollback(self):
        """删除本次创建的数据; 已被替换或因递归移除的项直接跳过"""
        for kind, items in self.ids.items():
            data = getattr(bpy.data, kind)
            # 输出集合先创建/子集合后创建;逆序才能先清空孩子再回收父集合
            for item in reversed(items):
                try:
                    # 名字被新数据占用说明该项已不是原来的对象, 不再处理
                    if data.get(item.name) != item:
                        continue
                    if kind == "objects":
                        data.remove(item, do_unlink=True)
                    elif (kind == "meshes" and item.users == 0) or (
                        kind == "collections" and not item.objects and not item.children
                    ):
                        data.remove(item)
                except ReferenceError:
                    continue


def track(tracker, kind, item):
    """tracker 允许为空, 便于单独调用底层函数做测试"""
    return tracker.track(kind, item) if tracker is not None else item
