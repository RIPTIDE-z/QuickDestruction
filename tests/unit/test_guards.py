"""边界守卫单元测试 - 保证各工具只经由 common 的契约交流, 分层方向不被打破

测试目的:
    1. 工具包里不出现 _qd_ 开头的字符串字面量: 资产与碎块的自定义属性名只能由 common 的契约模块拼写,
       工具各自拼写会让改名或改格式时漏改
    2. 工具之间不互相导入: tools/<A> 下的模块不导入 tools/<B>, 跨工具的数据经场景数据与 common 契约传递
    3. 分层方向: common 不导入 framework 与 tools, framework 不导入 tools
测试过程:
    用 AST 解析 source 下全部源码, 相对导入按文件所在包换算成绝对模块名; 文档字符串不计入字面量检查;
    不需要 Blender
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_guards.py
"""

import ast
import logging
from pathlib import Path

LOG = logging.getLogger(__name__)
SOURCE = Path(__file__).resolve().parents[2] / "source"
# 契约键的前缀
MARK_PREFIX = "_qd_"


def package_parts(path):
    """源码文件所在的包, 以 source 为根, 例如 ['source', 'tools', 'MeshFracture', 'core']"""
    relative = path.relative_to(SOURCE.parent).with_suffix("")
    # 普通模块的包是它的上级; 包的 __init__ 去掉文件名后正好是包自己, 两者都去掉最后一段
    return list(relative.parts)[:-1]


def imported_modules(path, tree):
    """文件导入的全部 source 内模块, 相对导入换算成绝对名"""
    package = package_parts(path)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level:
                base = package[: len(package) - (node.level - 1)]
                module = [*base, *(node.module.split(".") if node.module else [])]
            else:
                module = node.module.split(".") if node.module else []
            if module and module[0] == "source":
                found.append(".".join(module))
        elif isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if alias.name.startswith("source."))
    return found


def docstring_nodes(tree):
    """模块, 类与函数的文档字符串节点"""
    owners = [tree, *(node for node in ast.walk(tree) if isinstance(node, ast.ClassDef))]
    owners += [
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]
    result = set()
    for owner in owners:
        body = getattr(owner, "body", [])
        first = body[0] if body else None
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            result.add(id(first.value))
    return result


def sources(folder):
    """某个子目录下的全部源码及其语法树"""
    return [
        (path, ast.parse(path.read_text(encoding="utf-8")))
        for path in sorted((SOURCE / folder).rglob("*.py"))
    ]


def tool_of(module):
    """模块所属的工具包名, 不在 tools 下时为 None"""
    parts = module.split(".")
    return parts[2] if len(parts) > 2 and parts[1] == "tools" else None


def test_tools_do_not_spell_marks():
    """工具包里没有 _qd_ 开头的字符串字面量"""
    violations = []
    for path, tree in sources("tools"):
        skipped = docstring_nodes(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and node.value.startswith(MARK_PREFIX)
                and id(node) not in skipped
            ):
                violations.append(f"{path.relative_to(SOURCE)}:{node.lineno} {node.value}")
    LOG.info("checked mark literals, %d violations", len(violations))
    assert not violations, violations


def test_tools_do_not_import_each_other():
    """tools/<A> 只能导入自己, framework 与 common"""
    violations = []
    for path, tree in sources("tools"):
        own = package_parts(path)[2] if len(package_parts(path)) > 2 else None
        for module in imported_modules(path, tree):
            other = tool_of(module)
            if other is not None and other != own:
                violations.append(f"{path.relative_to(SOURCE)} imports {module}")
    LOG.info("checked tool imports, %d violations", len(violations))
    assert not violations, violations


def test_layer_direction():
    """common 不导入 framework 与 tools, framework 不导入 tools"""
    forbidden = {"common": ("source.framework", "source.tools"), "framework": ("source.tools",)}
    violations = []
    for folder, prefixes in forbidden.items():
        for path, tree in sources(folder):
            for module in imported_modules(path, tree):
                if module.startswith(prefixes):
                    violations.append(f"{path.relative_to(SOURCE)} imports {module}")
    LOG.info("checked layer direction, %d violations", len(violations))
    assert not violations, violations


def test_import_resolution():
    """相对导入的换算本身要正确, 否则上面的检查会漏报"""
    path = SOURCE / "tools" / "MeshFracture" / "core" / "pipeline.py"
    modules = imported_modules(path, ast.parse(path.read_text(encoding="utf-8")))
    LOG.info("pipeline imports %s", modules)
    assert "source.common.created_data" in modules
    assert "source.tools.MeshFracture.core" in modules
