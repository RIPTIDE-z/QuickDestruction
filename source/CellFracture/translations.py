"""预切割翻译目录 - 参数按字段组织并与英文规格分离"""

from ..common.localization import merge_messages, property_messages
from .settings import PROPERTY_SPECS

# 英文标签与提示由 settings 维护 - 此处仅维护稳定标识对应的译文
ZH_HANS_PROPERTIES = {
    "source": {
        "name": "来源",
        "description": "合并所选来源的种子点；子物体仅指直接子物体。未找到点时回退到源网格自身顶点。",
        "items": {
            "VERT_OWN": {
                "name": "自身顶点",
                "description": "使用源网格修改器之前的原始顶点，转换为世界空间坐标。",
            },
            "VERT_CHILD": {
                "name": "子物体顶点",
                "description": "使用直接子物体顶点；网格子物体使用原始顶点，可转换的其他子物体使用求值几何。",
            },
            "PARTICLE_OWN": {
                "name": "自身粒子",
                "description": "使用源物体全部粒子系统的粒子位置，不包含粒子系统的子粒子实例。",
            },
            "PARTICLE_CHILD": {
                "name": "子物体粒子",
                "description": "使用直接子物体上的粒子系统，不是源粒子系统中的子粒子。",
            },
            "PENCIL": {
                "name": "注解铅笔",
                "description": "使用活动注解层和活动帧的笔画点，不读取蜡笔物体几何。",
            },
        },
    },
    "source_limit": {
        "name": "种子点上限",
        "description": "去重前最多保留的种子点数，0 表示全部保留。不会补充种子点，也不保证最终碎块数量。",
    },
    "source_noise": {
        "name": "噪波",
        "description": "种子点随机位移，最大距离为世界空间包围盒对角线一半乘以此值；0 表示不扰动。",
    },
    "random_seed": {
        "name": "随机种子",
        "description": "控制采样、噪波、递归选择与数值抖动的随机种子。同一 Blender 版本下，相同输入与种子可复现切割；修改此值可生成不同图案，0 也是固定种子。",
    },
    "cell_scale": {
        "name": "胞体缩放",
        "description": "胞体分割平面的轴向权重，各轴为 0.001–1，改变平面方向和距离，不缩放物体；三轴权重相等时与未缩放分区相同。",
    },
    "recursion": {
        "name": "递归",
        "description": "首次切割后的额外细分轮数；0 表示不递归。增长量还受参与比例与停止阈值控制。",
    },
    "recursion_source_limit": {
        "name": "递归种子上限",
        "description": "每个递归碎块在去重前最多采样的种子点数；0 表示保留全部可用点。达到父碎块顶点数后此项不再产生变化。",
    },
    "recursion_clamp": {
        "name": "碎块停止阈值",
        "description": "每个源物体的当前碎块数达到此阈值后停止继续递归；单次细分或孤岛拆分可能超出阈值。0 表示关闭阈值，首次切割不受此项限制。",
    },
    "recursion_chance": {
        "name": "递归参与比例",
        "description": "每轮选中当前碎块的比例，数量向下取整；0 不选，1 全选。具体碎块由递归选择方式决定。",
    },
    "recursion_chance_select": {
        "name": "递归对象",
        "description": "递归比例小于 1 时决定选哪些碎块；大小按世界空间包围盒对角线衡量，游标距离按物体原点计算。",
        "items": {
            "RANDOM": {"name": "随机", "description": "随机选取指定比例的碎块。"},
            "SIZE_MIN": {
                "name": "小碎块",
                "description": "优先选择世界空间包围盒对角线较小的碎块。",
            },
            "SIZE_MAX": {
                "name": "大碎块",
                "description": "优先选择世界空间包围盒对角线较大的碎块。",
            },
            "CURSOR_MIN": {"name": "靠近游标", "description": "优先选择原点更靠近 3D 游标的碎块。"},
            "CURSOR_MAX": {"name": "远离游标", "description": "优先选择原点更远离 3D 游标的碎块。"},
        },
    },
    "use_smooth_faces": {
        "name": "平滑内侧面",
        "description": "对新生成的内部断面使用平滑着色，外表面着色由源网格继承。",
    },
    "use_sharp_edges": {
        "name": "锐边",
        "description": "把内部断面与外表面交界处的边标记为锐边，与内部平滑着色开关独立。",
    },
    "use_sharp_edges_apply": {
        "name": "应用拆分边",
        "description": "启用锐边时实际拆开所有锐边，包括继承的锐边；会复制顶点，可能让原本封闭的碎块变为不水密网格。",
    },
    "use_data_match": {
        "name": "匹配数据",
        "description": "复制材质槽并创建对应的 UV/颜色层；布尔插值传递受支持的外表面数据，内部断面不会自动展开 UV。",
    },
    "use_island_split": {
        "name": "拆分孤岛",
        "description": "将每个布尔结果的不连通部分拆成独立物体，可能使碎块数量多于种子点数量。",
    },
    "margin": {
        "name": "内缩量",
        "description": "每个种子分割平面向内偏移的世界空间长度，单位为 Blender 单位；相对两面的间隙约为此值两倍，过大会使胞体消失。",
    },
    "material_index": {
        "name": "材质索引",
        "description": "内部断面使用的材质槽索引，从 0 开始；0 为第一个槽。需要启用匹配数据且存在对应材质槽才能显示材质。",
    },
    "use_interior_vgroup": {
        "name": "内部顶点组",
        "description": "在拆开锐边之前，为仅属于内部断面的顶点创建“内部”顶点组；排除与外表面共享的边界顶点，拆边副本不重新分类。",
    },
    "mass_mode": {
        "name": "质量模式",
        "description": "兼容旧预设的占位参数；切割流程不使用它，也不创建或修改刚体，不分配碎块质量。",
        "items": {
            "VOLUME": {
                "name": "按体积比例",
                "description": "无效的旧版选项：当前不按包围盒体积分配质量。",
            },
            "UNIFORM": {
                "name": "统一质量",
                "description": "无效的旧版选项：当前不执行统一质量赋值。",
            },
        },
    },
    "mass": {
        "name": "质量",
        "description": "兼容旧预设的占位参数，单位千克；切割流程不分配质量，也不创建刚体。",
    },
    "use_recenter": {
        "name": "重设轴心",
        "description": "切割后用 MEDIAN 将碎块原点移到几何中心，不移动几何本身；不是按体积计算的质量中心。",
    },
    "use_remove_original": {
        "name": "移除上级碎块",
        "description": "递归成功替换后删除生成的上级碎块；最初选中的源物体始终保留。",
    },
    "collection_name": {
        "name": "集合",
        "description": "输出集合名称；留空使用当前集合。同名集合必须可编辑且在当前视图层可见，只有不存在同名集合时才新建。全部碎块直接放入此集合或其中的分组集合。",
    },
    "shard_prefix": {
        "name": "碎块前缀",
        "description": "碎块名前缀：不分组时为 cell_001 这样的形式，分组时为 cell_L1G1_001；该前缀不会出现在集合名里。",
    },
    "use_shard_collections": {
        "name": "碎块分组",
        "description": "把每个被递归切开的碎块的子块放进各自独立的集合（名为 L<层>_G<组>，位于输出集合之内）；关闭时所有碎块直接平铺在输出集合里。",
    },
    "shard_index_padding": {
        "name": "序号位数",
        "description": "碎块序号的最小补零位数；超过 3 位只在停止阈值允许产生 999 个以上碎块时才看得出差别。不对层号和组号补零，也不保证全局按创建顺序排序。",
    },
    "use_debug_points": {
        "name": "显示种子点",
        "description": "创建 DebugPoints 网格显示采样种子，包含递归种子；它是诊断几何，不计为碎块。",
    },
    "use_debug_redraw": {
        "name": "实时显示进度",
        "description": "切割过程中刷新视口，可能降低大规模切割速度；后台模式下无效。",
    },
    "use_debug_bool": {
        "name": "保留布尔修改器",
        "description": "在凸胞体基础网格上保留启用的布尔修改器，求值几何仍显示求交结果；跳过递归、孤岛拆分和内部断面收尾。",
    },
}


