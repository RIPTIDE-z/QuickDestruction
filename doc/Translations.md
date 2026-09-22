# 翻译维护

## 职责

| 文件 | 维护内容 |
| --- | --- |
| `CellFracture/settings.py` | 参数英文标签、提示和枚举原文的唯一来源 |
| `CellFracture/translations.py` | 按参数字段 / 枚举 ID 组织的译文、界面消息、语言目录 |
| `common/localization.py` | 纯 Python 校验、参数消息生成、模块合并与语言别名 |
| `i18n.py` | 显式目录装配、根翻译域、界面 / 报告翻译适配 |
| `registration.py` | 向 Blender 注册及注销最终字典 |

最终格式保持 Blender 原生结构: `locale → (context, 英文原文) → 译文`
英文作为运行时消息键, 参数字段名只用于维护译文

## 修改参数译文

在 `ZH_HANS_PROPERTIES` 中按字段查找, 不复制英文提示

```python
"recursion_chance": {
    "name": "递归参与比例",
    "description": "每轮选中当前碎块的比例，数量向下取整；0 不选，1 全选。具体碎块由递归选择方式决定。",
},
```

枚举通过 `items[枚举ID].name` 和 `.description` 定位
英文修改后自动生成新的 Blender 消息键, 同时人工复核译文语义
自动校验只能发现结构错误, 不能判断译文是否准确

## 新增语言

1. 在业务翻译模块新增该语言的参数表与界面消息表
2. 使用 `property_messages(PROPERTY_SPECS, 参数表)` 生成参数消息
3. 使用 `merge_messages(参数消息, 界面消息)` 合并
4. 将结果加入 `CATALOGS[语言标识]`

参数表必须完整覆盖该阶段的字段、属性和枚举
界面消息缺译时沿用 Blender 的英文回退, 不要求每种语言覆盖所有界面消息
当前 `zh_CN` 是 `zh_HANS` 的别名, 在根 `LOCALE_ALIASES` 中维护
若未来要分别维护两者, 删除别名并提供两个独立目录

## 新增业务模块

业务模块提供 `CATALOGS`, 在根 `i18n.CATALOGS` 显式加入:

```python
("阶段名", ("阶段上下文",), STAGE_CATALOGS),
```

RNA 使用相同 `translation_context`, 布局使用相同 `text_ctxt`
手动翻译可调用 `tr(message, context="阶段上下文")` 或 `tr_report(...)`
`context` 为适配器保留参数, 不作为消息格式化字段名

相同英文在不同阶段含义不同时使用独立上下文
只有确实需要默认上下文查询时才加入 `"*"`, 避免不同阶段互相争用译文
预切割保留当前 `QuickDestruction` 与 `"*"` 双上下文以维持界面行为

## 校验规则

| 情况 | 处理 |
| --- | --- |
| 参数漏译、多余字段、枚举 ID 不匹配 | 目录构建时报错 |
| 同语言 / 上下文 / 原文存在不同译文 | 报错, 不静默覆盖 |
| 相同译文重复出现 | 合并 |
| `{name}` 或 `{seconds:.2f}` 的名称、次数、格式、转换标记变化 | 报错, 允许调整占位符顺序 |
| 空译文或非字符串译文 | 报错 |
| 语言别名指向不存在的语言或覆盖已有目录 | 报错 |

界面与报告分别调用 `pgettext_iface` / `pgettext_rpt`, 保持 Blender 翻译开关语义
RNA 标签与 tooltip 继续由 Blender 自动翻译, 不预先写入中文
纯翻译目录可在普通 Python 中导入和检查, 不需要启动 Blender

## 格式与验收

使用项目配置执行 `uv run ruff format`, 中文译文尽量保持完整句子
本轮 13 项专项检查通过, 138 条消息与原目录一致, 双版本 RNA / 翻译 / 几何基线一致
本地检查为 `tests/check_localization.py`, 日志与包位于 `tests/modularization/M8_i18n`

参考 [Blender 翻译 API](https://docs.blender.org/api/5.1/bpy.app.translations.html) 和 [翻译开发指南](https://developer.blender.org/docs/handbook/translating/developer_guide/)
