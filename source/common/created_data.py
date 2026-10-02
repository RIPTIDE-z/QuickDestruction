"""创建账本与写前快照 - CreatedData 记录本次新建的数据, 失败时只回收这些条目;
WriteLog 记录本次对已有数据的改动, 失败或取消时按相反顺序撤回
"""

import traceback

import bpy


class CreatedData:
    """只记录本次显式创建的数据, 便于失败时精确回收"""

    def __init__(self):
        """初始化三类 ID 列表"""
        # 键与 bpy.data 的集合名一致, 回滚时可直接 getattr(bpy.data, kind)
        self.ids = {"objects": [], "meshes": [], "collections": [], "actions": []}
        # 待删数据: 已经取消链接, 等 flush 时一次批量删除
        self.graveyard = []

    def track(self, kind, item):
        """记录本次创建的数据并原样返回, 便于写成 item = tracker.track(...)"""
        self.ids[kind].append(item)
        return item

    def bury(self, *items):
        """登记待删数据: 调用方要先取消链接; 逐个删除数据块的代价随数据库里的数据块总数增长, 攒着一起删快得多"""
        self.graveyard.extend(items)

    def flush(self):
        """一次批量删除全部待删数据, 已经失效的引用跳过"""
        alive = []
        for item in self.graveyard:
            try:
                _ = item.name
            except ReferenceError:
                continue
            alive.append(item)
        if alive:
            bpy.data.batch_remove(alive)
        # 删除成功才清空记录 - 部分删除失败时下一次仍能过滤失效引用并重试
        self.graveyard.clear()

    def rollback(self):
        """删除本次创建的数据; 已被替换或已经删除的项直接跳过"""
        # 待删数据也登记在 ids 里, 由下面统一回收
        self.graveyard.clear()
        failures = []
        # 先删物体, 网格与集合随后才可能变成无用户状态而被安全回收
        for kind, items in self.ids.items():
            # 没有登记过的类别直接跳过, 不必访问对应的数据集合
            if not items:
                continue
            data = getattr(bpy.data, kind)
            # 逆序: 子集合后创建, 先回收孩子再回收父集合
            for item in reversed(items):
                try:
                    # 名字被新数据占用说明该项已不是原来的对象
                    if data.get(item.name) != item:
                        continue
                    if kind in {"objects", "actions"}:
                        # 物体与动作必须 do_unlink, 否则仍在使用它们的地方会让删除失败
                        data.remove(item, do_unlink=True)
                    elif (kind == "meshes" and item.users == 0) or (
                        kind == "collections" and not item.objects and not item.children
                    ):
                        # 网格与集合只在没有使用者或没有内容时才删除
                        data.remove(item)
                except ReferenceError:
                    # 已经删除的数据只剩失效引用
                    continue
                except Exception as error:
                    # 单项失败不跳过其他条目 - 全部尝试后仍向调用方报告失败
                    traceback.print_exc()
                    failures.append(error)
        if failures:
            raise failures[0]


def _plain(value):
    """把自定义属性的值转成普通 Python 值, 撤回时能原样写回; 矩阵等可复制的值取副本"""
    for method in ("to_dict", "to_list", "copy"):
        convert = getattr(value, method, None)
        if callable(convert):
            return convert()
    return value


class WriteLog:
    """写前快照 - 改动已有数据之前记下原值, 失败或取消时按相反顺序撤回

    只记录本次真正改动的属性; 新建的数据由 CreatedData 回收, 两者配合使用
    """

    def __init__(self):
        """初始化撤回列表"""
        # 撤回动作, 按改动顺序排列, 撤回时逆序执行
        self.undos = []

    def set(self, owner, attribute, value):
        """记下 owner 的属性原值后写入新值"""
        old = _plain(getattr(owner, attribute))
        self.undos.append(lambda: setattr(owner, attribute, old))
        setattr(owner, attribute, value)

    def set_custom(self, holder, key, value):
        """记下自定义属性的原值后写入新值; 原来没有这个键时撤回就是删除它"""
        if key in holder:
            old = _plain(holder[key])
            self.undos.append(lambda: holder.__setitem__(key, old))
        else:
            self.undos.append(lambda: holder.pop(key, None))
        holder[key] = value

    def pop_custom(self, holder, key):
        """记下自定义属性的原值后删除它"""
        if key not in holder:
            return
        old = _plain(holder[key])
        self.undos.append(lambda: holder.__setitem__(key, old))
        holder.pop(key)

    def add(self, undo):
        """登记一个自定义的撤回动作, 用于集合成员这类不是属性赋值的改动"""
        self.undos.append(undo)

    def undo(self):
        """逆序执行全部撤回动作; 单项失败不跳过其余项, 最后抛出第一个错误"""
        failures = []
        for undo in reversed(self.undos):
            try:
                undo()
            except Exception as error:
                traceback.print_exc()
                failures.append(error)
        self.undos.clear()
        if failures:
            raise failures[0]
