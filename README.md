# QuickDestruction

> 此工具代码使用 AI 辅助编写，可能有很多错误，不能 100% 保证稳定性。

用于 Blender 4.2 及以上版本的破坏动画工具集。所有工具都以独立面板的形式，出现在 3D 视图侧栏的 **QuickDestruction** 标签页里（中文界面显示为"快速破坏"）。

- [x] **MeshFracture／网格破碎**：在种子点上构建 3D Voronoi 胞体，再与原物体做布尔求交，生成贴合原表面的预切割碎块。算法派生自 [object_fracture_cell](https://projects.blender.org/extensions/object_fracture_cell)。
- [ ] **PhysicalSettings／物理设置**：占位面板，参数与逻辑待实现。
- [ ] 刚体设定和自定义外力
- [ ] 破坏动画生成
- [ ] 自动绑定以及动画烘焙

## 安装

在仓库根目录打包，然后安装：

```bat
<Blender 可执行文件> -c extension build --source-dir=./source
install.bat            :: 自动选择已配置的最高 Blender 版本
install.bat 5.2        :: 安装到指定版本
install.bat 5.2 -Force :: 覆盖已有安装时不询问
```

安装后需要重启 Blender。

## 使用

1. 在物体模式下选中要切割的网格。
2. 按 **N** 打开侧栏，切到 **QuickDestruction** 标签页。
3. 在 **MeshFracture** 面板里调整参数，点击 **Generate Fragments**。

源物体始终保留，按 **Ctrl+Z** 可以撤销整次生成。参数可以保存为预设。

## 文档

| 文档 | 内容 |
| --- | --- |
| [MeshFracture](doc/MeshFracture.md) | 参数、命名、输出与使用边界 |
| [Architecture](doc/Architecture.md) | 框架分层、工具契约、预设与阶段 API |
| [Translations](doc/Translations.md) | 语言文件与翻译检查 |
| [Developer](doc/Developer.md) | 开发环境、代码检查、测试、打包与安装 |
