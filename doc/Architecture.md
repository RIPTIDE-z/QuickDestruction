# 架构与接口

## 分层

```
source/
  __init__.py        扩展入口, 调用时才导入装配模块
  registration.py    工具清单 TOOLS, 生成参数组, 注册前校验, 合并翻译, 事务式注册与回滚
  framework/         工具可以依赖的框架层: 工具契约, 通用预设, 面板与算子基类, 参数绘制
  common/            与本插件无关的公共机制: 参数规格, 领域异常, 翻译, 创建账本, 碎块契约
  tools/<Tool>/      业务工具, 一个工具一个包
```

**依赖规则**：

- 工具只依赖 `framework` 与 `common`；
- `framework` 只依赖 `common`；
- `common` 只依赖标准库与 Blender API；
- 工具之间不互相导入。

## 工具契约

新增一个工具的步骤：

1. 在 `tools/` 下建一个包；
2. 在包内的 `tool.py` 声明 `TOOL`；
3. 把它加进 `registration.TOOLS`。

删除工具时，删掉这个包，再从 `TOOLS` 中去掉一行。`TOOLS` 的顺序就是注册顺序，也是侧栏面板的上下顺序。

```python
TOOL = ToolSpec(
    tool_id="mesh_fracture",
    locales=Path(__file__).with_name("locales"),
    classes=(QD_MESH_FRACTURE_OT_generate, QD_MESH_FRACTURE_PT_main),
    schema=SCHEMA,
    presets=True,
)
```

| 字段 | 含义 |
| --- | --- |
| `tool_id` | 唯一身份，只能用小写字母、数字与下划线。场景参数 `Scene.qd_settings.<tool_id>`、预设目录 `presets/<tool_id>` 与翻译上下文 `QuickDestruction.<tool_id>` 都由它派生 |
| `locales` | 工具自带的语言文件目录 |
| `classes` | 要注册的算子、面板与菜单类 |
| `schema` | 参数规格 `Schema(PROPERTY_SPECS, CONTEXT)`；为空表示没有参数，也没有预设 |
| `presets` | 为真时获得通用预设界面 |

| 约定 | 说明 |
| --- | --- |
| 包结构 | `__init__.py` 只写文档字符串；带参数的工具在 `settings.py` 里给出 `SCHEMA`。这样单元测试不启动 Blender 也能导入参数规格 |
| 命名 | 类名以 `QD_<TOOL_ID>_` 开头，算子 ID 以 `qd_<tool_id>.` 开头；框架自己的类用 `QD_` 与 `qd.`。注册时检查前缀与重名，因为 Blender 遇到重复的 `bl_idname` 会静默顶替 |
| 面板 | 继承 `framework.ui.ToolPanel`，它固定了侧栏位置与标签页；默认收起的面板自己写 `bl_options = {"DEFAULT_CLOSED"}`；界面排版见下一节 |
| 算子 | 继承 `framework.operators.ToolOperator`，只实现 `run(context)`，它返回的文本作为 INFO 报告 |
| 说明 | 面板、菜单与算子都要写英文 `bl_description`。否则 Blender 会把中文文档字符串当作悬停提示 |

参数组类 `QD_PG_<tool_id>` 由注册层按参数规格自动生成。注册按"翻译 → 类 → `Scene.qd_settings` → 预设登记"的顺序执行，任一步失败都会逆序撤销已经完成的步骤。

## 界面排版

侧栏很窄，工具以后还会增加，所以面板按"常用项直接可见，其余逐层展开"来排：

- **主面板**：最上面是主操作按钮，下面是少数常用参数。主面板类设置 `tool_id`，带预设的工具由 `ToolPanel.draw_header_preset` 自动在标题栏显示预设入口，不占内容高度。
- **子面板**：其余参数用 `ParamGroup(标题, 字段, closed=是否默认收起, toggle=标题上的总开关)` 声明，由 `parameter_panels(主面板类, SCHEMA, 常用字段, 分组表, active)` 生成挂在主面板下的子面板类。
  - Blender 会记住每个子面板的折叠状态；
  - 不常调的分组默认收起；
  - 有总开关的分组把开关画在子面板标题上，关闭时整组置灰。
- **字段**：`draw_fields` 一行画一个字段，标签在左、数值在右（`use_property_split`），在任何宽度和语言下标签都能完整显示。多选枚举的标签单独占一行，选项分两列排开。
- **置灰**：`active(owner, key)` 为假的字段只置灰、不锁定，与 Blender 的惯例一致，仍然可以预先设置。
- **检查**：`parameter_panels` 会检查每个参数恰好排布一次（常用字段、分组字段与标题开关合计），漏排或重复在导入时就会报错。
- **标签**：写得短，不重复面板标题已经给出的上下文。例如"递归破碎"子面板里的字段叫 `Fraction`，不叫 `Recursion Fraction`。

