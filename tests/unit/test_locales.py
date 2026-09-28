"""语言文件单元测试 - 运行期宽容加载, 开发期由这里严格把关

测试目的:
    1. 运行期加载器: 坏文件与坏条目只跳过, interface 注册到模块上下文, tooltips 注册到默认上下文
    2. 语言文件结构: 无重复键, locale 与文件名一致, 只有三个顶层键, 译文非空且占位符一致
    3. 覆盖与陈旧: 代码里出现的界面文字都有译文, 语言文件里没有代码已经不用的键
    4. 写法约束: 字面量 text= 必须带 text_ctxt 或 translate=False, 字面量消息的 tr 调用传 CONTEXT,
       带 bl_label 的类必须写 bl_description, 否则 Blender 会把中文文档字符串当作悬停提示
测试过程:
    工具按 source/*/tool.py 发现; 参数规格从 <工具>.settings.SCHEMA 导入;
    界面文字用 AST 从源码提取, 不需要 Blender
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_locales.py
"""

import ast
import importlib
import json
import logging
from pathlib import Path

import pytest

from source.common.localization import DEFAULT_CONTEXT, build_translations, format_fields
from source.framework.constants import LOCALES, SIDEBAR_CATEGORY

LOG = logging.getLogger(__name__)
SOURCE = Path(__file__).resolve().parents[2] / "source"
# 工具包目录: 含 tool.py 的直接子目录
TOOL_DIRS = sorted(path.parent for path in SOURCE.glob("*/tool.py"))
# 会绘制文字的布局方法
LAYOUT_CALLS = frozenset({"label", "operator", "menu", "prop", "prop_enum"})


def call_name(node):
    """调用表达式的函数名, 属性调用取最后一段"""
    func = node.func
    return func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")


def literal(node):
    """字符串字面量的值, 其他表达式返回 None"""
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def keyword(node, name):
    """调用里某个关键字参数的表达式"""
    return next((item.value for item in node.keywords if item.arg == name), None)


def parse_all():
    """解析 source 下全部源码, 返回 {路径: 语法树}"""
    return {path: ast.parse(path.read_text(encoding="utf-8")) for path in SOURCE.rglob("*.py")}


def domain_errors(trees):
    """DomainError 及其全部子类的类名"""
    bases = {
        node.name: {getattr(base, "id", getattr(base, "attr", "")) for base in node.bases}
        for tree in trees.values()
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef)
    }
    found = {"DomainError"}
    while True:
        more = {name for name, parents in bases.items() if parents & found} - found
        if not more:
            return found
        found |= more


class Extractor:
    """从一个模块的源码里收集 interface 与 tooltips 文字, 并记录违反写法约束的位置"""

    def __init__(self, errors):
        """errors 是 DomainError 子类名集合"""
        # DomainError 子类名: 它们构造时的第一个参数是消息键
        self.errors = errors
        # 注册到模块上下文的文字
        self.interface = set()
        # 注册到默认上下文的悬停说明
        self.tooltips = set()
        # 违反写法约束的描述
        self.violations = []

    def visit_class(self, node, where):
        """收集 bl_label 与 bl_description, 并要求两者成对出现"""
        values = {
            target.id: literal(item.value)
            for item in node.body
            if isinstance(item, ast.Assign)
            for target in item.targets
            if isinstance(target, ast.Name)
        }
        if values.get("bl_label"):
            self.interface.add(values["bl_label"])
            if not values.get("bl_description"):
                self.violations.append(f"{where}: {node.name} has bl_label without bl_description")
        if values.get("bl_description"):
            self.tooltips.add(values["bl_description"])

    def visit_call(self, node, where):
        """按调用种类收集文字"""
        name = call_name(node)
        if name in {"tr", "tr_report"} and len(node.args) > 1 and literal(node.args[1]):
            self.interface.add(literal(node.args[1]))
            if getattr(node.args[0], "id", None) != "CONTEXT":
                self.violations.append(f"{where}: {name} must pass CONTEXT")
        elif name in self.errors and node.args and literal(node.args[0]):
            self.interface.add(literal(node.args[0]))
        elif name == "ParamGroup":
            self.interface.add(literal(node.args[0]))
            notes = keyword(node, "notes")
            self.interface.update(literal(item) for item in getattr(notes, "elts", ()))
        elif name in LAYOUT_CALLS and literal(keyword(node, "text")):
            self.interface.add(literal(keyword(node, "text")))
            translate = keyword(node, "translate")
            if keyword(node, "text_ctxt") is None and getattr(translate, "value", True):
                self.violations.append(f"{where}: literal text without text_ctxt")
        elif name.endswith("Property"):
            self.interface.update(filter(None, [literal(keyword(node, "name"))]))
            self.tooltips.update(filter(None, [literal(keyword(node, "description"))]))

    def visit(self, tree, path):
        """遍历一个文件, 另外收集模块级的 *_MESSAGE 常量"""
        for node in ast.walk(tree):
            where = f"{path.relative_to(SOURCE)}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.ClassDef):
                self.visit_class(node, where)
            elif isinstance(node, ast.Call):
                self.visit_call(node, where)
        for node in tree.body:
            if isinstance(node, ast.Assign) and literal(node.value):
                if any(getattr(target, "id", "").endswith("_MESSAGE") for target in node.targets):
                    self.interface.add(literal(node.value))


