# QuickDestruction

> 此工具代码使用 AI 辅助编写，可能有很多错误，不能 100% 保证稳定性。

用于 Blender 5.2 及以上版本的破坏动画工具集。所有工具都以独立面板的形式，出现在 3D 视图侧栏的 **QuickDestruction** 标签页里（中文界面显示为"快速破坏"），按使用顺序从上到下排列。

- [x] **MeshFracture／网格破碎**：在种子上构建 3D Voronoi 胞体，再与原物体做布尔求交，生成贴合原表面的预切割碎块，每个源物体的结果是一个破碎资产。算法派生自 [object_fracture_cell](https://projects.blender.org/extensions/object_fracture_cell)。
- [x] **Physics／物理设置**：按材料为碎块建立刚体、计算质量、把原点移到质心，设定碎块受击前是冻结、休眠还是自由下落。
- [x] **Drivers／破坏驱动**：用可拖动、可缩放的物体放置爆炸与推力，显示的形状就是作用范围；可以在视口里预览每块碎块的起动速度。
- [x] **Simulation／模拟烘焙**：把资产与驱动放在一起模拟，逐帧烘焙成关键帧，可按 Esc 取消。
- [x] **Rig／绑定构建**：由烘焙好的动画建立骨架与蒙皮网格并逐帧检查；可以选择先显示原物体再换成碎块。
- [x] **GameExport／游戏导出**：把建好的绑定导出成 FBX，供虚幻引擎与 Unity 导入（引擎预设尚未在引擎里逐项验证）。

## 安装

在项目根目录运行，将路径替换为本机 Blender 可执行文件：

```powershell
.\install.bat 'D:\program\Blender\blender.exe'
```

- **过程**：用指定的 Blender 打包、安装并启用扩展，安装到它使用的用户配置；开发验证可通过环境变量指定临时配置，见[开发文档](doc/开发文档/Developer.md)。
- **只打包**：加 `-package` 时只打包不安装，扩展包放在项目根目录：`.\install.bat 'D:\program\Blender\blender.exe' -package`。
- **重启**：安装后需要重启 Blender。

## 使用

1. 在物体模式下选中要切割的网格，按 **N** 打开侧栏，切到 **QuickDestruction** 标签页。
2. **网格破碎**：点击 **生成碎块**。
3. **物理设置**：选中一块碎块，选材料与受击前状态，点击 **应用物理**。
4. **破坏驱动**：点击 **爆炸** 或 **推力**，移动与缩放驱动来摆放作用范围；计划预览默认打开，可以先看每块碎块的起动速度。
5. **模拟烘焙**：选中碎块，点击 **烘焙模拟**，然后按播放查看动画。
6. **绑定构建**：点击 **构建绑定**。
7. **游戏导出**：点击 **导出 FBX**。

完整流程与修改方法见[快速上手](doc/使用说明/快速上手.md)。每一步都可以用 **Ctrl+Z** 撤销，各工具的参数都可以保存为预设。

## 文档

| 文档 | 内容 |
| --- | --- |
| [快速上手](doc/使用说明/快速上手.md) | 从模型到破碎动画的完整流程、修改与重来、状态含义 |
| [MeshFracture](doc/使用说明/MeshFracture.md) | 参数、命名、资产身份、输出与使用边界 |
| [Physics](doc/使用说明/Physics.md) | 材料、受击前状态、应用物理的细节与状态 |
| [Drivers](doc/使用说明/Drivers.md) | 爆炸与推力、作用范围、参数、衰减公式、计划预览，以及旧文件里其他驱动类型的规则 |
| [Simulation](doc/使用说明/Simulation.md) | 烘焙过程、取消与复位、状态、耗时与阶段 API |
| [Rig](doc/使用说明/Rig.md) | 绑定构建、原物体切换、检查与阶段 API |
| [GameExport](doc/使用说明/GameExport.md) | 引擎预设、FBX 导出、首次导入清单与阶段 API |
| [Architecture](doc/开发文档/Architecture.md) | 分层、工具契约、资产契约、数据归属、模拟会话与阶段 API |
| [Translations](doc/开发文档/Translations.md) | 语言文件与翻译检查 |
| [Developer](doc/开发文档/Developer.md) | 开发环境、代码检查、测试、测速、打包与安装 |
