"""界面英文原文与简体中文译文。"""

import bpy

from .qd_properties import CONTEXT, PROPERTY_MESSAGES

# 注册域名与扩展包名一致
TRANSLATIONS_DOMAIN = __package__
# 界面文案; 参数相关的译文集中在 qd_properties.py, 这里只放非参数文本
UI_MESSAGES = {
    "Quick Destruction Selected Mesh Objects": "快速破坏选中的网格物体",
    "Quick Destruction Effect": "快速破坏效果",
    "Cut selected editable meshes into shards; source objects are kept": "将选中的可编辑网格切成碎块，保留源物体",
    "QuickDestruction": "快速破坏",
    "Fracture": "切割",
    "Point Source": "种子点来源", "Recursive Shatter": "递归破碎",
    "Mesh Data": "网格数据", "Physics": "物理", "Object": "物体",
    "Debug": "调试", "Collection": "集合",
    # 面板上的两处补充说明: RNA 提示在这两个参数上取不到 (逐项/分量绘制),
    # 所以把最关键的前提直接画在分组里, 见 qd_panel._draw_group_notes()
    "Seeds are existing geometry positions, not random points": "种子取自现成的几何位置, 不是随机撒点",
    "Both seed limits act only below the parent shard vertex count; larger values change nothing":
        "两个种子上限只在小于父碎块顶点数时起作用, 再大也不会有变化",
    "Reserved for Rigid Body; this tool never creates one": "为刚体预留; 本工具不创建刚体",
    "Presets": "预设", "Preset": "预设", "Name": "名称",
    "Name of the preset": "预设名称",
    "Overwrite": "覆盖",
    "Replace the existing preset with this name": "替换同名的现有预设",
    "Save Preset": "保存预设", "Save Preset...": "保存预设…",
    "Load Preset": "加载预设", "Remove Preset": "删除预设",
    "Open Preset Directory": "打开预设目录", "No presets yet": "尚无预设",
    "Save the parameters currently shown in this panel or dialog as a JSON preset": "把当前面板或对话框显示的参数保存为 JSON 预设",
    "Load a JSON preset into the parameters currently shown here": "把 JSON 预设加载到当前界面显示的参数中",
    "Permanently delete this JSON preset after confirmation": "确认后永久删除此 JSON 预设",
    "Open the folder containing the shared JSON presets": "打开存放共享 JSON 预设的文件夹",
    "The parameter dialog has closed": "参数对话框已关闭",
    "Cannot resolve the preset directory": "无法定位预设目录",
    "Preset name is empty": "预设名称为空",
    "This preset name is reserved by Windows": "此预设名称为 Windows 保留名称",
    "Preset name is too long": "预设名称过长",
    "Preset path is outside the preset directory": "预设路径超出预设目录",
    "Preset must be a JSON object": "预设内容必须是 JSON 对象",
    "Preset file is too large": "预设文件过大",
    "Preset operation failed: {detail}": "预设操作失败：{detail}",
    "Preset directory could not be read: {detail}": "无法读取预设目录：{detail}",
    "Preset already exists; enable Overwrite to replace it": "预设已存在，请勾选“覆盖”后替换",
    'Saved preset "{name}"': '已保存预设“{name}”',
    'Removed preset "{name}"': '已删除预设“{name}”',
    "Loaded {count} parameters": "已加载 {count} 项参数",
    "Created {count} shards in {seconds:.2f} s": "已生成 {count} 个碎块，耗时 {seconds:.2f} 秒",
    "Invalid parameter: {name}": "无效参数：{name}",
    "Fracture failed: {detail}": "切割失败：{detail}",
    "Shards were created, but sidebar settings could not be synchronized: {detail}": "碎块已生成，但侧栏设置同步失败：{detail}",
    "Select editable mesh objects in Object Mode": "请在物体模式下选择可编辑的网格物体",
    'Mesh "{name}" has no faces': '网格“{name}”没有面',
    'Material slot {index} does not exist on "{name}"': '物体“{name}”不存在索引为 {index} 的材质槽',
    "Output collection must be editable and visible in the current view layer": "输出集合必须可编辑，并在当前视图层可见",
    'No valid shards were produced for "{name}"': '物体“{name}”未生成有效碎块',
    "Source geometry contains non-finite coordinates": "源几何包含非有限坐标",
    'Boolean intersection for "{name}" contains non-finite coordinates': '物体“{name}”的布尔交集包含非有限坐标',
    'Boolean intersection extends outside "{name}"; check for open or invalid source geometry': '布尔交集超出物体“{name}”的范围；请检查源网格的开口或无效几何',
}
# RNA 标签和枚举名使用属性上下文；RNA 提示与侧栏分类还需要默认上下文。
# 保留双上下文兼容现有界面路径，数据名与用户预设名不送入翻译函数。
MESSAGES = {**UI_MESSAGES, **PROPERTY_MESSAGES}
translations_dict = {
    language: {(context, source): translated
               for source, translated in MESSAGES.items()
               for context in (CONTEXT, "*")}
    for language in ("zh_HANS", "zh_CN")
}


def tr(message, **values):
    """按自定义上下文取译文, 并填入格式化参数。"""
    translated = bpy.app.translations.pgettext_iface(message, CONTEXT)
    return translated.format(**values) if values else translated


def tr_report(message, **values):
    """尊重报告翻译开关；动态字段在消息翻译之后填入。"""
    translated = bpy.app.translations.pgettext_rpt(message, CONTEXT)
    return translated.format(**values) if values else translated
