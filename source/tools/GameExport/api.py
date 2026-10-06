"""阶段入口 - 把资产的绑定导出成 FBX; 配置缺失字段取默认值

绑定由绑定构建工具建立并校验, 这里只读导出集合与绑定记录, 不导入绑定构建
导出前检查运行锁: 同一时间只允许一个长任务
"""

from ...common import asset, busy
from ...common.names import first_non_ascii
from ...common.status import CURRENT, rig_status
from .core import fbx
from .errors import ExportError
from .settings import SCHEMA, complete_config

__all__ = ("existing_files", "export_fbx", "export_paths", "needs_directory")

# 导出前绑定已过期时的错误
STALE_MESSAGE = "The rig is outdated; build it again"
# 构建时要求 ASCII 名称, 导出时名字里出现了非 ASCII 字符
NON_ASCII_MESSAGE = (
    'Name "{name}" is not ASCII; rename it or turn off ASCII Names Only in the rig settings'
)


def _values(config):
    """补全引擎联动后校验并得到只读参数值

    config 调用方给出的字段字典, 可以只含部分字段
    返回 Values
    """
    return SCHEMA.resolve(complete_config(config))


def existing_files(root, config):
    """导出会写到的文件里已经存在的

    root 资产根集合
    config 配置字典, 缺失字段取默认值
    返回 已存在的路径列表
    """
    return fbx.existing_files(root, _values(config))


def export_paths(roots, config):
    """一组资产导出会写到的全部文件路径

    roots 资产根集合序列
    config 配置字典, 缺失字段取默认值
    返回 路径列表; 不同资产会写到同一个文件时抛 ExportError
    """
    return fbx.all_targets(list(roots), _values(config))


def needs_directory(config):
    """导出目录是否还定不下来: 为空, 或是相对路径而 blend 文件还没保存

    config 配置字典, 缺失字段取默认值
    返回 定不下来为真
    """
    return fbx.directory_missing(_values(config))


def export_fbx(context, root, config, overwrite=False):
    """导出资产的绑定; 没有构建或绑定已过期时拒绝导出

    逐帧校验在构建时已经做过, 校验不通过的绑定不会提交; 构建之后改动过导出集合里的物体,
    请先在绑定构建面板里检查绑定
    目标文件已存在时需要 overwrite=True, 否则报错且什么都不写
    context Blender 上下文
    root 资产根集合
    config 配置字典, 缺失字段取默认值
    overwrite 为真时允许覆盖已存在的文件
    返回 文件路径列表
    """
    busy.ensure_idle()
    asset.validate([root])
    values = _values(config)
    record = asset.rig_record(root)
    # 两道前置检查依次是: 构建过, 没有过期
    if record is None:
        raise ExportError(fbx.NOT_BUILT_MESSAGE)
    if rig_status(root, context.scene).state != CURRENT:
        raise ExportError(STALE_MESSAGE)
    # 构建时要求过 ASCII 名称: 写文件前再查一次, 文件名与材质名可能在构建之后才改
    if record.get("ascii"):
        bad = first_non_ascii(fbx.export_names(root, values))
        if bad is not None:
            raise ExportError(NON_ASCII_MESSAGE, name=bad)
    paths = fbx.export_files(context, root, values, overwrite)
    # 导出记录写在资产上: 面板据它判断导出状态
    asset.set_export_record(root, {"rig": record["id"], "files": [str(path) for path in paths]})
    return paths
