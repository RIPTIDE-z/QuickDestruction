> 此工具代码使用 AI 辅助编写，可能有很多错误，不能100%保证稳定性

- 若要进行代码开发工作，请阅读 [开发文档](doc/Developer.md)

# MeshFracture - 网格破碎

本工具为破坏动画流程生成预切割碎块，含有以下功能：

- [x] `MeshFracture` 参考并派生自 [object_fracture_cell](https://projects.blender.org/extensions/object_fracture_cell)
    - 在种子点上构建 3D Voronoi 图，把每个胞体做成凸多面体，再与原物体做**布尔求交**，得到贴合原表面的碎块，用于破坏动画预切割
- [ ] 刚体设定和自定义外力
- [ ] 破坏动画生成
- [ ] 自动绑定以及动画烘焙


## 构建

在本目录运行：

```text
<path_to_blender> -c extension build --source-dir=./source
```

## 安装

直接双击 `install.bat`

```bat
install.bat              :: 自动定位 zip 与 Blender 配置版本并安装
install.bat 5.2          :: 指定 Blender 配置版本目录
install.bat 5.2 -Force   :: 覆盖已安装版本时不询问
install.bat /nopause     :: 结束后不等待按键，可用于自动化测试
```

安装器优先搜索脚本所在目录，再搜索父目录；每个目录按修改时间从新到旧选择首个 QuickDestruction ZIP。脚本目录中已有合格包时，不会被父目录的包替换。

检测到已有安装时会询问是否覆盖，**默认是覆盖**：直接回车或输入 `y` 即替换，其他非空输入取消。更新后需重启 Blender 以加载新代码。

## 使用

有两个入口，参数同一套逻辑，可混用：

1. 菜单（弹窗）：3D 视图 → **物体** → **快速效果** → **网格破碎**
2. 侧栏面板（常驻）：3D 视图按 **N** → **QuickDestruction** 标签页 → **MeshFracture／网格破碎** 面板，点击 **Generate Fragments** 生成碎块

标签页里的每个工具是一块独立折叠的面板，可以直接收起不用的工具；后续新增的工具会作为同级面板出现在同一标签页下。

弹窗打开时读取侧栏设置，取消不写回；切割成功后同步设置。若几何已经生成但侧栏同步失败，会保留切割结果并报告警告，仍可撤销。默认开启一轮递归，因此最终碎块数不等于初始种子数。

更多使用、参数和算法说明见 [MeshFracture](doc/MeshFracture.md)。模块与接口见 [Architecture](doc/Architecture.md)。
