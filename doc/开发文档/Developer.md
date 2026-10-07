# 开发与验证

## 环境

- 开发环境以 Python 3.13 为基准，用 `uv sync --python 3.13` 安装开发依赖，包括 `ruff`、`pytest` 与 Blender 类型桩。
- 插件代码位于 `source/`，结构与约定见[架构文档](Architecture.md)。
- 插件只支持 Blender 5.2 及以上，扩展清单的 `blender_version_min` 为 `5.2.0`。可以使用 Python 3.13 的语法与标准库。
- `tmp/` 不进仓库，临时脚本放在 `tmp/script`，输出放在 `tmp/output_tmp`。新增正式测试需遵循 [AGENTS.md](../../AGENTS.md)。

## 代码检查

修改代码后，在仓库根目录依次运行：

```powershell
uv run ruff format .
uv run ruff check --fix .
uv run ruff check .        # 手动修复剩余问题后再运行一次, 直到通过
uv run ruff format .
```

ruff 还会检查圈复杂度（上限 10）与函数参数个数（上限 8）。

## 注释约定

代码的注释一律用中文，只用 ASCII 标点，不写句号。注释写"做什么"与"为什么"，不逐字翻译代码，且必须与实现一致。

| 位置 | 要求 |
| --- | --- |
| 模块 | 文档字符串写职责与关键约束 |
| 类 | 文档字符串写职责 |
| 类成员，数据类与具名元组的字段，模块常量 | 上方一行注释，写含义，单位或取值范围；分组的常量可以共用一行 |
| 函数与方法 | 文档字符串首行写用途与特殊行为；有参数时每个参数一行"名字 含义"；有返回值时最后一行写"返回 ..." |
| 函数内部 | 按逻辑步骤写注释；每个分支，循环与关键判断说明原因，而不是复述条件 |
| 异常与回退 | 说明为什么要捕获，以及为什么这样处理 |

文档字符串示例：

```python
def bone_name(number):
    """会动碎块的骨骼名: b_ 加从 1 开始的顺序号, 例如 b_001; 只含 ASCII

    number 从 0 开始的顺序号
    返回 骨骼名
    """
```

单行不超过 100 列，中文按每字两列计算，过长时手动换行。Blender 的 `bl_idname`、`bl_label` 这类约定属性不逐个加注释，它们的含义由 Blender 规定。测试脚本里的参数化测试与场景函数用文档字符串说明场景，辅助函数只要求用途清楚。

## 测试

在仓库根目录运行以下 PowerShell 命令，把示例路径替换为本机安装位置。总入口先跑单元测试，再跑全部 Blender 测试；任一项失败，退出码为 1：

```powershell
$blender = 'D:\program\Blender\blender.exe'
uv run python scripts/run_tests.py $blender
```

选项 `--skip-unit` 不跑单元测试，`--skip-ui` 不跑会弹出 Blender 窗口的界面测试。也可以单独运行：

```powershell
uv run pytest
& $blender -b --factory-startup --python-exit-code 1 --python tests\blender\verify_dynamics.py
& $blender --enable-event-simulate --factory-startup --python-exit-code 1 --python tests\blender\verify_ui_draw.py
```