ZH_HANS_UI = {
    "Quick Destruction Selected Mesh Objects": "快速破坏选中的网格物体",
    "Quick Destruction Effect": "快速破坏效果",
    "Cut selected editable meshes into shards; source objects are kept": "将选中的可编辑网格切成碎块，保留源物体",
    "QuickDestruction": "快速破坏",
    "Fracture": "切割",
    "Point Source": "种子点来源",
    "Recursive Shatter": "递归破碎",
    "Mesh Data": "网格数据",
    "Physics": "物理",
    "Object": "物体",
    "Debug": "调试",
    "Collection": "集合",
    # 面板上的两处补充说明: RNA 提示在这两个参数上取不到 (逐项/分量绘制),
    # 所以把最关键的前提直接画在分组里, 见 panel._draw_group_notes()
    "Seeds are existing geometry positions, not random points": "种子取自现成的几何位置, 不是随机撒点",
    "Both seed limits act only below the parent shard vertex count; larger values change nothing": "两个种子上限只在小于父碎块顶点数时起作用, 再大也不会有变化",
    "Reserved for Rigid Body; this tool never creates one": "为刚体预留; 本工具不创建刚体",
    "Presets": "预设",
    "Preset": "预设",
    "Name": "名称",
    "Name of the preset": "预设名称",
    "Overwrite": "覆盖",
    "Replace the existing preset with this name": "替换同名的现有预设",
    "Save Preset": "保存预设",
    "Save Preset...": "保存预设…",
    "Load Preset": "加载预设",
    "Remove Preset": "删除预设",
    "Open Preset Directory": "打开预设目录",
    "No presets yet": "尚无预设",
    "Save the parameters currently shown in this panel or dialog as a JSON preset": "把当前面板或对话框显示的参数保存为 JSON 预设",
    "Load a JSON preset into the parameters currently shown here": "把 JSON 预设加载到当前界面显示的参数中",
    "Permanently delete this JSON preset after confirmation": "确认后永久删除此 JSON 预设",
    "Open the folder containing the shared JSON presets": "打开存放共享 JSON 预设的文件夹",
    "The parameter dialog has closed": "参数对话框已关闭",
    "Cannot resolve the preset directory": "无法定位预设目录",
    "Preset name is empty": "预设名称为空",
    "This preset name is reserved by Windows": "此预设名称为 Windows 保留名称",
    "Preset name is too long": "预设名称过长",
    "Preset path is outside the preset directory": "预设路径超出预设目录",
    "Preset must be a JSON object": "预设内容必须是 JSON 对象",
    "Preset file is too large": "预设文件过大",
    "Preset operation failed: {detail}": "预设操作失败：{detail}",
    "Preset directory could not be read: {detail}": "无法读取预设目录：{detail}",
    "Preset already exists; enable Overwrite to replace it": "预设已存在，请勾选“覆盖”后替换",
    'Saved preset "{name}"': "已保存预设“{name}”",
    'Removed preset "{name}"': "已删除预设“{name}”",
    "Loaded {count} parameters": "已加载 {count} 项参数",
    "Created {count} shards in {seconds:.2f} s": "已生成 {count} 个碎块，耗时 {seconds:.2f} 秒",
    "Invalid parameter: {name}": "无效参数：{name}",
    "Fracture failed: {detail}": "切割失败：{detail}",
    "Shards were created, but sidebar settings could not be synchronized: {detail}": "碎块已生成，但侧栏设置同步失败：{detail}",
    "Select editable mesh objects in Object Mode": "请在物体模式下选择可编辑的网格物体",
    'Mesh "{name}" has no faces': "网格“{name}”没有面",
    'Material slot {index} does not exist on "{name}"': "物体“{name}”不存在索引为 {index} 的材质槽",
    "Output collection must be editable and visible in the current view layer": "输出集合必须可编辑，并在当前视图层可见",
    'No valid shards were produced for "{name}"': "物体“{name}”未生成有效碎块",
    "Source geometry contains non-finite coordinates": "源几何包含非有限坐标",
    'Boolean intersection for "{name}" contains non-finite coordinates': "物体“{name}”的布尔交集包含非有限坐标",
    'Boolean intersection extends outside "{name}"; check for open or invalid source geometry': "布尔交集超出物体“{name}”的范围；请检查源网格的开口或无效几何",
}


# 新语言新增独立数据表后加入此目录 - 根装配负责语言别名与注册
CATALOGS = {
    "zh_HANS": merge_messages(
        property_messages(PROPERTY_SPECS, ZH_HANS_PROPERTIES),
        ZH_HANS_UI,
    ),
}
