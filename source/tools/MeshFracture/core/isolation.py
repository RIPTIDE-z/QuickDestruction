"""依赖图隔离 - 递归期间把已完成的碎块移出依赖图, 每批细分只让当前一批父块与新碎块留在里面

Blender 每次刷新视图层都要按视图层里的物体总数重建依赖图关系, 碎块越多每次切割越慢, 总耗时随碎块数二次方增长
被排除的集合里的物体不在依赖图里, 不能求值也不能选中, 所以当前一批父块要先临时链接进工作集合, 新碎块也先落在那里
"""

from contextlib import contextmanager

import bpy


def _layer_node(layer, collection):
    """在视图层的集合树里找到集合对应的节点, 找不到时返回 None"""
    if layer.collection == collection:
        return layer
    for child in layer.children:
        found = _layer_node(child, collection)
        if found is not None:
            return found
    return None


class Isolation:
    """一次生成期间的依赖图隔离; enabled 为假时所有操作都不做, 结果与不隔离完全一致"""

    def __init__(self, context, tracker, enabled):
        """enabled 为假的典型情形是实时重绘: 那时要让人看到已完成的碎块"""
        self.context = context
        self.tracker = tracker
        self.enabled = enabled
        # 已排除的集合, 整批结束后统一取消
        self.hidden = []
        # 工作集合: 第一次细分时才建立
        self.work = None

    def _work_collection(self):
        """工作集合, 挂在场景根集合下并登记到回收账本"""
        if self.work is None:
            self.work = self.tracker.track("collections", bpy.data.collections.new("QD_Work"))
            self.context.scene.collection.children.link(self.work)
        return self.work

    def _hide(self, collection):
        """把集合排除出视图层; 已经排除或没有可排除的节点时什么也不做"""
        if collection is None or collection in self.hidden:
            return
        node = _layer_node(self.context.view_layer.layer_collection, collection)
        if node is not None:
            node.exclude = True
            self.hidden.append(collection)

    @contextmanager
    def around(self, parents, naming):
        """细分一批父块的作用域: 父块与新碎块都在工作集合里, 离开时新碎块进入已排除的碎块集合"""
        if not self.enabled:
            yield
            return
        self._hide(naming.shards)
        work = self._work_collection()
        # 父块之后可能被丢弃, 先记下名字用来区分父块与新碎块
        parent_names = {parent.name for parent in parents}
        for parent in parents:
            work.objects.link(parent)
        naming.landing = work
        try:
            yield
        finally:
            naming.landing = naming.shards
            for obj in list(work.objects):
                work.objects.unlink(obj)
                # 没有产出的父块留在原集合里, 只需离开工作集合
                if obj.name not in parent_names:
                    naming.shards.objects.link(obj)
            # 保留的父块集合是细分时才第一次建立的, 此后它们不再参与求交
            self._hide(naming.parents_root)

    def release(self):
        """整批结束后取消全部排除并删掉工作集合; 集合已被回滚删除时跳过"""
        if not self.enabled:
            return
        for collection in self.hidden:
            try:
                node = _layer_node(self.context.view_layer.layer_collection, collection)
            except ReferenceError:
                continue
            if node is not None:
                node.exclude = False
        self.hidden.clear()
        if self.work is not None:
            try:
                bpy.data.collections.remove(self.work)
            except ReferenceError:
                pass
            self.work = None
        self.context.view_layer.update()
