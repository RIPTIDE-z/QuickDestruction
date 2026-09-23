# 架构与接口

## 模块边界

| 位置 | 职责 |
| --- | --- |
| `source/__init__.py`、`registration.py` | Blender 扩展入口、类与 RNA ID 校验、顺序注册和逆序清理。 |
| `source/tool_contract.py`、`tool_registry.py` | 工具声明结构与有序工具清单。 |
| `source/properties.py`、`panel.py`、`constants.py` | 场景参数聚合根、侧栏父面板和扩展级界面标识。 |
| `source/preset_ui.py` | 通用预设界面：会话、目录解析、算子与菜单，对全部工具共用。 |
| `source/i18n.py`、`locales/*.json` | 根据工具清单构建翻译目录，并提供一个与工具无关的框架分组；每种语言一个 JSON。 |
| `source/common/` | 参数校验、RNA 属性生成、包围盒测量、译文取词、预设存储与信封、翻译目录构建及 Blender 数据与上下文辅助。 |
| `source/<Tool>/` | 各业务工具包，内部结构见 [MeshFracture](#meshfracture)。 |

依赖方向为根装配 → 工具声明 → 交互层 → 阶段 API → 计算层。计算层不导入交互层；工具可使用 `common`，`common` 不导入工具。`preset_ui.py` 只按传入的 `PresetSpec` 工作，不导入 `tool_registry`，因此任何工具都可以直接导入它。`CreatedData` 只回收本次创建的 Blender 对象、网格和集合。

交互层可读取根 `constants` 与通用的 `common/translate.py`（界面标识与译文取词）；根层只在装配时通过 `tool_registry` 导入工具。工具包内的模块按普通顶层导入书写，不必依赖导入顺序。

## 工具装配

`tool_registry.TOOLS` 是工具顺序来源，`ordered_tools()` 按 `weight` 给出界面顺序，注册、父面板与翻译目录共用它。每个 `ToolSpec` 声明：

| 字段 | 含义 |
| --- | --- |
| `tool_id`、`translation_module`、`translation_context` | 工具身份、语言 JSON 中的分组名与该工具消息的翻译上下文。 |
| `property_specs` | 参数规格，供翻译目录与 RNA 生成共用。 |
| `classes()` | 返回本工具要注册的算子与面板类，元组顺序即注册顺序。 |
| `settings_attr`、`settings_class` | 可选的场景属性组字段名与类型，两者必须同时提供或同时省略。 |
| `preset` | 可选的 `PresetSpec`，声明后获得通用预设界面。 |
| `menu_hooks`、`load_pre`、`load_post`、`clear_sessions` | 菜单入口与文件加载前后的回调。 |
| `panel_row` | 可选，在根侧栏父面板里画一行本工具的入口。 |
| `weight` | 界面顺序权重，数值小的排在前面。 |

注册层先检查类名与 `bl_idname` 唯一性、工具身份与预设归属的唯一性，再注册参数组、`Scene.qd_settings`、父面板、通用预设类、工具类和外部入口。停用或注册失败时按相反顺序清理。

新增一个工具只需要在自己的包内声明 `ToolSpec` 并加入 `TOOLS`：需要持久化参数时同时给出属性组类与 `QDSettings` 字段名，需要预设时给出 `PresetSpec`，需要侧栏界面时把子面板挂到 `QD_PT_main` 下，并在每种语言的 JSON 里补上该模块的分组（见[翻译维护](Translations.md)）。根层负责注册顺序、父面板、场景指针、通用预设界面和翻译目录，工具之间不互相导入。

## 通用预设

预设界面与文件格式由根层提供，与工具数量无关：算子与菜单只注册一份（`qd.preset_save`、`qd.preset_load`、`qd.preset_remove`、`qd.preset_open_directory` 与 `QD_MT_presets`）。工具在自己的 `ToolSpec.preset` 上声明 `PresetSpec` 即可获得这套界面：

| 字段 | 含义 |
| --- | --- |
| `key` | 工具标识，用于预设归属校验，同时是侧栏会话的令牌。 |
| `subdir` | 相对 Blender 用户脚本目录的预设目录，每个工具独占一层。 |
| `specs` | 参数规格；预设字段的范围校验、写回与翻译字段路径都按它进行。 |

目录与文件由 `common/preset_storage.py` 处理：文件名净化、大小限制、JSON 读写与原子保存；`common/preset_format.py` 负责信封；`preset_ui.py` 负责会话、目录解析、算子与菜单。预设位于 Blender 用户脚本目录的 `quick_destruction/presets/<工具标识>/`：

```json
{
  "format_version": 1,
  "tool_id": "mesh_fracture",
  "values": {"source_limit": 100}
}
```

保存时写出该工具的全部字段。读取时允许缺字段并保留当前界面值，但拒绝未知字段、其他工具的预设和不支持的格式版本。

`preset_name` 由通用预设层挂到工具的场景参数组上，只记录界面显示的名称与保存对话框的默认名，不属于预设参数。

会话令牌把「侧栏」与「参数弹窗」两个编辑目标分开。侧栏令牌就是工具标识，每次面板重绘刷新同一条记录；弹窗令牌是一次性 UUID，写在算子的隐藏字段上，对话框关闭或执行后即失效。换文件时全部会话一并作废，侧栏会话由下一次重绘重建。

令牌经布局上下文交给菜单（`layout.context_string_set` / `getattr(context, ...)`），不落在场景参数组上：面板绘制回调只允许读 ID 数据，往参数组写临时字段会抛 `Writing to ID classes in this context is not allowed`，并被 Blender 静默跳过该面板剩余内容。

## MeshFracture

预切割工具：点源采样 → 有界胞体 → 凸包 → Exact 布尔求交 → 递归与断面收尾。它是当前唯一的工具，也是新增工具时的参照实现。

### 包结构

| 位置 | 职责 |
| --- | --- |
| `tool.py` | `ToolSpec` 声明、场景属性组、菜单入口、会话清理与文件加载回调。 |
| `settings.py` | 27 项参数规格、场景参数组访问器、预设归属，以及校验与序列化入口。 |
| `interaction/` | 子面板与参数布局、生成算子；预设界面来自根层。 |
| `api.py` | 可直接调用的生成入口与结果对象。 |
| `core/` | 纯计算：种子采样、胞体构造、布尔策略、网格操作、命名与递归编排。 |
| `constants.py`、`errors.py` | 领域常量与领域异常。 |

`core/mesh_ops.py` 的两个阶段 `build_cells` 与 `intersect_cells` 都接受可选的 `snapshot`：流程编排对每个源只取一次求值快照传给两者，同一次切割的世界空间顶点、包围盒与尺度因此只求一次；两个阶段被独立调用时各自求值。

### 场景参数与界面标识

场景参数位于 `Scene.qd_settings.mesh_fracture`，其中 `preset_name` 只记录本工具当前预设名。侧栏子面板 ID 为 `MESH_FRACTURE_PT_settings`，生成算子 ID 为 `mesh_fracture.generate_fragments`。工具菜单与子面板都显示 **MeshFracture／网格破碎**；生成动作显示 **Generate Fragments／生成碎块**。

### 阶段 API

从已安装扩展的 `MeshFracture.api` 导入：

```python
generate_fragments(context, sources, config, output_collection, *, rng=None)
```

| 参数 | 契约 |
| --- | --- |
| `context` | Blender 物体模式上下文，包含场景和视图层。 |
| `sources` | 显式源物体序列；非网格忽略。 |
| `config` | 用户参数映射；缺字段从 `PROPERTY_SPECS` 补齐。 |
| `output_collection` | 输出集合对象或名称；`None` / 空名使用当前集合。 |
| `rng` | 可选局部随机流，提供时按调用顺序消费。 |

API 由显式的 `output_collection` 决定输出位置。它在完成或失败后恢复原选择和活动物体；交互算子在成功后选中生成的碎块。返回的 `MeshFractureResult` 包含碎块元组 `shards`、按源物体索引的只读映射 `source_to_shards`、耗时 `seconds` 和诊断元组 `diagnostics`。只读的是结果容器，Blender 对象本身仍可编辑。
