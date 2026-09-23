# 翻译维护

扩展按语言维护 JSON：`source/locales/<locale>.json`。工具的参数英文原文来自各自的 `PROPERTY_SPECS`；`source/tool_registry.py` 列出工具，`source/i18n.py` 根据工具描述建立模块与上下文映射，`source/common/localization.py` 验证 JSON 并生成 Blender 翻译字典。运行时不会反复读取 JSON。

## 文件结构

```json
{
  "schema_version": 2,
  "locale": "zh_HANS",
  "modules": {
    "MeshFracture": {
      "properties": {
        "source_limit.name": "种子点上限",
        "source_limit.description": "去重前最多保留的种子点数，0 表示全部保留。"
      },
      "messages": {
        "MeshFracture": "网格破碎",
        "Generate Fragments": "生成碎块"
      }
    }
  }
}
```

示例省略了其他字段。正式文件中，`properties` 必须与该模块由规格生成的全部路径完全一致；`messages` 可只覆盖需要翻译的普通界面和报告文本。参数路径使用 `字段.name`、`字段.description`、`字段.items.枚举ID.name` 和 `.description`。可以通过 `common.localization.property_sources(PROPERTY_SPECS)` 查看完整路径与当前英文原文。

文件名必须与 `locale` 一致，编码为 UTF-8。加载器拒绝重复 JSON 键、遗漏或多出的模块与参数路径，以及不匹配的格式占位符。译文中的 `{name}`、`{seconds:.2f}` 等字段名称、出现次数和格式规格必须与英文原文一致。英文参数说明变化时，仍应复核同一路径下译文的语义；结构检查不能判断翻译是否准确。

## 增加语言或工具

- 新语言：复制现有语言 JSON，修改文件名、`locale` 和所有模块译文；不需要逐个在 Python 中登记语言。
- 新工具：定义工具描述中的 `translation_module`、`translation_context` 和 `property_specs`，加入 `tool_registry.TOOLS`，并在每个语言 JSON 的 `modules` 中增加对应分组。
- 同一英文文案在不同工具中需要不同译法时，为工具使用独立上下文。MeshFracture 当前使用 `QuickDestruction` 与 `*` 上下文。

`zh_CN` 当前是 `zh_HANS` 的别名，配置在 `i18n.LOCALE_ALIASES`，不需要第二份相同 JSON。界面消息通过 `tr()`，报告消息通过 `tr_report()` 获取译文；工具专属消息应显式传入该工具的 `context`。
