- 这是一个用于 blender 的插件开发目录
    - 开发环境使用 uv 管理，主要为 blender5.2 + py313

- source/ 为插件主体代码
- doc/ 为插件说明文档
- analysis/ 为本地分析文档，对架构代码等的临时分析只允许写在这个文件夹

- tests/ 为测试文件夹
    - unit 用于普通python的单元测试，会使用 pytest 进行检查
        - unit 测试安装 pytest 标准进行编写
    - blender 则使用真实的 blender 可执行文件进行测试
    - 非必要不要新增和改动测试脚本
    - 测试脚本需要用户运行才能放入测试文件夹，且测试文件必须有清晰注释和日志输出，说明测试过程和测试目的

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
    - 按 `<path_to_blender> -c extension build --source-dir=./source` 进行打包
    - 使用根目录的 `install.bat` 进行安装

- 所有代码都必须要有干净且清晰的注释
    - 包括函数/类的作用
    - 函数内部逻辑
    - 类成员意义