def spec_texts(tool_dir):
    """工具参数规格里的名称与说明"""
    if not (tool_dir / "settings.py").exists():
        return set(), set()
    schema = importlib.import_module(f"source.{tool_dir.name}.settings").SCHEMA
    names, descriptions = set(), set()
    for _kind, options in schema.specs.values():
        names.add(options["name"])
        descriptions.add(options["description"])
        for _identifier, name, description in options.get("items", ()):
            names.add(name)
            descriptions.add(description)
    return names, descriptions


def modules():
    """{模块名: (语言目录, 提取器)}; 框架模块包含工具包以外的全部源码"""
    trees = parse_all()
    errors = domain_errors(trees)
    result = {"framework": (LOCALES, Extractor(errors))}
    result["framework"][1].interface.add(SIDEBAR_CATEGORY)
    for tool_dir in TOOL_DIRS:
        extractor = Extractor(errors)
        names, descriptions = spec_texts(tool_dir)
        extractor.interface |= names
        extractor.tooltips |= descriptions
        result[tool_dir.name] = (tool_dir / "locales", extractor)
    for path, tree in trees.items():
        owner = next((d.name for d in TOOL_DIRS if d in path.parents), "framework")
        result[owner][1].visit(tree, path)
    return result


MODULES = modules()
LOCALE_NAMES = sorted(path.stem for path in LOCALES.glob("*.json"))


def unique_object(pairs):
    """拒绝重复 JSON 键"""
    keys = [key for key, _value in pairs]
    duplicates = sorted({key for key in keys if keys.count(key) > 1})
    assert not duplicates, f"duplicate keys {duplicates}"
    return dict(pairs)


def load(directory, locale):
    """严格读取一个语言文件"""
    path = directory / f"{locale}.json"
    assert path.exists(), f"missing {path}"
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=unique_object)


@pytest.mark.parametrize("module", sorted(MODULES))
@pytest.mark.parametrize("locale", LOCALE_NAMES)
def test_catalog_structure(module, locale):
    """结构, 译文与占位符"""
    data = load(MODULES[module][0], locale)
    assert set(data) == {"locale", "interface", "tooltips"}
    assert data["locale"] == locale
    for section in ("interface", "tooltips"):
        for source, translated in data[section].items():
            assert isinstance(translated, str) and translated, source
            assert format_fields(source) == format_fields(translated), source


@pytest.mark.parametrize("module", sorted(MODULES))
@pytest.mark.parametrize("locale", LOCALE_NAMES)
def test_catalog_coverage(module, locale):
    """代码里的文字都有译文, 语言文件里没有陈旧的键"""
    directory, extractor = MODULES[module]
    data = load(directory, locale)
    LOG.info(
        "%s/%s interface %d tooltips %d",
        module,
        locale,
        len(extractor.interface),
        len(extractor.tooltips),
    )
    for section, expected in (("interface", extractor.interface), ("tooltips", extractor.tooltips)):
        present = set(data[section])
        assert not sorted(expected - present), f"{section} missing {sorted(expected - present)}"
        assert not sorted(present - expected), f"{section} stale {sorted(present - expected)}"


@pytest.mark.parametrize("module", sorted(MODULES))
def test_source_conventions(module):
    """写法约束"""
    violations = MODULES[module][1].violations
    LOG.info("%s violations %d", module, len(violations))
    assert not violations, violations


@pytest.mark.parametrize("locale", LOCALE_NAMES)
def test_tooltips_do_not_conflict(locale):
    """全部模块的 tooltips 共用默认上下文, 同一原文不能有不同译法"""
    seen = {}
    for module, (directory, _extractor) in MODULES.items():
        for source, translated in load(directory, locale)["tooltips"].items():
            assert seen.setdefault(source, (module, translated))[1] == translated, source


def test_loader_is_lenient(tmp_path):
    """坏文件与坏条目被跳过, 其余条目按分组注册到正确的上下文"""
    good = {
        "locale": "xx",
        "interface": {"Label {n}": "L {n}", "Broken {n}": "B {m}", "Empty": ""},
        "tooltips": {"Tip": "T"},
    }
    (tmp_path / "xx.json").write_text(json.dumps(good), encoding="utf-8")
    (tmp_path / "yy.json").write_text("{not json", encoding="utf-8")
    table = build_translations([("ctx", tmp_path)])
    LOG.info("built %r", table)
    assert table == {"xx": {("ctx", "Label {n}"): "L {n}", (DEFAULT_CONTEXT, "Tip"): "T"}}


def test_real_catalogs_load_cleanly(capsys):
    """仓库里的全部语言文件都能被运行期加载器完整读入, 没有任何跳过"""
    build_translations([(name, directory) for name, (directory, _e) in MODULES.items()])
    assert "skipped" not in capsys.readouterr().out
