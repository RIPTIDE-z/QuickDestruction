# 翻译维护

每个模块自带语言文件，一种语言一个 JSON：

| 模块 | 位置 | 翻译上下文 |
| --- | --- | --- |
| 框架 | `source/framework/locales/<locale>.json` | `QuickDestruction` |
| 工具 | `source/tools/<Tool>/locales/<locale>.json` | `QuickDestruction.<tool_id>` |

## 文件格式

```json
{
  "locale": "zh_HANS",
  "interface": {"Max Pieces": "首次切割上限"},
  "tooltips": {"Maximum pieces of the first cut: ...": "首次切割最多切成的块数……"}
}
```

- **键与文件**：键是代码里的英文原文。文件名与 `locale` 一致，编码为 UTF-8。
- **`interface` 分组**：
  - 内容：标题、参数名、枚举项名、按钮与界面文字、报告与异常消息；
  - 只注册到本模块的上下文，不会外溢到 Blender 或其他插件，不同工具对同一英文可以有不同译法。
- **`tooltips` 分组**：
  - 内容：参数说明、枚举项说明与 `bl_description`；
  - Blender 在默认上下文 `*` 里查找悬停说明，所以这一组注册到 `*`。
- **侧栏标签**：`QuickDestruction` 由 Blender 在默认上下文里翻译。它的译文写在框架文件的 `interface` 里，由装配层单独登记到 `*`。
- **占位符**：`{name}`、`{seconds:.2f}` 等的名称、次数与格式必须与原文一致。

## 运行期与检查

运行期的加载是宽容的：坏文件与占位符不一致的条目会在控制台告警后跳过，缺失的译文回退到英文。

严格检查放在 `tests/unit/test_locales.py`，运行 `uv run pytest`：

- **结构**：没有重复键，`locale` 与文件名一致，译文非空，占位符一致，各模块的 `tooltips` 译法不冲突。
- **覆盖与陈旧**：
  - 必须有译文的文字：参数规格的名称与说明，以及用 AST 从源码中提取的界面文字；
  - 提取范围：`bl_label`、`bl_description`、字面量 `text=`、`tr` 和 `tr_report` 的字面量消息、领域异常的消息、模块级 `*_MESSAGE` 常量、`ParamGroup` 的标题、`*Property` 的 `name` 与 `description`；
  - 语言文件里不能有代码已经不用的键。
- **写法**：
  - 字面量 `text=` 必须带 `text_ctxt`，或写 `translate=False`；
  - `tr` 与 `tr_report` 要传本模块的 `CONTEXT`；
  - 带 `bl_label` 的类必须写 `bl_description`。

## 常见操作

- **新语言**：为框架和每个工具各复制一份 JSON，改文件名、`locale` 与译文。
- **新工具或新文字**：运行 `uv run pytest`，按报出的缺失项补齐。
- **代码取词**：界面文字用 `tr(CONTEXT, message, **values)`，报告用 `tr_report(CONTEXT, message, **values)`。后者遵守 Blender 独立的"翻译报告"开关。
