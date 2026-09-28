"""框架标识 - 框架文案的翻译上下文, 侧栏分类名与框架语言文件目录"""

from pathlib import Path

# 框架文案的翻译上下文, 也是各工具上下文的前缀
CONTEXT = "QuickDestruction"
# 3D 视图侧栏的标签页名; 中文界面下由 Blender 在默认上下文里翻译, 装配层为它单独登记译文
SIDEBAR_CATEGORY = "QuickDestruction"
# 框架文案的语言文件目录
LOCALES = Path(__file__).with_name("locales")
