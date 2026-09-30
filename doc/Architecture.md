# 架构与接口

## 分层

```text
source/
  __init__.py        扩展入口, 延迟导入装配模块
  registration.py    工具清单, 参数组生成, 注册校验, 翻译合并与注册清理
  framework/         工具契约, 通用预设, 面板与算子基类, 参数绘制
  common/            多个工具共用的机制与数据契约
  tools/<Tool>/      业务工具, 一个工具一个包
```

项目内部依赖方向为 `tools → framework → common`，工具也可直接使用 `common`。工具之间不互相导入；公共层不依赖框架或工具。各层按需使用标准库与 Blender API，纯参数和文件处理模块可脱离 Blender 测试。

## 工具接入

1. 在 `tools/` 下建立包，`__init__.py` 只写文档字符串。
2. 在 `tool.py` 声明 `TOOL`；有参数的工具在 `settings.py` 定义 `SCHEMA`。
3. 在 `registration.py` 导入声明并加入 `TOOLS`。移除工具时同时删除导入、清单项与工具包。

以下是 MeshFracture 的声明结构，类与参数规格由工具模块提供：

```python
TOOL = ToolSpec(
    tool_id="mesh_fracture",
    locales=Path(__file__).with_name("locales"),
    classes=(QD_MESH_FRACTURE_OT_generate, QD_MESH_FRACTURE_PT_main, *SUBPANELS),
    schema=SCHEMA,
    presets=True,
)
```

| 字段 | 含义 |
| --- | --- |
| `tool_id` | 唯一标识，以小写字母开头，后接小写字母、数字或下划线；派生 `Scene.qd_settings.<tool_id>`、`presets/<tool_id>` 和翻译上下文 `QuickDestruction.<tool_id>` |
| `locales` | 工具语言文件目录 |
| `classes` | 按注册顺序排列的类，包括生成的参数面板 |
| `schema` | 参数规格；`None` 表示没有场景参数组 |
| `presets` | 启用通用预设，要求提供 `schema` |

| 接入约定 | 要求 |
| --- | --- |
| 命名 | 工具类使用 `QD_<TOOL_ID大写>_` 前缀，算子 ID 使用 `qd_<tool_id>.`；注册前检查标识、类名与 `bl_idname` 重复 |
| 面板 | 继承 `ToolPanel` 与 Blender `Panel`；主面板设置 `tool_id`，带预设时由 `draw_header_preset` 绘制标题栏入口 |
| 算子 | 继承 `ToolOperator` 与 Blender `Operator`，实现 `run(context)`；返回的非空文本作为 INFO 报告，可按需要增加 `invoke` 等回调 |
| 翻译 | 手写面板、菜单与算子声明英文 `bl_description`，规则见[翻译文档](Translations.md) |

`TOOLS` 决定工具注册及侧栏排列顺序。注册层生成 `QD_PG_<tool_id>`，依次登记翻译、类、`Scene.qd_settings` 与预设。注册失败或注销时逆序尝试清理，单步失败仍继续其他步骤并打印堆栈；当前不保留失败步骤供下次重试。

## 参数与界面

`Schema` 统一参数的 RNA 定义、读取、校验和预设写回。字段顺序约束在 `orders` 中声明，界面更新回调负责联动调整。MeshFracture 的参数生效条件集中在 `settings.py`，供界面和流水线使用。

- **布局**：主面板放操作按钮和常用参数，其余用 `ParamGroup` 分组。`closed=True` 默认收起，`popover=True` 在底部生成弹出按钮。
- **生成**：`parameter_panels(parent, schema, core, groups, active)` 返回面板类，并检查每个参数恰好列入一次。`core` 是主面板自己绘制的字段。
- **绘制**：`draw_fields` 将标签与数值分列，向量分量并排；`CheckGrid((字段名, ...))` 将布尔字段排成两列。
- **自绘**：分组可提供 `draw(layout, owner)`，仍需在字段表列出所绘字段，以便检查。
- **生效条件**：`active(owner, key)` 为假时只置灰，仍可编辑。标签应简短，不重复分组标题。

## 错误与预设

`ToolOperator` 将执行异常报告为“<操作>失败：<原因>”，返回 `CANCELLED`。领域错误使用 `DomainError`，按声明的上下文翻译消息和术语；参数校验失败使用 `InvalidParameterError`。其他异常显示原始文本并打印堆栈。业务数据的回滚由工具负责。

通用预设提供 `qd.preset_save`、`qd.preset_load`、`qd.preset_remove`、`qd.preset_open_directory` 和 `QD_MT_presets`。隐藏参数 `tool_id` 定位当前场景参数组；菜单通过布局上下文接收标识，绘制期间不写 ID 数据。