## 错误与报告

`ToolOperator` 把异常统一报告为"<操作>失败：<原因>"：

- **可预期的错误**：都是 `common.errors.DomainError`。消息按错误类声明的 `context` 翻译，未声明时按框架上下文翻译。`terms` 里列出的插值参数（例如参数名）会先按各自的上下文翻译，再填入消息。
- **参数错误**：参数校验失败抛出 `InvalidParameterError`，参数名按所属工具的上下文翻译。
- **意外异常**：原样展示，并在控制台打印堆栈。

## 通用预设

`framework/presets.py` 提供一套与工具数量无关的算子与菜单：`qd.preset_save`、`qd.preset_load`、`qd.preset_remove`、`qd.preset_open_directory`，以及 `QD_MT_presets`。

- **定位目标**：
  - 每个预设算子带一个隐藏的 `tool_id` 参数，执行时取当前场景里这个工具的参数组，因此脚本也能直接调用；
  - 面板经 `layout.context_string_set` 把 `tool_id` 交给菜单，绘制期间不写任何 ID 数据。在绘制回调里写 ID 数据会被 Blender 拒绝。
- **撤销**：载入预设会进入撤销栈；保存、删除与打开目录只是文件操作，不进入撤销栈。
- **存放**：每个工具的预设存在扩展用户目录的 `presets/<tool_id>/` 下，升级扩展时不会被清掉。
- **文件格式**：由 `framework/preset_files.py` 负责，名称会被净化。写入时先写临时文件再整体替换；读取上限为 1 MiB，只当数据解析。

```json
{"format_version": 1, "tool_id": "mesh_fracture", "values": {"source_limit": 100}}
```

- **保存**：写出全部字段。
- **载入**：先核对信封，再由 `Schema.write` 整体校验并写回。
  - 缺失的字段保留界面当前值；
  - 未知字段、其他工具的预设、不支持的格式版本都会被拒绝；
  - 任一字段写入失败时，恢复全部原值。

## 工具间契约

生成碎块的工具通过 `common/shards.py` 写入三个隐藏属性：`_qd_source`（源物体名）、`_qd_level`（生成轮次）、`_qd_group`（轮次内的组号）。已被细分但保留下来的上级碎块另外带有 `_qd_parent`，它与子块几何重叠，不算作碎块。其他工具用 `shards_of(objects, source_name)` 识别碎块（不含上级碎块），不必导入生成它的工具。

## MeshFracture

参照实现，也是目前唯一带完整计算链路的工具。用户说明见 [MeshFracture](MeshFracture.md)。

| 位置 | 职责 |
| --- | --- |
| `tool.py` | `ToolSpec` 声明 |
| `settings.py` | 参数规格 `PROPERTY_SPECS` 与 `SCHEMA` |
| `interaction.py` | 主面板 `QD_MESH_FRACTURE_PT_main`、常用字段与分组表（由此生成参数子面板）、生成算子 `qd_mesh_fracture.generate` |
| `api.py` | 阶段 API 与结果对象 |
| `core/pipeline.py` | 编排：前置检查、首次切割、递归、收尾、碎块硬上限、失败回滚 |
| `core/` 其余模块 | 种子采样、胞体构造、布尔策略、网格操作、命名 |

**关键设计**：

- **生成算子不声明参数**：执行时直接读取场景参数组。它是 `UNDO` 加 `INTERNAL`，只由面板按钮调用。
- **每次切割只求值一次源物体**：得到快照 `BooleanSource`。胞体构造、噪波、焊接距离与越界校验共用快照里的包围盒与尺度。
- **中间结果只焊接顶点与三角化非平面面，不合并面**：原因有三：
  - 递归时，上一轮的碎块就是下一轮的布尔操作数；
  - 精确布尔要求操作数的每个面都是平面；
  - 面合并会产生不共面的 n-gon，让下一轮布尔留下未裁剪的残片。

  所以面合并只在全部切割结束后，对最终碎块做一次（`mesh_ops.dissolve_final_shards`）。

### 阶段 API

```python
from bl_ext.user_default.quick_destruction.tools.MeshFracture.api import generate_fragments

result = generate_fragments(context, sources, config, *, rng=None)
```

| 参数 | 契约 |
| --- | --- |
| `context` | 物体模式的上下文 |
| `sources` | 源物体序列，非网格物体会被忽略 |
| `config` | 参数映射。缺失字段取默认值，未知字段抛 `InvalidParameterError`。输出集合由 `collection_name` 决定：空名表示当前集合，否则按名查找，找不到时新建 |
| `rng` | 可选的局部随机流，为空时按 `random_seed` 新建 |

- **返回值**：`MeshFractureResult`，包含碎块元组 `shards`、按源物体索引的只读映射 `source_to_shards`，以及耗时 `seconds`。
- **选择状态**：完成或失败后，都会恢复调用前的选择与活动物体。
