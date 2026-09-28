- 这是一个用于 blender 的插件开发目录
    - 开发环境使用 uv 管理，主要为 blender5.2 + py313

- source/ 为插件主体代码
- doc/ 为插件说明文档
- analysis/ 为本地分析文档，对架构代码等的临时分析只允许写在这个文件夹
- scripts/ 为开发辅助脚本，例如 install.bat 调用的安装验证脚本，不会打包进插件

- tests/ 为测试文件夹
    - unit 用于普通python的单元测试，会使用 pytest 进行检查
        - unit 测试安装 pytest 标准进行编写
    - blender 则使用真实的 blender 可执行文件进行测试
    - 非必要不要新增和改动测试脚本，但是如果确定是因为需求改动代码后导致测试代码落后，可以在进行详细分析判断后更新测试代码
    - 测试脚本需要用户允许才能放入测试文件夹，且测试文件必须有清晰注释和日志输出，说明测试过程和测试目的

- tmp/ 为本地缓存文件夹
    - 存放一些临时脚本文件，可用于单次需求的测试和 unit test 的输出目录
        - tmp\script 存放测试脚本
        - tmp\output_tmp 可以作为 unit test 的输出目录
    - 不进入 git 仓库

- 测试需要知道 Blender 真实安装位置和要求测试的版本(比如5.2)，如果没有这些信息则需要询问用户得到准确信息后才能进行测试

- 项目使用 ruff 进行格式化和问题检查，每次修改代码都必须按照以下流程进行检查
    1. 在根目录运行一次 `uv run ruff format .` 进行格式化
    2. 在根目录运行一次 `uv run ruff check --fix .` 进行安全的自动化修复
    3. 在根目录运行一次 `uv run ruff check .` ，检查需要手动修复的问题并进行修复
    4. 修复完毕后再次运行 `uv run ruff check .` 以及 `uv run ruff format .` 确保问题已解决，如果还有问题则重复 3

- 若有测试文件，则按测试标准进行单元测试和blender真实运行环境测速

- 所有修改完毕后，需要进行一次插件打包和安装测试
    - 在根目录运行 `install.bat <path_to_blender> /nopause`，它会用这个 Blender 完成打包、安装、启用和安装验证
    - 以输出的 `INSTALL SUCCEEDED` 和退出码 0 为准，失败时会输出 `INSTALL FAILED` 以及具体的 `[FAIL]` 项
    - 只需要扩展包而不安装时，按 `<path_to_blender> -c extension build --source-dir=./source` 进行打包

- 所有代码都必须要有干净且清晰的注释
    - 包括函数/类的作用
    - 函数内部逻辑
    - 类成员意义
    - 注释不使用句号和任何中文符号如 ，；。“”（）