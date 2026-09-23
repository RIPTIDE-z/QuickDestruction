# 架构与接口

## 模块边界

| 位置 | 职责 |
| --- | --- |
| `source/__init__.py` | Blender 扩展的 `register()` / `unregister()` 入口。 |
| `source/tool_contract.py`, `tool_registry.py` | 工具描述结构与有序工具清单。 |
| `source/registration.py` | 校验类和 RNA ID 唯一性；按依赖顺序注册，逆序注销，注册失败时回滚已完成步骤。 |
| `source/properties.py`, `panel.py`, `constants.py` | 场景参数聚合根、侧栏父面板与扩展级 UI 标识。 |
| `source/i18n.py`, `locales/*.json` | 根据工具清单建立 Blender 翻译目录；每个语言一个 JSON。 |
| `source/common/` | 参数校验和 RNA 生成、翻译目录构建、JSON 存储、Blender 上下文和本次创建数据的回收。它不导入业务工具。 |
| `source/CellFracture/tool.py` | Cell Fracture 的参数组、交互类、菜单和文件加载回调声明。 |
| `source/CellFracture/settings.py`, `properties.py` | 29 项参数规格与 Blender 属性组。 |
| `source/CellFracture/panel.py`, `operators.py`, `presets.py`, `preset_format.py` | 工具子面板、切割算子、预设操作与预设格式校验。 |
| `source/CellFracture/api.py`, `pipeline.py` | 显式阶段 API 和递归切割编排。 |
| `source/CellFracture/sampling.py`, `cells.py`, `boolean.py`, `mesh_ops.py`, `naming.py` | 采样、胞体、布尔策略、网格执行及输出命名。 |

根装配导入各工具的声明；业务工具可调用 `common`，工具之间不互相导入。`tool.py` 延迟加载本工具的交互类，避免翻译目录与 UI 模块形成导入环。`common/created_data.py` 只跟踪当前切割创建的 objects、meshes 和 collections，不提供对既有 Blender 数据的完整事务回滚。

## 工具接入

`tool_registry.TOOLS` 是唯一的工具顺序来源。每个工具提供一个 `ToolSpec`：

| 字段 | 用途 |
| --- | --- |
| `tool_id`、`translation_module`、`translation_context` | 工具身份与语言文件中的模块、翻译上下文。 |
| `property_specs` | 本工具的参数英文原文，供翻译目录使用；可以为空。 |
| `classes` | 返回本工具的算子、菜单和子面板类；根装配负责注册。 |
| `settings_attr`、`settings_class` | 可选的场景持久化参数组；两者必须同时提供。 |
| `menu_hooks`、`load_pre`、`clear_sessions` | 可选的菜单入口、文件加载前回调和会话清理。 |

新增工具时，在独立业务包内声明 `ToolSpec`，将其加入 `tool_registry.TOOLS`，并在每个语言 JSON 的 `modules` 中增加对应分组。需要持久化参数时，在描述中同时声明属性组类和 `QDSettings` 字段名；需要侧栏 UI 时将面板设为 `QD_PT_main` 的子面板。注册层会先注册工具属性组和 `QDSettings`，再挂 `Scene.qd_settings`，然后注册父面板、工具交互类、菜单和 handler。注销顺序相反。

现有场景数据路径 `Scene.qd_settings.fracture`、Cell Fracture 算子 ID `object.add_quick_destruction_objects` 和子面板 ID `QD_PT_settings` 保持不变。`QDSettings.preset_name` 仍为 Cell Fracture 保留，以读取已有 `.blend` 文件中的预设名；新工具应使用自己的名称状态。

## Cell Fracture 阶段 API

从已安装扩展的 `CellFracture.api` 导入：

```python
fracture(context, sources, config, output_collection, *, rng=None)
```

| 参数 | 契约 |
| --- | --- |
| `context` | Blender 物体模式上下文，包含场景和视图层。 |
| `sources` | 显式源物体序列；非网格忽略。 |
| `config` | 用户参数映射；缺字段从 `PROPERTY_SPECS` 补齐。 |
| `output_collection` | 输出集合对象或名称；`None` / 空名使用当前集合。 |
| `rng` | 可选局部随机流，提供时按调用顺序消费。 |

API 不从 `config.collection_name` 隐式决定输出位置。交互算子显式传入该字段。调用完成或失败后，API 恢复原选择和活动物体；交互算子在成功后主动选择产物。

`FractureResult.shards` 是碎块对象元组，`source_to_shards` 是只读映射，`seconds` 是编排耗时，`diagnostics` 当前为空元组。容器只读不等于 Blender 对象不可编辑。`complete_config()` 返回从规格补齐的只读运行配置。

## 预设

`common/preset_storage.py` 负责安全文件名、大小限制、JSON 读写与原子保存；工具负责验证字段、格式和归属。Cell Fracture 新预设位于 Blender 用户脚本目录下的 `quick_destruction/presets/cell_fracture/`，结构为：

```json
{
  "format_version": 1,
  "tool_id": "cell_fracture",
  "values": {"source_limit": 100}
}
```

保存时通常写出全部 29 个参数。读取时允许缺字段并保留当前界面值，但拒绝未知字段、错误工具 ID 和不支持的格式版本。旧的无信封 JSON 仅从原目录 `quick_destruction/presets/` 读取；同名文件存在于两个目录时，新目录优先。新预设只写到工具目录。
