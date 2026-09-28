"""创建账本 - 记录本次新建的 objects, meshes 与 collections, 失败时只回收这些条目"""

import bpy


class CreatedData:
    """只记录本次显式创建的数据, 便于失败时精确回收"""

    def __init__(self):
        """初始化三类 ID 列表"""
        # 键与 bpy.data 的集合名一致, 回滚时可直接 getattr(bpy.data, kind)
        self.ids = {"objects": [], "meshes": [], "collections": []}

    def track(self, kind, item):
        """记录本次创建的数据并原样返回, 便于写成 item = tracker.track(...)"""
        self.ids[kind].append(item)
        return item

    def rollback(self):
        """删除本次创建的数据; 已被替换或已经删除的项直接跳过"""
        # 先删物体, 网格与集合随后才可能变成无用户状态而被安全回收
        for kind, items in self.ids.items():
            data = getattr(bpy.data, kind)
            # 逆序: 子集合后创建, 先回收孩子再回收父集合
            for item in reversed(items):
                try:
                    # 名字被新数据占用说明该项已不是原来的对象
                    if data.get(item.name) != item:
                        continue
                    if kind == "objects":
                        # 物体必须 do_unlink, 否则场景中的引用会让删除失败
                        data.remove(item, do_unlink=True)
                    elif (kind == "meshes" and item.users == 0) or (
                        kind == "collections" and not item.objects and not item.children
                    ):
                        # 网格与集合只在没有使用者或没有内容时才删除
                        data.remove(item)
                except ReferenceError:
                    # 已经删除的数据只剩失效引用
                    continue
