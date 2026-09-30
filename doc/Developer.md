# 开发与验证

## 环境

- 开发环境以 Python 3.13 为基准，用 `uv sync --python 3.13` 安装开发依赖，包括 `ruff`、`pytest` 与 Blender 类型桩。
- 插件代码位于 `source/`，结构与约定见[架构文档](Architecture.md)。
- 插件需要支持 Blender 4.2（Python 3.11）与 5.2（Python 3.13），不使用 Python 3.12 起才有的语法与标准库接口。Ruff 当前目标为 `py313`，通过检查不代表满足 3.11 兼容性，仍需在 Blender 4.2 中验证。
- `tmp/` 不进仓库，临时脚本放在 `tmp/script`，输出放在 `tmp/output_tmp`。新增正式测试需遵循 [AGENTS.md](../AGENTS.md)。

## 代码检查

修改代码后，在仓库根目录依次运行：

```powershell
uv run ruff format .
uv run ruff check --fix .
uv run ruff check .        # 手动修复剩余问题后再运行一次, 直到通过
uv run ruff format .
```

ruff 还会检查圈复杂度（上限 10）与函数参数个数（上限 8）。

## 测试

在仓库根目录运行以下 PowerShell 命令，将示例路径替换为本机安装位置。总入口先跑单元测试，再依次测试两个 Blender；任一项失败，退出码为 1：

```powershell
$blender52 = 'D:\program\Blender\blender.exe'
$blender42 = 'D:\program\blender-4.2.23\blender.exe'
uv run python scripts/run_tests.py $blender52 $blender42
```

选项 `--skip-unit` 不跑单元测试，`--skip-ui` 不跑会弹出 Blender 窗口的界面测试。也可以单独运行：

```powershell
uv run pytest
& $blender52 -b --factory-startup --python-exit-code 1 --python tests\blender\verify_fracture.py
& $blender52 --factory-startup --python-exit-code 1 --python tests\blender\verify_ui_draw.py
```

| 测试 | 运行方式 | 覆盖内容 |
| --- | --- | --- |
| `tests/unit` | pytest，不需要 Blender | 参数校验与默认值解析，预设文件与信封，语言文件的覆盖、陈旧与写法检查，设置项的联动规则，细分名额分配，碎块标记 |
| `verify_fracture.py` | 后台 Blender | 体积守恒、可复现、断面收尾、命名与集合结构、失败回滚、输入边界、重设原点、保留父碎块、碎块总数上限与首次切割种子数、点接触的部分、孤岛拆分、共面面合并、种子扰动、材质与数据层、布尔策略、求交结果整理、依赖图隔离、分批切割、硬上限提前预判、非等轴权重剪枝 |
| `verify_inputs.py` | 后台 Blender | 源网格体检（不封闭与自相交），各种切割种子来源与回退 |
| `verify_operator.py` | 后台 Blender | 生成算子，预设的保存、读取与删除，注册与失败回滚 |
| `verify_ui_draw.py` | 真实界面，不能加 `-b` | 面板、子面板与弹出面板绘制不抛错，折叠状态正确，各绘制入口产生控件，绘制期不写 ID 数据，源网格有问题时的确认对话框 |

- **单元测试**：只导入不依赖 Blender 的模块。虚拟环境装了类型桩后，`import bpy` 也能成功，这不能当作脱离 Blender 的证明。
- **Blender 测试**：后台脚本共用 `tests/blender/harness.py`，界面脚本有独立计时和绘制检查。通过需同时满足退出码 0 与 `ALL PASS`，测试覆盖所列场景，不证明所有输入正确。
- **界面测试**：绘制入口由 `registration.TOOLS` 推导，新增工具会自动纳入。

## 打包与安装

```powershell
.\install.bat $blender52 /nopause
```

也可用 `$env:BLENDER = $blender52` 指定路径后运行 `.\install.bat /nopause`。省略 `/nopause` 时，脚本结束后等待按键。

安装流程全程使用指定的 Blender，目标由它的用户配置决定。开发验证时，将 `BLENDER_USER_CONFIG`、`BLENDER_USER_EXTENSIONS`、`BLENDER_USER_SCRIPTS` 和 `BLENDER_USER_DATAFILES` 指向 `tmp/output_tmp` 下的独立目录，避免覆盖已有安装与设置。

1. `extension build`：把 `source/` 打包到临时目录；
2. `extension install-file -r user_default -e`：安装到 `user_default` 仓库并启用；
3. `scripts/install_verify.py`：在后台 Blender 里核对。
   - 包内每个文件都逐字节一致；
   - 安装目录里没有包外文件，运行期 `__pycache__` 缓存除外；
   - 扩展已启用并注册成功。

不能仅凭 `install-file` 的退出码判断安装成功。以验证判据全部通过、`INSTALL SUCCEEDED` 和退出码 0 为准；打包或验证失败输出 `INSTALL FAILED`，退出码为 1。

- **预设**：存放在扩展用户目录，重装不会丢失。
- **单独打包**：运行 `& $blender52 -c extension build --source-dir=./source`，ZIP 生成在仓库根目录。
