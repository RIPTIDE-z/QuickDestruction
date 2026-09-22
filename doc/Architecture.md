# 模块与接口

## 目录职责

| 位置 | 职责 |
| --- | --- |
| `source/__init__.py` | 延迟导入注册装配 |
| `registration.py` | 显式类顺序、Scene 指针、菜单、handler |
| `properties.py` | 场景聚合根 `QDSettings` |
| `i18n.py` | 根翻译域与界面 / 报告适配 |
| `common/schema.py`, `rna.py`, `errors.py` | 接收 specs 的校验、转换、RNA 工厂、字段异常 |
| `common/preset_storage.py` | 调用方指定目录的 JSON 存储 |
| `common/created_data.py`, `blender_context.py` | 本次新建数据回收、BMesh 生命周期、选择恢复 |
| `CellFracture/settings.py`, `properties.py` | 29 字段唯一规格与阶段 RNA |
| `CellFracture/operators.py`, `panel.py`, `presets.py` | 交互、布局与预设会话 |
| `CellFracture/presentation.py`, `translations.py` | 错误字段呈现与纯消息数据 |
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

## 持久化与接口契约

- 扩展 id `quick_destruction`, 最低 Blender 4.2
- 主算子 `object.add_quick_destruction_objects`, 仅 `UNDO`
- 四个 `qd.preset_*`, 面板 `QD_PT_settings`, RNA 类名保持
- `Scene.qd_settings.fracture` 与 `preset_name` 保持
- 旧 JSON 格式与 `quick_destruction/presets` 目录保持
- 旧 `qd_*` 模块与 `pipeline.main` 已移除, 脚本统一调用阶段 API
- 内部组名仍为 `内部`, 通过 `obj.vertex_groups.get("内部")` 读取
- 故障注入直接指向当前实现模块

注册中途失败自动回滚仍是已知未实现项, 本轮未加入行为修复
未来动画与绑定按实际需求建立同级业务包, 当前不创建空框架

## 清理复核

- 28 个 Python 模块, 58 条内部依赖, 包含函数内延迟导入的静态依赖图无环
- 根目录不再保留 qd_* 转发文件, pipeline 不反向依赖 api
- 注册仅由根 registration 管理, 面板与预设模块无重复注册入口
- 删除旧内部组查询兼容函数和未使用的布尔 collection 参数
- 未发现未使用导入或无引用的顶层定义, Blender 回调由宿主调用
- mass / mass_mode 等持久化字段保持, 不因清理旧 Python 入口改变场景和 JSON 结构

业务对比与故障注入未发现本轮引入的严重回归, 检查范围见 [Validation](Validation.md)
