# 翻译维护

## 数据流

| 位置 | 职责 |
| --- | --- |
| `source/CellFracture/settings.py` | 参数名称、说明及枚举项的英文原文唯一来源 |
| `source/locales/<locale>.json` | 每个独立维护的语言一份译文，按业务模块分组 |
| `source/common/localization.py` | 从参数规格生成稳定字段路径与英文消息、JSON 加载校验、目录合并及别名展开 |
| `source/i18n.py` | 模块规格与翻译上下文、语言别名、界面及报告翻译适配 |
| `source/registration.py` | 向 Blender 注册和注销最终翻译字典 |

启动时一次性读取全部语言文件，转换为 Blender 的 `locale → (context, 英文原文) → 译文` 字典。界面绘制和 `tr()` / `tr_report()` 调用不会再读取 JSON。RNA 标签和提示仍由 Blender 自动翻译，报告与界面消息分别调用 `pgettext_rpt` 和 `pgettext_iface`。

## 语言文件格式

文件名必须与 `locale` 相同，并使用 UTF-8 编码。例如 `source/locales/zh_HANS.json`：

```json
{
  "schema_version": 2,
  "locale": "zh_HANS",
  "modules": {
    "CellFracture": {
      "properties": {
        "source_limit.name": "种子点上限",
        "source_limit.description": "去重前最多保留的种子点数，0 表示全部保留。不会补充种子点，也不保证最终碎块数量。",
        "source.items.VERT_OWN.name": "自身顶点",
        "source.items.VERT_OWN.description": "使用源网格修改器之前的原始顶点，转换为世界空间坐标。"
      },
      "messages": {
        "Quick Destruction Effect": "快速破坏效果"
      }
    }
  }
}
```

示例只展示格式；正式文件的 `properties` 必须覆盖该业务模块全部预期路径。参数名称使用 `字段.name`，说明使用 `字段.description`，枚举使用 `字段.items.枚举ID.name` 和 `.description`。路径由 `PROPERTY_SPECS` 生成，英文原文也只从该规格读取，不要复制到语言 JSON。调整枚举显示顺序不会改变译文归属。

维护时可调用 `common.localization.property_sources(PROPERTY_SPECS)` 生成“稳定路径 → 当前英文原文”的参考表。英文说明改变但路径未变时，应复核该路径的译文；加载器能检查结构和占位符，不能判断语义是否仍准确。

`messages` 的键是代码中使用的英文原文，可以只翻译部分普通界面消息；缺项时保留英文。参数字段、属性和枚举项必须全部翻译。JSON 不能写注释、尾随逗号或重复对象键；加载器会拒绝重复键，避免静默覆盖。

## 修改译文

1. 在对应语言文件中按稳定字段路径或英文消息键查找译文。
2. 参数英文变化时，检查新原文的含义。参数译文仍按字段关联，但普通界面消息必须同步更改英文键。
3. 保留格式字段的名称、次数、格式规格和转换标记。例如 `{name}` 与 `{seconds:.2f}` 不能改名或改变精度，可以调整出现顺序。

当前 `zh_CN` 是 `zh_HANS` 的别名，配置在根 `i18n.LOCALE_ALIASES`，不需要维护第二份相同 JSON。若将来分别翻译两者，删除别名并新增 `zh_CN.json`。

## 新增语言和业务模块

新增语言时复制现有 JSON 为 `<locale>.json`，修改 `locale` 和译文。加载器会自动发现文件，不需要在 Python 中逐个登记语言。新增业务模块时，在 `i18n.MODULES` 中登记其参数规格与翻译上下文，并在每个语言文件的 `modules` 中新增相应分组。

RNA 使用与模块相同的 `translation_context`，布局手动文本使用相同的 `text_ctxt`。动态界面和报告消息分别通过 `tr(message, context=...)` 和 `tr_report(message, context=...)` 获取译文；`context` 是适配器参数，不能作为消息格式化字段名。

相同英文在不同业务模块中含义不同时使用独立上下文。只有确实需要默认上下文查询时才加入 `"*"`，避免模块之间争用译文。预切割模块保留 `QuickDestruction` 与 `"*"` 两个上下文以维持现有界面行为。