文件位于扩展用户目录的 `presets/<tool_id>/`，与扩展代码目录分开。名称经过净化，写入采用同目录临时文件替换，读取上限为 1 MiB。格式为：

```json
{"format_version": 1, "tool_id": "mesh_fracture", "values": {"first_cut_seeds": 100}}
```

保存全部参数；载入先检查格式、版本和工具标识，再由 `Schema.write` 校验写入，缺失字段保留当前值。遇到 `TypeError`、`ValueError` 或 `RuntimeError` 时尝试恢复待写字段的原值，恢复本身失败仍会抛错。载入支持撤销；保存、删除和打开目录不进入撤销栈。

## 碎块契约

`common/shards.py` 定义工具共用的物体属性：

| 属性 | 含义 |
| --- | --- |
| `_qd_source` | 生成时的完整源物体名 |
| `_qd_path` | 逐层序号；`parent_path(shard)` 返回去掉末项的父路径 |
| `_qd_level` | 路径长度，首次切割为 1 |
| `_qd_parent` | 已被细分但保留下来的父碎块，与子块几何重叠 |

`shards_of(objects, source_name)` 按来源名筛选并排除父碎块。标记不随源重命名更新，也不区分同一源的多次生成；处理本次产物时使用 API 返回的 `result.shards`。

## MeshFracture

目前唯一带完整计算链路的工具，参数与使用边界见[使用文档](MeshFracture.md)。

| 模块 | 职责 |
| --- | --- |
| `tool.py`、`settings.py`、`interaction.py` | 工具声明、参数规格、面板与生成算子 |
| `api.py` | 显式输入、切割前体检与结果对象 |
| `core/pipeline.py` | 输入检查、首次切割、递归、收尾、硬上限与失败清理 |
| `core/sampling.py`、`cells.py`、`cell_objects.py` | 种子采样、半空间求交、凸包及材质和数据层初始化 |
| `core/boolean.py`、`intersect.py` | 求值快照、Exact 求交、结果校验与布尔烘焙 |
| `core/recursion.py`、`allocation.py` | 候选选择与碎块预算分配；后者是纯数值逻辑 |
| `core/cleanup.py`、`islands.py` | 焊接、清理、非平面面三角化、孤岛拆分及最终面合并 |
| `core/isolation.py` | 临时排除已完成碎块，当前父块与新碎块使用工作集合；实时重绘时停用 |
| `core/interior.py`、`shard_objects.py`、`naming.py` | 断面处理、物体操作、路径命名与集合组织 |
| `core/diagnosis.py` | 非流形与自相交体检；共享顶点的面对不参与自相交判定 |

生成算子读取场景参数，使用 `UNDO` 与 `INTERNAL`，不进入算子搜索，也可由脚本调用。`invoke` 先体检并在发现问题时弹窗；`execute` 和阶段 API 不执行这项体检。

每次切割建立一份源网格数值快照；批次先准备胞体与修改器，再统一刷新并取回结果。递归中间结果先清理和三角化，以降低后续布尔处理非平面面的风险；共面面合并留到全部切割结束后，对最终碎块及保留的父碎块执行。

### 阶段 API

以下示例在 Blender 内运行，假设扩展安装在 `user_default` 仓库且已启用：

```python
import bpy

from bl_ext.user_default.quick_destruction.tools.MeshFracture.api import generate_fragments

context = bpy.context
sources = tuple(context.selected_editable_objects)
config = {"recursion": 0}
result = generate_fragments(context, sources, config, rng=None)
```

| 参数 | 契约 |
| --- | --- |
| `context` | 物体模式的 Blender 上下文 |
| `sources` | 源物体序列，非网格物体被忽略，网格按名称排序处理 |
| `config` | 参数映射；缺失字段取默认值，未知字段抛 `InvalidParameterError`；输出集合遵循 `collection_name` |
| `rng` | 可选局部随机流；`None` 时按 `random_seed` 新建 |

返回 `MeshFractureResult`：`shards` 是本次最终碎块元组，`source_to_shards` 是源物体到碎块的只读映射，物体本身仍可编辑。`seconds` 是流水线计时，不含最后的隔离释放、进度结束和选择恢复。

API 在退出时尝试恢复原选择和活动物体；生成算子成功后另行选中本次碎块。流水线失败时尝试回收本次创建的数据、释放隔离和结束进度，清理异常打印堆栈，继续其他收尾并重新抛出原流水线异常。选择恢复在外层执行，若其失败仍可能覆盖先前异常。

`source_issues(context, sources)` 返回 `{源物体: 问题集合}`，仅包含体检发现问题的网格物体。问题标识为 `NOT_CLOSED` 和 `SELF_INTERSECTING`；它是提示性检查，不证明输入几何有效。
