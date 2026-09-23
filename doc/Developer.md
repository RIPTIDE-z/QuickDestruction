# 开发与验证

插件代码位于 `source/`，Blender 扩展入口为 `source/__init__.py`。使用 `uv sync` 建立开发环境；`ruff`、`pytest` 和 Blender 类型桩是开发依赖。当前支持的真实运行环境可用 `D:\program\blender-4.2.23\blender.exe` 与 `D:\program\Blender\blender.exe` 检查。

## 修改工具

MeshFracture 的参数在 `source/MeshFracture/settings.py` 中集中声明。变更字段时同步检查 `interaction/panel.py` 的布局覆盖与启用条件、`source/locales/*.json` 的参数译文，以及旧预设读取兼容性。工具的公开计算入口在 `api.py`；算法实现位于 `core/`，算子、子面板和预设交互位于 `interaction/`。

新增工具应创建独立包及 `ToolSpec` 声明，并加入 `source/tool_registry.py`。根层负责注册顺序、侧栏父面板、场景指针和翻译目录；工具之间不互相导入。详细契约见 [架构文档](Architecture.md)。

## 代码检查

修改代码后在仓库根目录依次运行：

```powershell
uv run ruff format .
uv run ruff check --fix .
uv run ruff check .
# 修复剩余问题后，再次运行：
uv run ruff check .
uv run ruff format .
```

`tests/unit` 用普通 Python 与 pytest；`tests/blender` 使用真实 Blender。临时验证脚本和输出放在 `tmp/`。本地 `tests/` 不纳入版本控制；将新脚本放入该目录前，先按仓库 `agents.md` 的要求由用户运行。不要把安装了 `blender-python-stubs` 的环境中“能导入 `bpy`”当作脱离 Blender 的证明，应验证实际行为或显式拦截 Blender 模块。

真实环境可用以下命令执行本地冒烟脚本：

```powershell
& 'D:\program\blender-4.2.23\blender.exe' -b --factory-startup --python-exit-code 1 --python tmp\script\verify_multitool_refactor.py
& 'D:\program\Blender\blender.exe' -b --factory-startup --python-exit-code 1 --python tmp\script\verify_multitool_refactor.py
```

脚本用临时目录隔离预设。改动算法时还应在真实 Blender 中执行切割冒烟，检查产物、命名、材质和注册/注销。

## 打包与安装

```powershell
& 'D:\program\Blender\blender.exe' -c extension build --source-dir=./source
```

构建完成后运行 `install.bat 5.2 -Force /nopause` 做安装测试。安装器验证 ZIP、解压到暂存目录并替换用户扩展中的 `quick_destruction`；新代码在 Blender 重新启动后加载。`install.bat` 是开发安装工具，不进入扩展 ZIP。
