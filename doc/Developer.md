# 开发与验证

插件代码位于 `source/`，Blender 扩展入口为 `source/__init__.py`。使用 `uv sync` 建立开发环境；`ruff`、`pytest` 和 Blender 类型桩是开发依赖。当前验证的两个真实运行环境是 Blender 4.2.23（Python 3.11）与 Blender 5.2.2（Python 3.13）；`source/` 需同时支持两者，不使用 3.12 起才提供的语法与标准库接口。

## 修改工具

MeshFracture 的参数在 `source/MeshFracture/settings.py` 中集中声明。变更字段时同步检查 `interaction/panel.py` 的布局覆盖与启用条件、`source/locales/*.json` 的参数译文，以及预设格式校验。工具的公开计算入口在 `api.py`，算法实现位于 `core/`，算子与子面板位于 `interaction/`；预设界面由根层 `source/preset_ui.py` 统一提供，工具只需在自己的 `settings.py` 里声明 `PRESET`。

新增工具应创建独立包及 `ToolSpec` 声明，并加入 `source/tool_registry.py`；需要预设时给出 `ToolSpec.preset`。根层负责注册顺序、侧栏父面板、场景指针、通用预设界面和翻译目录；工具之间不互相导入。详细契约见 [架构文档](Architecture.md)。

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

`tests/unit` 用普通 Python 与 pytest；`tests/blender` 使用真实 Blender；`tmp/` 是本地临时目录，不进仓库。首次把脚本放入 `tests/` 前，按仓库 `agents.md` 的要求由用户运行确认。不要把安装了 `blender-python-stubs` 的环境中“能导入 `bpy`”当作脱离 Blender 的证明，应验证实际行为或显式拦截 Blender 模块。

改动算法时应在真实 Blender 中检查产物、命名、材质以及注册与注销；在 `-b` 后台模式下运行的脚本应加上 `--python-exit-code 1`，让断言失败体现为进程退出码。

界面代码还有一类后台模式覆盖不到的风险：绘制回调里写 ID 数据会被拒绝，异常又只记在控制台，界面表现为面板只剩标题、内容为空。`tests/blender/verify_ui_draw.py` 专门验证这一点，必须去掉 `-b` 运行。

它分两轮：第一轮把各工具的侧栏面板放进侧栏当前激活的分类，交给 Blender 自己绘制，验证注册与摆放，确认它们都是顶层面板，并核对折叠状态与 `ToolSpec.default_closed` 是否一致（默认收起的面板本来就不该被绘制）；第二轮另注册一个驱动面板，在它的 `draw` 里用真实布局与真实绘制上下文逐个驱动插件声明的全部绘制入口（工具面板、工具菜单、预设入口、框架预设菜单），并用转发型布局代理记录控件调用。入口与期望值都由 `ToolSpec` 推导，新增工具会自动纳入；判据是“不抛异常 + 控件数达标 + 面板提供本工具算子入口 + 带预设的工具在绘制期设置会话令牌”，并用一条负向自检确认驱动确实运行在受限上下文里。

## 打包与安装

在仓库根目录打包，把 `<Blender 可执行文件>` 换成本机安装的 Blender：

```powershell
<Blender 可执行文件> -c extension build --source-dir=./source
```

打包在仓库根目录生成扩展 ZIP，文件名带清单里的版本号。ZIP 里就是 `source/` 下的内容，`doc/`、`tests/`、`tmp/` 和 `install.bat` 都不会打进去。

装进 Blender 用根目录的 `install.bat`：

```powershell
install.bat 5.2 -Force /nopause
```

- 不写版本号时，自动选已配置的最高 Blender 版本目录；写 `5.2` 就装到 5.2 的配置里
- 已经有安装时脚本会先问一句再替换，加 `-Force` 就不问
- `/nopause` 表示装完不停下来等按键
- 只用 cmd 内置命令和系统自带的 `tar`，不依赖 PowerShell

脚本会先在自身目录和上级目录里找到并校验 ZIP，解压到暂存目录，备份旧安装后再替换；中途任何一步失败都会回滚，不会留下半个安装。预设存在 Blender 用户脚本目录里，和扩展本体分开，重装不会丢。装完重启 Blender 才会加载新代码。`install.bat` 是开发用的安装工具，不参与打包。
