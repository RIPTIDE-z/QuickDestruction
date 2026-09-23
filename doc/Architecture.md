# 架构与接口

## 模块边界

| 位置 | 职责 |
| --- | --- |
| `source/__init__.py`、`registration.py` | Blender 扩展入口、类与 RNA ID 校验、顺序注册和逆序清理。 |
| `source/tool_contract.py`、`tool_registry.py` | 工具声明结构与有序工具清单。 |
| `source/properties.py`、`panel.py`、`constants.py` | 场景参数聚合根、侧栏父面板和扩展级界面标识。 |
| `source/i18n.py`、`locales/*.json` | 根据工具清单构建翻译目录；每种语言一个 JSON。 |
| `source/common/` | 参数校验、RNA 属性生成、JSON 存储、翻译目录构建及 Blender 数据与上下文辅助。 |
| `source/<Tool>/` | 各业务工具包，内部结构见 [MeshFracture](#meshfracture)。 |

依赖方向为根装配 → 工具声明 → 交互层 → 阶段 API → 计算层。计算层不导入交互层；工具可使用 `common`，`common` 不导入工具。`CreatedData` 只回收本次创建的 Blender 对象、网格和集合。

交互层可读取根 `i18n` 与 `constants`（共享的翻译适配与界面标识）；根层只在装配时通过 `tool_registry` 导入工具；工具包内的交互模块由 `tool.classes()` 延迟导入，避免根翻译目录建立前形成导入环。

## 工具装配

`tool_registry.TOOLS` 是工具顺序来源。每个 `ToolSpec` 声明工具 ID、翻译模块与上下文、参数规格、可选的场景属性组、交互类和菜单 / 文件加载回调。注册层先检查类名与 `bl_idname` 唯一性，再注册参数组、`Scene.qd_settings`、父面板、工具类和外部入口。停用或注册失败时按相反顺序清理。

新增一个工具只需要在自己的包内声明 `ToolSpec` 并加入 `TOOLS`：需要持久化参数时同时给出属性组类与 `QDSettings` 字段名，需要侧栏界面时把子面板挂到 `QD_PT_main` 下，并在每种语言的 JSON 里补上该模块的分组（见[翻译维护](Translations.md)）。根层负责注册顺序、父面板、场景指针和翻译目录，工具之间不互相导入。

## MeshFracture

预切割工具：点源采样 → 有界胞体 → 凸包 → Exact 布尔求交 → 递归与断面收尾。它是当前唯一的工具，也是新增工具时的参照实现。

### 包结构

| 位置 | 职责 |
| --- | --- |
| `tool.py` | `ToolSpec` 声明、场景属性组、菜单入口、会话清理与文件加载回调。 |
| `settings.py` | 27 项参数规格，以及校验、序列化与错误标签转换。 |
| `interaction/` | 子面板与参数布局、生成算子、预设会话与预设格式。 |
| `api.py` | 可直接调用的生成入口与结果对象。 |
| `core/` | 纯计算：种子采样、胞体构造、布尔策略、网格操作、命名与递归编排。 |
| `constants.py`、`errors.py` | 领域常量与领域异常。 |

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

### 预设

存储由 `common/preset_storage.py` 提供：文件名净化、大小限制、JSON 读写和原子保存；MeshFracture 负责验证格式、工具身份与参数。预设位于 Blender 用户脚本目录的 `quick_destruction/presets/mesh_fracture/`：

```json
{
  "format_version": 1,
  "tool_id": "mesh_fracture",
  "values": {"source_limit": 100}
}
```

保存当前界面时写出全部 27 项参数。读取时允许缺字段并保留当前界面值，但拒绝未知字段、其他工具的预设和不支持的格式版本。`preset_name` 不属于预设参数，只记录界面显示的名称与保存对话框的默认名。
