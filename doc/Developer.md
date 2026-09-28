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
| `verify_fracture.py` | 后台 Blender | 体积守恒、可复现、断面收尾、命名、失败回滚、输入边界、重设原点 |
| `verify_ui_draw.py` | 真实界面，不能加 `-b` | 面板绘制不抛错、折叠状态正确、各绘制入口产生控件，以及绘制期不写 ID 数据 |

- **单元测试**：只导入不依赖 Blender 的模块。虚拟环境装了类型桩后，`import bpy` 也能成功，这不能当作脱离 Blender 的证明。
- **界面测试**：绘制入口由 `registration.TOOLS` 推导，新增工具会自动纳入。

## 打包与安装

```powershell
<Blender> -c extension build --source-dir=./source
install.bat 5.2 -Force /nopause
```

- **打包**：在仓库根目录生成带版本号的 ZIP，内容只有 `source/`。
- **`install.bat` 的参数**：
  - 不写版本号时，选已配置的最高版本；
  - 已有安装时默认询问，直接回车表示覆盖，`-Force` 表示不询问；
  - `/nopause` 表示结束时不等待按键。
- **找包顺序**：先找脚本目录，再找上级目录；每个目录取修改时间最新的 QuickDestruction ZIP。
- **安装过程**：先解压到暂存目录，备份旧安装后再替换，任何一步失败都会回滚。
- **依赖**：只用 cmd 内置命令与系统自带的 `tar`。
- **预设**：存放在扩展用户目录，重装不会丢失。
