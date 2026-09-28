# 翻译维护

每个模块维护自己的语言文件，一种语言一个 JSON：

| 模块 | 位置 | 翻译上下文 |
| --- | --- | --- |
| 框架（通用预设等） | `source/framework/locales/<locale>.json` | `QuickDestruction` |
| 各工具 | `source/<Tool>/locales/<locale>.json` | `QuickDestruction.<tool_id>` |

增删工具只需要处理这个工具自己包里的语言文件，不涉及其他文件。

## 文件结构

```json
{
  "locale": "zh_HANS",
  "interface": {
    "Seed Limit": "种子点上限",
    "Created {count} shards in {seconds:.2f} s": "已生成 {count} 个碎块，耗时 {seconds:.2f} 秒"
  },
  "tooltips": {
    "Maximum sampled seed points before deduplication; ...": "去重前最多保留的种子点数……"
  }
}
```

键就是代码里的英文原文，文件名必须与 `locale` 一致，编码为 UTF-8。

- **`interface`**：面板与菜单标题、参数名、枚举项名、按钮文字、界面提示、报告与异常消息。
  - 只注册到本模块自己的上下文，不会外溢到 Blender 或其他插件；
  - 不同工具对同一英文可以有不同译法。
- **`tooltips`**：参数说明、枚举项说明，以及类的 `bl_description`。
  - Blender 在默认上下文 `*` 里查找悬停说明，所以这一组注册到 `*`；
  - 这一组都是完整句子，与其他插件撞车的可能很小。

侧栏标签 `QuickDestruction` 由 Blender 在默认上下文里翻译。它的译文写在框架文件的 `interface` 里，装配层再单独把这一条登记到 `*`，因此中文界面下标签显示为"快速破坏"。

译文中的 `{name}`、`{seconds:.2f}` 等占位符，名称、次数与格式都必须与原文一致。英文原文一改，旧译文就自然失配，单元测试会报出来。

## 运行期与测试

运行期的加载器（`common/localization.py`）是宽容的：坏文件或占位符不一致的单条译文会在控制台告警并被跳过，缺失的译文回退到英文，扩展照常启用。

严格检查放在单元测试 `tests/unit/test_locales.py` 里，运行 `uv run pytest` 即可：

- 文件结构、重复键、`locale` 与文件名一致、译文非空、占位符一致；
- `tooltips` 在各模块之间没有冲突的译法；
- **覆盖**：参数规格里的名称与说明，以及用 AST 从源码中提取的界面文字，都必须有译文。提取的范围包括：`bl_label`、`bl_description`、字面量 `text=`、`tr` 和 `tr_report` 的字面量消息、领域异常的消息、模块级 `*_MESSAGE` 常量、`ParamGroup` 的标题与提示、`*Property` 的 `name` 和 `description`；
- **陈旧**：语言文件里不能有代码已经不用的键；
- **写法约束**：
  - 字面量 `text=` 必须带 `text_ctxt`，或者声明 `translate=False`；
  - `tr` 和 `tr_report` 使用本模块的 `CONTEXT`；
  - 带 `bl_label` 的类必须写 `bl_description`。

## 增加语言或工具

- **新语言**：为框架和每个工具各复制一份现有 JSON，修改文件名、`locale` 和全部译文。
- **新工具**：在工具包里建 `locales/`，按测试报出的缺失项补齐。
- **代码取词**：界面文字用 `common/translate.py` 的 `tr(CONTEXT, message, **values)`；报告消息用 `tr_report(CONTEXT, message, **values)`，它遵守 Blender 独立的"翻译报告"开关。
