# 开发与验证

插件代码位于 `source/`，Blender 扩展入口为 `source/__init__.py`。使用 `uv sync` 建立开发环境；`ruff`、`pytest` 和 Blender 类型桩是开发依赖。当前验证的两个真实运行环境是 Blender 4.2.23（Python 3.11）与 Blender 5.2.2（Python 3.13）；`source/` 需同时支持两者，不使用 3.12 起才提供的语法与标准库接口。

## 修改工具

MeshFracture 的参数集中声明在 `source/MeshFracture/settings.py` 的 `PROPERTY_SPECS` 里。改动字段时，同步检查以下几处：

- `interaction.py` 中的分组表与启用条件；
- `MeshFracture/locales/*.json` 的译文，单元测试会报出缺失与陈旧的条目。

工具的公开计算入口在 `api.py`，算法实现位于 `core/`。预设界面由 `source/framework/presets.py` 统一提供，工具只需在 `ToolSpec` 上声明 `presets=True`。

新增工具的步骤：

1. 创建独立包；
2. 在包内的 `tool.py` 声明 `ToolSpec`；
3. 加入 `source/registration.py` 的 `TOOLS`；
4. 在包里准备自己的 `locales/`。

工具之间不互相导入，只依赖 `framework` 与 `common`。详细契约见[架构文档](Architecture.md)。
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

## 测试

以下每条命令都要分别在 Blender 4.2 与 5.2 上运行，把 `<Blender>` 换成本机的可执行文件：

```powershell
uv run pytest
<Blender> -b --factory-startup --python-exit-code 1 --python tests\blender\verify_fracture.py
<Blender> --factory-startup --python-exit-code 1 --python tests\blender\verify_ui_draw.py
```

- **`tests/unit`**：普通 Python 加 pytest，不需要 Blender。
  - 覆盖参数规格引擎、预设文件与信封，以及语言文件的严格检查（见[翻译维护](Translations.md)）；
  - 单元测试只导入不依赖 Blender 的模块。安装了 `blender-python-stubs` 后 `import bpy` 也能成功，这不能当作脱离 Blender 的证明。
- **`tests/blender/verify_fracture.py`**：在后台模式下直接调用阶段 API，核对以下内容：
  - 体积守恒（多种尺度、非等轴、递归、凹体加孤岛）与可复现性；
  - 断面收尾、多源命名与失败回滚；
  - 碎块上限、未知参数与链接库拒绝，以及重设原点。
- **`tests/blender/verify_ui_draw.py`**：必须在真实界面里运行，不能加 `-b`。
  - 它针对的是后台模式覆盖不到的风险：绘制回调里写 ID 数据会被拒绝，异常只记在控制台，面板只剩标题；
  - 第一轮把各工具的面板交给 Blender 自己绘制，确认它们都是顶层面板，并确认 `bl_options` 含 `DEFAULT_CLOSED` 的面板没有被绘制；
  - 第二轮用驱动面板在真实绘制上下文里逐个驱动全部绘制入口，判据如下：
    - 不抛异常，控件数达标；
    - 面板提供本工具算子入口，带预设的工具在绘制期写入工具标识；
    - 用一条负向自检，确认驱动确实运行在受限上下文里。
  - 入口由 `registration.TOOLS` 推导，新增工具会自动纳入。

`tmp/` 是本地临时目录，不进仓库。新脚本放入 `tests/` 之前，需要按 `agents.md` 的要求征得同意。
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

脚本会先在自身目录和上级目录里找到并校验 ZIP，解压到暂存目录，备份旧安装后再替换；中途任何一步失败都会回滚，不会留下半个安装。预设存在扩展用户目录里，与扩展本体分开，重装与升级都不会丢。装完重启 Blender 才会加载新代码。`install.bat` 是开发用的安装工具，不参与打包。
