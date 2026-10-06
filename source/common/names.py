"""名字检查 - 游戏引擎对名字的限制; 绑定构建在构建前检查, 游戏导出在写文件前再查一次"""


def first_non_ascii(names):
    """找出第一个含 ASCII 以外字符的名字

    names 名字序列
    返回 名字; 全部是 ASCII 时为 None
    """
    return next((name for name in names if not name.isascii()), None)


def material_names(objects):
    """一组网格物体用到的材质名, 去重后按名字排序

    objects 网格物体序列
    返回 材质名列表, 空槽位不计
    """
    return sorted({slot.name for obj in objects for slot in obj.data.materials if slot})
