"""阶段入口 - 对显式给出的资产根集合构建绑定, 检查, 导出 FBX; 配置缺失字段取默认值

build_rig 同步执行到底; build_rig_steps 是生成器, 每个阶段产出一次进度, 供可取消的界面任务使用
构建与导出都先检查运行锁: 同一时间只允许一个长任务
"""

from ...common import asset, busy
from ...common.status import CURRENT, rig_status
from .core import check as _check
from .core import fbx
from .core.build import Progress, RigResult
from .core.build import build_steps as _build_steps
from .errors import ExportError
from .settings import SCHEMA, complete_config

__all__ = (
    "Progress",
    "RigResult",
    "build_rig",
    "build_rig_steps",
    "check",
    "existing_files",
    "export_fbx",
    "export_paths",
)

# 导出前绑定已过期时的错误
STALE_MESSAGE = "The rig is outdated; build it again"
# 导出前检查发现错误时的错误
ISSUES_MESSAGE = "The rig failed its checks: {detail}"


def _values(config):
    """补全引擎联动后校验并得到只读参数值

    config 调用方给出的字段字典, 可以只含部分字段
    返回 Values
    """
    return SCHEMA.resolve(complete_config(config))


def build_rig_steps(context, roots, config):
    """构建的生成器: 每个阶段产出一次 Progress; 调用方负责持有运行锁

    context Blender 上下文
    roots 资产根集合序列
    config 配置字典, 缺失字段取默认值
    返回 生成器, 返回值是 [RigResult, ...]
    """
    return _build_steps(context, list(roots), _values(config))


def build_rig(context, roots, config):
    """同步构建到底; 失败时场景回到构建之前, 旧导出保留

    context Blender 上下文
    roots 资产根集合序列
    config 配置字典, 缺失字段取默认值
    返回 [RigResult, ...]
    """
    with busy.holding():
        steps = build_rig_steps(context, roots, config)
        # 逐步推进到底, 生成器的返回值在 StopIteration 里
        while True:
            try:
                next(steps)
            except StopIteration as stop:
                return stop.value


def check(context, root, config, sample_frames=3):
    """检查资产的绑定

    context Blender 上下文
    root 资产根集合
    config 配置字典, 缺失字段取默认值
    sample_frames 逐帧蒙皮核对抽取的帧数, 0 表示不核对
    返回 Issue 元组; 没有构建过时抛 ExportError
    """
    busy.ensure_idle()
    asset.validate([root])
    return _check.check_rig(context, root, _values(config), sample_frames)


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


def export_fbx(context, root, config, overwrite=False):
    """导出资产的绑定; 绑定过期或检查有错误时拒绝导出

    目标文件已存在时需要 overwrite=True, 否则报错且什么都不写
    context Blender 上下文
    root 资产根集合
    config 配置字典, 缺失字段取默认值
    overwrite 为真时允许覆盖已存在的文件
    返回 (文件路径列表, Issue 元组)
    """
    busy.ensure_idle()
    asset.validate([root])
    values = _values(config)
    # 三道前置检查依次是: 构建过, 没有过期, 检查无错误
    if asset.rig_record(root) is None:
        raise ExportError(_check.NOT_BUILT_MESSAGE)
    if rig_status(root, context.scene).state != CURRENT:
        raise ExportError(STALE_MESSAGE)
    issues = _check.check_rig(context, root, values)
    errors = [issue for issue in issues if issue.level == _check.ERROR]
    # 只拿第一条错误报告: 一次说一件事, 用户修复后再检查
    if errors:
        detail = _check.issue_text(errors[0])
        raise ExportError(ISSUES_MESSAGE, detail=detail)
    paths = fbx.export_files(context, root, values, overwrite)
    # 导出记录写在资产上: 面板据它判断导出状态
    asset.set_export_record(
        root,
        {"rig": asset.rig_record(root)["id"], "files": [str(path) for path in paths]},
    )
    return paths, issues