| 测试 | 运行方式 | 覆盖内容 |
| --- | --- | --- |
| `tests/unit` | pytest，不需要 Blender | 参数校验与默认值，预设文件与信封，语言文件的覆盖、陈旧与写法，设置项联动，细分名额分配，碎块标记，冲量模型（旋涡、区域优先级、质量响应、曲线方向、撞击事件）与驱动契约，弹道（穿越点配对、穿透预算、撞击作用），分层与契约键守卫 |
| `verify_fracture.py` | 后台 Blender | 体积守恒、可复现、断面收尾、命名与集合结构、失败回滚、输入边界、重设原点、保留父碎块、上限与种子数、孤岛拆分、面合并、种子扰动、材质与数据层、布尔策略、依赖图隔离、分批切割、资产身份与持久性 |
| `verify_inputs.py` | 后台 Blender | 源网格体检（不封闭与自相交），各种切割种子来源与回退 |
| `verify_operator.py` | 后台 Blender | 生成算子，预设的保存、读取与删除，注册与失败回滚，挂载参数组，注销失败后的重试 |
| `verify_dynamics.py` | 后台 Blender | 预设地面（盒体被动刚体、顶面齐平、重复添加重新对齐、失败撤回、碎块落在地面上），物理应用（质量、质心、静止姿态、单位与缩放、总质量与穿透阻力记录、撤回）、脚本改变换后不刷新直接调用、材料联动、破坏驱动、交接精度（含时间缩放与分数帧率）、冻结与锚定、驱动角色（旋涡、区域、曲线方向、弹道与飞行动画、质量响应、受影响的碎块与计划预览）、质量档与子步建议、起动受阻（冻结邻块与环境地面挡住，反弹不随子步放大，部分覆盖与多驱动，途中起动）、途中再交接、多资产会话与环境刚体、取消与各阶段失败、过期原因、复位与守卫、确定性、保存重开 |
| `verify_export.py` | 后台 Blender | 绑定构建（守恒、全部帧蒙皮与碎块动画一致、命名与集合、动静分类）、骨骼上限（拆段与拆骨架）、骨骼名退回、断面盒投影 UV、校验失败的回滚、各种状态与过期、FBX 导出（覆盖确认、错误情形、现场还原）与导出文件重新导入后的逐帧顶点比对 |
| `verify_ui_draw.py` | 真实界面，不能加 `-b`，需要 `--enable-event-simulate` 才能发真实的 Esc | 全部面板、子面板与弹出面板绘制不抛错，折叠状态正确，绘制期不写 ID 数据，源网格问题对话框，模态烘焙的启动、推进、完成与运行锁释放，计划预览的真实绘制，烘焙中按 Esc、载入文件与停用插件时的撤回与资源释放 |

- **单元测试**：只导入不依赖 Blender 的模块。虚拟环境装了类型桩后，`import bpy` 也能成功，这不能当作脱离 Blender 的证明。
- **Blender 测试**：后台脚本共用 `tests/blender/harness.py`，界面脚本有独立计时和绘制检查。通过需同时满足退出码 0 与 `ALL PASS`。测试覆盖所列场景，不证明所有输入正确。
- **界面测试**：绘制入口由 `registration.TOOLS` 推导，新增工具会自动纳入。

## 测速

`scripts/bench_pipeline.py` 是开发辅助脚本，不是测试。它在后台 Blender 里按几种碎块规模跑完"切割 → 应用物理 → 爆炸驱动 → 烘焙 60 帧"，输出各阶段耗时：

```powershell
& $blender -b --factory-startup --python-exit-code 1 --python scripts\bench_pipeline.py -- 100 300 1000
```

每个里程碑跑一次并记录结果，用来发现性能退化。

## 打包与安装

```powershell
.\install.bat $blender /nopause
```

也可用 `$env:BLENDER = $blender` 指定路径后运行 `.\install.bat /nopause`。省略 `/nopause` 时，脚本结束后等待按键。

只打包不安装时加 `-package`：`.\install.bat $blender -package /nopause`。扩展包复制到项目根目录（`.gitignore` 忽略根目录的 zip），输出 `PACKAGE SUCCEEDED`；失败时输出 `PACKAGE FAILED`。

安装流程全程使用指定的 Blender，目标由它的用户配置决定。开发验证时，将 `BLENDER_USER_CONFIG`、`BLENDER_USER_EXTENSIONS`、`BLENDER_USER_SCRIPTS` 和 `BLENDER_USER_DATAFILES` 指向 `tmp/output_tmp` 下的独立目录，避免覆盖已有安装与设置。安装分三步：

1. `extension build`：把 `source/` 打包到临时目录；
2. `extension install-file -r user_default -e`：安装到 `user_default` 仓库并启用；
3. `scripts/install_verify.py`：在后台 Blender 里核对：
   - 包内每个文件都逐字节一致；
   - 安装目录里没有包外文件，运行期 `__pycache__` 缓存除外；
   - 扩展已启用并注册成功。

不能仅凭 `install-file` 的退出码判断安装成功。以验证判据全部通过、`INSTALL SUCCEEDED` 和退出码 0 为准；打包或验证失败输出 `INSTALL FAILED`，退出码为 1。

- **预设**：存放在扩展用户目录，重装不会丢失。
- **单独打包**：运行 `& $blender -c extension build --source-dir=./source`，ZIP 生成在仓库根目录。
