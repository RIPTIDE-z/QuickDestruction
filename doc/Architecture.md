# 模块与接口

## 目录职责

| 位置 | 职责 |
| --- | --- |
| `source/__init__.py` | 延迟导入注册装配 |
| `registration.py` | 显式类顺序、Scene 指针、菜单、handler |
| `properties.py` | 场景聚合根 `QDSettings` |
| `i18n.py`, `common/localization.py`, `locales/*.json` | 根翻译域、目录加载校验、界面 / 报告适配与各语言译文 |
| `common/schema.py`, `rna.py`, `errors.py` | 接收 specs 的校验、转换、RNA 工厂、字段异常 |
| `common/preset_storage.py` | 调用方指定目录的 JSON 存储 |
| `common/created_data.py`, `blender_context.py` | 本次新建数据回收、BMesh 生命周期、选择恢复 |
| `CellFracture/settings.py`, `properties.py` | 29 字段唯一规格与阶段 RNA |
| `CellFracture/operators.py`, `panel.py`, `presets.py` | 交互、布局与预设会话 |
| `CellFracture/presentation.py` | 错误字段呈现 |
| `CellFracture/api.py`, `pipeline.py` | 显式阶段入口与递归编排 |
| `CellFracture/sampling.py`, `cells.py` | 点源 / 球面采样与半空间胞体 |
| `CellFracture/mesh_ops.py`, `boolean.py`, `naming.py` | 网格操作、布尔策略、命名与集合 |
| `CellFracture/constants.py`, `errors.py` | 领域常量与纯异常 |
| 根目录 `install.bat` | 开发安装器, 不进入扩展 ZIP |

依赖方向: 根装配 → 业务界面 → API → pipeline → 几何模块
业务可使用 common, common 不导入业务
cells 仅依赖 mathutils、标准库、常量与纯异常
单独导入 cells 不加载 RNA、面板或翻译

## 阶段 API

从已安装扩展的 `CellFracture.api` 导入 `fracture`
签名: `fracture(context, sources, config, output_collection, *, rng=None)`

| 参数 | 契约 |
| --- | --- |
| `context` | Blender 物体模式上下文, 包含场景与视图层 |
| `sources` | 显式源物体序列, 由调用者提供当前视图层可编辑物体, 非网格忽略 |
| `config` | 用户字段映射, 缺字段从唯一 specs 补齐, 不写入 tracker 或 RNG |
| `output_collection` | 集合对象或名称, 名称不存在时新建, `None` 或空字符串使用当前集合 |
| `rng` | 可选局部 `random.Random`, 提供时按原顺序消费该流 |

显式 output_collection 决定输出位置, API 不从 config.collection_name 隐式取目标
交互算子将该字段显式传给 API
完成或失败后恢复原选择和 active, 交互算子再主动选中产物

| `FractureResult` 字段 | 含义 |
| --- | --- |
| `shards` | 碎块对象 tuple, 不含 DebugPoints |
| `source_to_shards` | 只读映射, 键为本次源对象, 值为对应碎块 tuple |
| `seconds` | 切割编排耗时 |
| `diagnostics` | 当前为空 tuple, 失败通过异常报告 |

结果容器只读, Blender 对象本身仍可编辑, 映射仅供当前运行使用
`complete_config` 从 specs 生成只读配置, 可再次传入 API
CreatedData 仅回收本次创建的数据, 不是既有数据的完整事务或撤销系统
