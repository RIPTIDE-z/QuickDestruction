# 开发与验证

## 环境

- 用 `uv sync` 建立开发环境；`ruff`、`pytest` 与 Blender 类型桩是开发依赖。
- 插件代码位于 `source/`，结构与约定见[架构文档](Architecture.md)。
- 需要同时支持 Blender 4.2（Python 3.11）与 5.2（Python 3.13）。因此不使用 Python 3.12 起才有的语法与标准库接口。
- `tmp/` 是本地临时目录，不进仓库。新脚本放入 `tests/` 之前，需要按 `agents.md` 的要求征得同意。

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

两个 Blender 版本都要各跑一遍：

```powershell
uv run pytest
<Blender> -b --factory-startup --python-exit-code 1 --python tests\blender\verify_fracture.py
<Blender> --factory-startup --python-exit-code 1 --python tests\blender\verify_ui_draw.py
```

| 测试 | 运行方式 | 覆盖内容 |
| --- | --- | --- |
| `tests/unit` | pytest，不需要 Blender | 参数校验、预设文件与信封，以及语言文件的覆盖、陈旧与写法检查 |
| `verify_fracture.py` | 后台 Blender | 体积守恒、可复现、断面收尾、命名与集合结构、失败回滚、输入边界、重设原点、保留父碎块、碎块总数上限、点接触的部分 |
| `verify_ui_draw.py` | 真实界面，不能加 `-b` | 面板、子面板与弹出面板绘制不抛错，折叠状态正确，各绘制入口产生控件，以及绘制期不写 ID 数据 |

- **单元测试**：只导入不依赖 Blender 的模块。虚拟环境装了类型桩后，`import bpy` 也能成功，这不能当作脱离 Blender 的证明。
- **界面测试**：绘制入口由 `registration.TOOLS` 推导，新增工具会自动纳入。

## 打包与安装

```powershell
install.bat <Blender> [/nopause]     # 也可以先 set BLENDER=<Blender>
```

`install.bat` 全程使用指定的 Blender，所以装进的一定是这个 Blender 版本的配置，而且装的一定是刚打出的包：

1. `extension build`：把 `source/` 打包到临时目录；
2. `extension install-file -r user_default -e`：安装到 `user_default` 仓库并启用。覆盖安装会整体替换目录，不留旧文件；
3. `scripts/install_verify.py`：在后台 Blender 里核对。
   - 包内每个文件都逐字节一致；
   - 安装目录里没有包外的文件；
   - 扩展已启用并注册成功。

Blender 自带的 `install-file` 在出错时只打印信息，退出码仍为 0（4.2 连信息都不打印），所以以第 3 步的结果为准。任何一步失败都会打印 `INSTALL FAILED`，退出码为 1。

- **预设**：存放在扩展用户目录，重装不会丢失。
- **单独打包**：需要单独的扩展包时，运行 `<Blender> -c extension build --source-dir=./source`，ZIP 生成在仓库根目录。
