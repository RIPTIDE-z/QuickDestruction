# 架构与接口

## 模块边界

| 位置 | 职责 |
| --- | --- |
| `source/__init__.py` | 扩展入口，只暴露 `register` 与 `unregister`，首次调用时才导入装配模块。 |
| `source/registration.py` | 工具清单 `TOOLS`、按规格生成参数组、注册前的全部校验、翻译合并，以及事务式注册与逆序回滚。 |
| `source/framework/` | 工具可以依赖的框架层：工具契约 `ToolSpec`、通用预设（文件、信封与界面）、面板与算子的混入类、参数分组绘制。 |
| `source/common/` | 与本插件无关的公共机制：参数规格引擎 `Schema`、领域异常、译文取词、语言文件加载、创建账本、Blender 上下文辅助、碎块契约。 |
| `source/<Tool>/` | 各业务工具包，内部结构见 [MeshFracture](#meshfracture)。 |

依赖规则只有一句：**工具只依赖 `framework` 与 `common`，`framework` 只依赖 `common`，`common` 只依赖标准库与 Blender API**。工具之间不互相导入，根层只有入口与装配。

## 工具契约

新增工具需要创建一个包，包内的 `tool.py` 声明 `TOOL`，然后把它加进 `registration.TOOLS`。`TOOLS` 的元组顺序就是注册顺序，也是侧栏里面板的上下顺序。

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
| `tool_id` | 唯一身份，小写字母、数字与下划线。场景字段 `Scene.qd_settings.<tool_id>`、预设目录 `presets/<tool_id>` 与翻译上下文 `QuickDestruction.<tool_id>` 都由它派生。 |
| `locales` | 工具自带的语言文件目录。 |
| `classes` | 要注册的算子、面板与菜单类，是一个元组。 |
| `schema` | 参数规格 `Schema(PROPERTY_SPECS, CONTEXT)`。为空表示没有场景参数，也没有预设。 |
| `presets` | 为真时，参数组附带预设名字段，并获得通用预设界面。 |
| `handlers` | Blender 应用挂钩，是 `(bpy.app.handlers 中的列表名, 回调)` 的序列。 |

工具包的约定：

- `__init__.py` 只写文档字符串，不导入 Blender。
- 带参数的工具在 `settings.py` 里给出 `SCHEMA`，这样单元测试不启动 Blender 也能导入参数规格。
- 类名以 `QD_` 开头，算子 ID 以 `qd_<tool_id>.` 开头。注册层会检查前缀与重名，因为 Blender 遇到重复的 `bl_idname` 会静默顶替。
- 面板继承 `framework.ui.ToolPanel`，它固定了 3D 视图侧栏与插件标签页。需要默认收起的面板，在自己的类上写 `bl_options = {"DEFAULT_CLOSED"}`。
- 算子继承 `framework.ui.ToolOperator` 并实现 `run(context)`，返回的文本会作为 INFO 报告。异常统一翻译成"<操作>失败：<原因>"：领域错误按它自己的翻译上下文翻译，参数错误会换成界面参数名。
- 面板与菜单类都要显式写英文 `bl_description`。否则 Blender 会把中文文档字符串当作悬停提示显示。

注册层按参数规格自动生成参数组类 `QD_PG_<tool_id>`，挂到场景参数根上，不需要工具手写。

注册按顺序执行以下步骤，任一步失败都会按相反顺序撤销已完成的步骤：

1. 翻译；
2. 参数组、预设类与工具类；
3. `Scene.qd_settings`；
4. 预设登记；
5. 应用挂钩。

## 通用预设

预设界面由 `framework/presets.py` 提供，算子与菜单只注册一份，与工具数量无关：`qd.preset_save`、`qd.preset_load`、`qd.preset_remove`、`qd.preset_open_directory` 与 `QD_MT_presets`。

- **定位目标**：每个预设算子都带一个隐藏的 `tool_id` 参数，执行时从 `context.scene.qd_settings` 取当前场景的参数组，因此脚本也能直接调用。
  - 面板经 `layout.context_string_set` 把 `tool_id` 交给同一布局上的菜单，绘制期不写任何 ID 数据。
  - 如果在绘制回调里写参数组，会抛 `Writing to ID classes in this context is not allowed`。
- **存放位置**：预设存在扩展用户目录 `bpy.utils.extension_path_user(扩展包, "presets/<tool_id>")` 下，升级扩展时不会被清掉。
  - 旧版本存放在用户脚本目录 `quick_destruction/presets/<tool_id>`；首次启用时，如果新目录还不存在，会把旧文件复制过来，旧目录保留不删。
- **文件处理**：由 `framework/preset_files.py` 负责。
  - 保存：名称会被净化；先写同目录的临时文件，再整体替换。
  - 读取：文件上限 1 MiB，只当数据解析。

文件格式如下：

```json
{
  "format_version": 1,
  "tool_id": "mesh_fracture",
  "values": {"source_limit": 100}
}
```

保存时写出该工具的全部字段。读取时先核对信封，再由 `Schema.write` 整体校验并写回：

- 缺失的字段保留当前界面值；
- 未知字段、其他工具的预设和不支持的格式版本都会被拒绝；
- 任一字段写入失败时，恢复全部原值。

## 工具间契约

生成碎块的工具通过 `common/shards.py` 在碎块上写入三个隐藏的自定义属性：`_qd_source`（源物体名）、`_qd_level`（生成轮次）、`_qd_group`（轮次内的组号）。其他工具用 `shards_of(objects, source_name)` 识别碎块，不必导入生成它的工具。

## MeshFracture

预切割工具：点源采样 → 有界胞体 → 凸包 → Exact 布尔求交 → 递归与断面收尾。它是目前唯一带完整计算链路的工具，也是新增工具时的参照实现。

### 包结构

| 位置 | 职责 |
| --- | --- |
| `tool.py` | `ToolSpec` 声明。 |
| `settings.py` | 参数规格 `PROPERTY_SPECS` 与 `SCHEMA`。 |
| `interaction.py` | 侧栏面板、参数分组表与生成算子。 |
| `api.py` | 可直接调用的生成入口与结果对象。 |
| `core/` | 纯计算：种子采样、胞体构造、布尔策略、网格操作、命名与递归编排。 |
| `constants.py`、`errors.py` | 领域常量与领域异常。 |
| `locales/` | 本工具的语言文件。 |

求交结果在 `intersect_cells` 里只做两件事：焊接重合顶点，以及把平面偏离超过焊接距离的面三角化；**不合并面**。面合并由流程编排在全部切割结束之后，对最终碎块调用一次（`mesh_ops.dissolve_final_shards`）。原因如下：

- 递归层的布尔操作数，正是上一轮产生的碎块；
- 精确布尔要求操作数只含平面面，不共面的面没有唯一的面平面，会让结果留下未裁剪的残片；
- 面合并会把近共面的多个面并成一个并不共面的 n-gon，所以它不能作用在中间碎块上。

三角化只改变面数，不改变渲染表面。

每次切割只对源物体求值一次，得到快照 `BooleanSource`。胞体构造、噪波、焊接距离与布尔越界校验共用这份快照里的包围盒与尺度。

### 场景参数与界面标识

- 场景参数位于 `Scene.qd_settings.mesh_fracture`，其中 `preset_name` 只记录本工具当前的预设名。
- 侧栏面板 ID 为 `QD_MESH_FRACTURE_PT_main`，显示为 **MeshFracture／网格破碎**。
- 生成算子 ID 为 `qd_mesh_fracture.generate`，显示为 **Generate Fragments／生成碎块**。
  - 它不声明参数，执行时直接读取场景参数组，因此侧栏上显示的值就是这次切割使用的值；
  - `bl_options` 是 `{'UNDO', 'INTERNAL'}`：几何改动进入撤销栈，可以用 **Ctrl+Z** 回退；它不进入算子搜索，也没有"调整上一步操作"面板。

### 阶段 API

从已安装扩展的 `MeshFracture.api` 导入：

```python
generate_fragments(context, sources, config, output_collection, *, rng=None)
```

| 参数 | 契约 |
| --- | --- |
| `context` | Blender 物体模式上下文，包含场景和视图层。 |
| `sources` | 显式源物体序列；非网格物体会被忽略。 |
| `config` | 用户参数映射。缺失的字段取规格默认值；未知字段抛 `InvalidParameterError`。 |
| `output_collection` | 输出集合对象或名称；`None` 或空名表示使用当前集合。 |
| `rng` | 可选的局部随机流；为空时按 `random_seed` 新建。 |

API 在完成或失败后，都会恢复原来的选择和活动物体。

返回的 `MeshFractureResult` 包含：碎块元组 `shards`、按源物体索引的只读映射 `source_to_shards`、耗时 `seconds`，以及诊断元组 `diagnostics`。

## PhysicalSettings

破坏动画的物理设置，**占位工具**：`tool.py` 里只有一块默认收起的侧栏面板，面板上一行用途提示，还没有参数、算子与预设。实现时参照 MeshFracture 的结构补齐。
