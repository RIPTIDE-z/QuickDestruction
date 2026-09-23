"""根翻译域 - 建立框架与各工具的翻译目录, 取词入口在 common/translate.py"""

from pathlib import Path

from .common.localization import build_translations, load_catalogs
from .constants import UI_CONTEXT
from .tool_registry import ordered_tools

TRANSLATIONS_DOMAIN = __package__

# 与具体工具无关的框架文案 (通用预设界面等) 所在的分组名;
# 工具不得占用它, 注册时会检查
COMMON_MODULE = "QuickDestruction"

# 模块规格与上下文由代码维护; 每种独立语言在 locales 中维护一个 JSON 文件
# 框架分组排在最前且没有参数规格, 每个工具再贡献一个模块;
# 上下文元组决定它的消息进入哪些翻译上下文
MODULES = {
    COMMON_MODULE: ((UI_CONTEXT, "*"), {}),
    **{
        tool.translation_module: ((tool.translation_context, "*"), tool.property_specs)
        for tool in ordered_tools()
    },
}
# 启动时一次性读入并校验全部语言文件, 之后绘制与报告都不再读磁盘
CATALOGS = load_catalogs(Path(__file__).resolve().parent / "locales", MODULES)
# zh_CN 与 zh_HANS 共用一套译文, 用别名避免维护两份内容相同的 JSON
LOCALE_ALIASES = {"zh_CN": "zh_HANS"}
translations_dict = build_translations(CATALOGS, aliases=LOCALE_ALIASES)
