# 架构与接口

## 模块边界

| 位置 | 职责 |
| --- | --- |
| `source/__init__.py`、`registration.py` | Blender 扩展入口、类与 RNA ID 校验、顺序注册和逆序清理。 |
| `source/tool_contract.py`、`tool_registry.py` | 工具声明结构与有序工具清单。 |
| `source/properties.py`、`constants.py` | 场景参数聚合根与扩展级界面标识。 |
| `source/preset_ui.py` | 通用预设界面：会话、目录解析、算子与菜单，对全部工具共用。 |
| `source/i18n.py`、`locales/*.json` | 根据工具清单构建翻译目录，并提供一个与工具无关的框架分组；每种语言一个 JSON。 |
| `source/common/` | 参数校验、RNA 属性生成、包围盒测量、译文取词、预设存储与信封、翻译目录构建及 Blender 数据与上下文辅助。 |
| `source/<Tool>/` | 各业务工具包，内部结构见 [MeshFracture](#meshfracture)。 |

依赖方向为根装配 → 工具声明 → 交互层 → 阶段 API → 计算层。计算层不导入交互层；工具可使用 `common`，`common` 不导入工具。`preset_ui.py` 只按传入的 `PresetSpec` 工作，不导入 `tool_registry`，因此任何工具都可以直接导入它。`CreatedData` 只回收本次创建的 Blender 对象、网格和集合。

交互层可读取根 `constants` 与通用的 `common/translate.py`（界面标识与译文取词）；根层只在装配时通过 `tool_registry` 导入工具。工具包内的模块按普通顶层导入书写，不必依赖导入顺序。

## 工具装配

`tool_registry.TOOLS` 是工具顺序来源，`ordered_tools()` 按 `weight` 给出界面顺序，注册与翻译目录共用它。当前清单是 `MeshFracture`（切割）与 `PhysicalSettings`（占位）。每个 `ToolSpec` 声明：

| 字段 | 含义 |
| --- | --- |
| `tool_id`、`translation_module`、`translation_context` | 工具身份、语言 JSON 中的分组名与该工具消息的翻译上下文。 |
| `property_specs` | 参数规格，供翻译目录与 RNA 生成共用。 |
| `classes()` | 返回本工具要注册的算子与面板类，元组顺序即注册顺序。 |
| `settings_attr`、`settings_class` | 可选的场景属性组字段名与类型，两者必须同时提供或同时省略。 |
| `preset` | 可选的 `PresetSpec`，声明后获得通用预设界面。 |
| `load_pre`、`load_post`、`clear_sessions` | 文件加载前后的回调与卸载时的会话清理。 |
| `weight` | 界面顺序权重，数值小的排在前面。 |
| `default_closed` | 次要工具的标记：为 `True` 时它的侧栏面板默认收起。注册层据此设置面板类的 `bl_options`，因此以本字段为唯一来源。 |

注册层先检查类名与 `bl_idname` 唯一性、工具身份与预设归属的唯一性，再把 `default_closed` 落到面板类上，然后注册参数组、`Scene.qd_settings`、通用预设类、工具类和文件加载挂钩。停用或注册失败时按相反顺序清理。

新增一个工具只需要在自己的包内声明 `ToolSpec` 并加入 `TOOLS`：需要持久化参数时同时给出属性组类与 `QDSettings` 字段名，需要预设时给出 `PresetSpec`，需要侧栏界面时在工具包里声明一个顶层 `Panel`，次要工具再把 `default_closed` 设为 `True`，并在每种语言的 JSON 里补上该模块的分组（见[翻译维护](Translations.md)）。根层负责注册顺序、场景指针、通用预设界面和翻译目录，工具之间不互相导入。

## 侧栏界面

同一分类下并列多个**顶层面板**，每个工具一个，各自独立折叠，不设父面板：

| 约定 | 说明 |
| --- | --- |
| `bl_space_type` / `bl_region_type` | 固定为 `VIEW_3D` / `UI`，也就是 3D 视图的侧栏。 |
| `bl_category` | 取 `constants.SIDEBAR_CATEGORY`，决定它属于侧栏的哪个标签页。 |
| `bl_parent_id` | 留空。面板之间是并列关系，不嵌套；需要子分节时才可以指向本工具的面板。 |
| 排列顺序 | 同分类的顶层面板按 `bl_order` 升序排列，值相同时按注册顺序；注册顺序由 `ordered_tools()` 决定，因此 `weight` 就是界面顺序。不设 `bl_order` 时全部视为 0，顺序完全由工具声明决定。 |
| 初始折叠 | 由 `ToolSpec.default_closed` 决定，注册层把它写成面板类的 `bl_options`，工具不需要自己写这个选项。收起的面板不会被 Blender 调用 `draw`，所以界面验证里"内容画得出来"由驱动面板负责，侧栏那一轮只核对注册、摆放与折叠状态。 |

分类标签页的标题来自 `bl_category` 本身，与面板数量无关；去掉父面板后标签页里直接就是各工具的面板。

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

会话令牌只有一种：令牌就是工具标识，面板每次重绘刷新同一条记录，因此它只随工具数量增长。令牌经布局上下文交给同一布局上的菜单，菜单与预设算子据此找回「哪个工具的哪份参数」。换文件时全部会话一并作废，下一次重绘重建。

令牌经布局上下文交给菜单（`layout.context_string_set` / `getattr(context, ...)`），不落在场景参数组上：面板绘制回调只允许读 ID 数据，往参数组写临时字段会抛 `Writing to ID classes in this context is not allowed`，并被 Blender 静默跳过该面板剩余内容。

## MeshFracture

预切割工具：点源采样 → 有界胞体 → 凸包 → Exact 布尔求交 → 递归与断面收尾。它是目前唯一带完整计算链路的工具，也是新增工具时的参照实现。

### 包结构

| 位置 | 职责 |
| --- | --- |
| `tool.py` | `ToolSpec` 声明、场景属性组、会话清理与文件加载回调。 |
| `settings.py` | 27 项参数规格、场景参数组访问器、预设归属，以及校验与序列化入口。 |
| `interaction/` | 侧栏顶层面板与参数布局、生成算子；预设界面来自根层。 |
| `api.py` | 可直接调用的生成入口与结果对象。 |
| `core/` | 纯计算：种子采样、胞体构造、布尔策略、网格操作、命名与递归编排。 |
| `constants.py`、`errors.py` | 领域常量与领域异常。 |

`core/mesh_ops.py` 的两个阶段 `build_cells` 与 `intersect_cells` 都接受可选的 `snapshot`：流程编排对每个源只取一次求值快照传给两者，同一次切割的世界空间顶点、包围盒与尺度因此只求一次；两个阶段被独立调用时各自求值。

### 场景参数与界面标识

场景参数位于 `Scene.qd_settings.mesh_fracture`，其中 `preset_name` 只记录本工具当前预设名。侧栏面板 ID 为 `MESH_FRACTURE_PT_settings`，生成算子 ID 为 `mesh_fracture.generate_fragments`，它只由面板按钮执行，不出现在任何菜单或算子搜索里。侧栏面板显示 **MeshFracture／网格破碎**；生成动作显示 **Generate Fragments／生成碎块**。

生成算子的 `bl_options` 是 `{'UNDO', 'INTERNAL'}`：几何改动进入撤销栈，用 **Ctrl+Z** 回退；`INTERNAL` 让它不进算子搜索，同时它也不声明 `REGISTER`，因此没有「调整上一步操作」面板。工具只保留侧栏这一个入口。

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

## PhysicalSettings

破坏动画的物理设置，**占位工具**：只有一块侧栏面板，里面一行用途提示，尚无参数、算子、预设与计算链路。

| 项 | 现状 |
| --- | --- |
| `tool_id` | `physical_settings` |
| 场景参数组 | 无。`settings_attr` 与 `settings_class` 都省略，因此它不参与场景持久化 |
| 参数规格 | 空字典，面板上没有可绑定的参数 |
| 算子 | 无。面板只显示说明，没有算子入口 |
| 预设 | 无 `PresetSpec`，因此没有预设目录与预设界面 |
| 面板 | `PHYSICAL_SETTINGS_PT_settings`，`default_closed = True`，默认收起 |

实现这块面板时，按 MeshFracture 的结构补齐：参数写进 `settings.py` 的规格并给出场景参数组，逻辑按需要放进 `core/` 与 `api.py`，算子放 `interaction/`，要预设时再加 `PRESET`；同时给每种语言的 JSON 补上属性译文。
