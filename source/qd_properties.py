"""算子与属性组共用的 RNA 定义, 以及参数的中英对照文案。"""

# 自定义翻译上下文: 核心中文词典优先级更高, 用它可避免参数名被核心词表抢占语义
CONTEXT = "QuickDestruction"

# 正下限与 RNA、预设校验和胞体计算共用，避免界面能输入执行时必定拒绝的零。
CELL_SCALE_MIN = 0.001

# 参数的唯一来源: 算子与 PropertyGroup 都从这里生成属性, 避免两处接口分叉。
# 键是参数标识, 值前半是 bpy.props 里的属性类型名, 后半是传给它的关键字参数。
PROPERTY_SPECS = {'source': ('EnumProperty',
            {'name': 'Source',
             'items': (('VERT_OWN',
                        'Own Verts',
                        'Use original source mesh vertices in world space, before modifiers.'),
                       ('VERT_CHILD',
                        'Child Verts',
                        'Use vertices of direct child objects; mesh children use original vertices and '
                        'convertible children use evaluated geometry.'),
                       ('PARTICLE_OWN',
                        'Own Particles',
                        'Use particle positions from all particle systems on the source object; '
                        'particle child instances are not included.'),
                       ('PARTICLE_CHILD',
                        'Child Particles',
                        'Use particle systems on direct child objects, not child particles of the '
                        'source particle system.'),
                       ('PENCIL',
                        'Annotation Pencil',
                        'Use points from the active annotation layer and frame, not Grease Pencil '
                        'object geometry.')),
             'options': {'ENUM_FLAG'},
             'default': {'PARTICLE_OWN'},
             'description': 'Combine selected seed sources. Child means direct child objects. If no '
                            'points are found, use the source mesh vertices.'}),
 'source_limit': ('IntProperty',
                  {'name': 'Seed Limit',
                   'description': 'Maximum sampled seed points before deduplication; 0 keeps all '
                                  'points. Does not add points or guarantee a shard count.',
                   'min': 0,
                   'max': 5000,
                   'default': 100}),
 'source_noise': ('FloatProperty',
                  {'name': 'Noise',
                   'description': 'Random seed displacement, from 0 to this fraction of half the '
                                  'world-space bounding-box diagonal. 0 disables displacement.',
                   'min': 0.0,
                   'max': 1.0,
                   'default': 0.5,
                   'precision': 3}),
 'cell_scale': ('FloatVectorProperty',
                {'name': 'Scale',
                 'description': 'Axis weights from 0.001 to 1 for cell dividing planes; changes their direction and distance, not object scale. Equal weights give the unscaled partition.',
                 'size': 3,
                 'min': CELL_SCALE_MIN,
                 'max': 1.0,
                 'default': (1.0, 1.0, 1.0)}),
 'recursion': ('IntProperty',
               {'name': 'Recursion',
                'description': 'Additional shatter rounds after the first cut. 0 disables recursion; '
                               'growth also depends on the fraction and stopping threshold.',
                'min': 0,
                'max': 5000,
                'default': 1}),
 'recursion_source_limit': ('IntProperty',
                            {'name': 'Recursion Seed Limit',
                             'description': 'Maximum seed points sampled for each recursively cut '
                                            'shard, before deduplication; 0 keeps all available points.',
                             'min': 0,
                             'max': 5000,
                             'default': 8}),
 'recursion_clamp': ('IntProperty',
                     {'name': 'Shard Stop Threshold',
                      'description': 'Stop further recursion when the current shard count per source '
                                     'object reaches this threshold. One cut or island split may exceed '
                                     'it. 0 disables the threshold; the first cut is not limited.',
                      'min': 0,
                      'max': 10000,
                      'default': 250}),
 'recursion_chance': ('FloatProperty',
                      {'name': 'Recursion Fraction',
                       'description': 'Fraction of current shards selected each round, rounded down. 0 '
                                      'selects none; 1 selects all. The selection mode chooses which '
                                      'shards.',
                       'min': 0.0,
                       'max': 1.0,
                       'default': 1.0}),
 'recursion_chance_select': ('EnumProperty',
                             {'name': 'Recurse Over',
                              'items': (('RANDOM',
                                         'Random',
                                         'Randomly choose the required fraction of shards.'),
                                        ('SIZE_MIN',
                                         'Small Shards',
                                         'Choose shards with smaller world-space bounding-box diagonals '
                                         'first.'),
                                        ('SIZE_MAX',
                                         'Big Shards',
                                         'Choose shards with larger world-space bounding-box diagonals '
                                         'first.'),
                                        ('CURSOR_MIN',
                                         'Cursor Close',
                                         'Choose shard origins closest to the 3D cursor first.'),
                                        ('CURSOR_MAX',
                                         'Cursor Far',
                                         'Choose shard origins farthest from the 3D cursor first.')),
                              'default': 'RANDOM',
                              'description': 'Choose which shards participate when the recursion '
                                             'fraction is below 1. Size uses the world-space '
                                             'bounding-box diagonal; cursor distance uses object '
                                             'origins.'}),
 'use_smooth_faces': ('BoolProperty',
                      {'name': 'Smooth Interior',
                       'description': 'Smooth-shade newly created interior faces; exterior shading '
                                      'comes from the source mesh.',
                       'default': False}),
 'use_sharp_edges': ('BoolProperty',
                     {'name': 'Sharp Edges',
                      'description': 'Mark edges between interior and exterior faces as sharp, '
                                     'independently of smooth interior shading.',
                      'default': True}),
 'use_sharp_edges_apply': ('BoolProperty',
                           {'name': 'Apply Split Edge',
                            'description': 'Physically split all sharp edges, including inherited ones, when Sharp Edges is enabled. Duplicates vertices and can make closed shards non-watertight.',
                            'default': True}),
 'use_data_match': ('BoolProperty',
                    {'name': 'Match Data',
                     'description': 'Copy material slots and create matching UV/color layers. Boolean '
                                    'interpolation transfers supported exterior data; interior UVs are '
                                    'not unwrapped.',
                     'default': True}),
 'use_island_split': ('BoolProperty',
                      {'name': 'Split Islands',
                       'description': 'Separate disconnected parts of each Boolean result into objects. '
                                      'This can produce more shards than seed points.',
                       'default': True}),
 'margin': ('FloatProperty',
            {'name': 'Margin',
             'description': 'Inset each seed-dividing plane by this world-space length in Blender '
                            'units. Opposing faces have about twice this gap; large values can erase '
                            'cells.',
             'min': 0.0,
             'max': 1.0,
             'default': 0.001,
             'precision': 4,
             'subtype': 'DISTANCE',
             'unit': 'LENGTH'}),
 'material_index': ('IntProperty',
                    {'name': 'Material',
                     'description': 'Zero-based material slot index for interior faces; 0 selects the '
                                    'first slot. Requires matching data and an existing slot for a '
                                    'visible material.',
                     'default': 0,
                     'min': 0}),
 'use_interior_vgroup': ('BoolProperty',
                         {'name': 'Interior VGroup',
                          'description': 'Create the vertex group 内部 for interior-only vertices before splitting sharp edges. Boundary vertices shared with exterior faces are excluded; split copies are not reclassified.',
                          'default': False}),
 'mass_mode': ('EnumProperty',
               {'name': 'Mass Mode',
                'items': (('VOLUME',
                           'By Volume',
                           'Inactive legacy option: proportional mass distribution by bounding-box volume is not performed.'),
                          ('UNIFORM',
                           'Uniform',
                           'Inactive legacy option: uniform mass assignment is not performed.')),
                'default': 'VOLUME',
                'description': 'Compatibility placeholder for old presets. The fracture pipeline does not use this field or create or modify rigid bodies; no mass is assigned.'}),
 'mass': ('FloatProperty',
          {'name': 'Mass',
           'description': 'Compatibility placeholder in kilograms, kept for old presets. The fracture pipeline does not assign mass or create rigid bodies.',
           'min': 0.001,
           'max': 1000.0,
           'default': 1.0}),
 'use_recenter': ('BoolProperty',
                  {'name': 'Recenter',
                   'description': 'Move each shard origin to its geometry center using MEDIAN, without moving its geometry. This is not a volume-based center of mass.',
                   'default': True}),
 'use_remove_original': ('BoolProperty',
                         {'name': 'Remove Parent Shards',
                          'description': 'Delete generated parent shards after successful recursive '
                                         'replacement. The originally selected source objects are '
                                         'always kept.',
                          'default': True}),
 'collection_name': ('StringProperty',
                     {'name': 'Collection',
                      'description': 'Output collection name. Empty uses the current collection. A named collection must be editable and visible in this view layer; create it only if no collection has that name. All shards are placed here or in its group collections.',
                      'default': 'cell_collection'}),
 'shard_prefix': ('StringProperty',
                  {'name': 'Shard Prefix',
                   'description': 'Prefix used for shard names, for example cell_001 without grouping '
                                  'or cell_L1G1_001 with grouping. It is never used for collection '
                                  'names.',
                   'default': 'cell'}),
 'use_shard_collections': ('BoolProperty',
                           {'name': 'Group Shards',
                            'description': 'Put the shards of each recursively cut shard into its own '
                                           'collection named L<level>_G<group>, inside the output '
                                           'collection. When off, every shard is linked directly into '
                                           'the output collection.',
                            'default': True}),
 'shard_index_padding': ('IntProperty',
                         {'name': 'Index Digits',
                          'description': 'Minimum digit width of the shard sequence number, padded with zeros. Does not pad level/group numbers or guarantee global creation-order sorting.',
                          'min': 0,
                          'max': 8,
                          'default': 3}),
 'use_debug_points': ('BoolProperty',
                      {'name': 'Seed Points',
                       'description': 'Create a DebugPoints mesh showing sampled seeds, including '
                                      'recursive seeds. It is diagnostic geometry, not a shard.',
                       'default': False}),
 'use_debug_redraw': ('BoolProperty',
                      {'name': 'Show Progress Realtime',
                       'description': 'Refresh the viewport during cutting. Can slow large cuts; has no '
                                      'effect in background mode.',
                       'default': True}),
 'use_debug_bool': ('BoolProperty',
                    {'name': 'Unapplied Boolean',
                     'description': 'Keep enabled Boolean modifiers on convex base meshes; evaluated geometry still shows the intersection. Skip recursion, island splitting and interior finishing.',
                     'default': False})}

# 英文原文到中文译文的对照表, 键必须与上面定义里的原文逐字一致
PROPERTY_MESSAGES = {'Combine selected seed sources. Child means direct child objects. If no points are found, use the source mesh vertices.': '合并所选来源的种子点；子物体仅指直接子物体。未找到点时回退到源网格自身顶点。',
 'Source': '来源',
 'Own Verts': '自身顶点',
 'Use original source mesh vertices in world space, before modifiers.': '使用源网格修改器之前的原始顶点，转换为世界空间坐标。',
 'Child Verts': '子物体顶点',
 'Use vertices of direct child objects; mesh children use original vertices and convertible children use evaluated geometry.': '使用直接子物体顶点；网格子物体使用原始顶点，可转换的其他子物体使用求值几何。',
 'Own Particles': '自身粒子',
 'Use particle positions from all particle systems on the source object; particle child instances are not included.': '使用源物体全部粒子系统的粒子位置，不包含粒子系统的子粒子实例。',
 'Child Particles': '子物体粒子',
 'Use particle systems on direct child objects, not child particles of the source particle system.': '使用直接子物体上的粒子系统，不是源粒子系统中的子粒子。',
 'Annotation Pencil': '注解铅笔',
 'Use points from the active annotation layer and frame, not Grease Pencil object geometry.': '使用活动注解层和活动帧的笔画点，不读取蜡笔物体几何。',
 'Maximum sampled seed points before deduplication; 0 keeps all points. Does not add points or guarantee a shard count.': '去重前最多保留的种子点数，0 '
                                                                                                                          '表示全部保留。不会补充种子点，也不保证最终碎块数量。',
 'Random seed displacement, from 0 to this fraction of half the world-space bounding-box diagonal. 0 disables displacement.': '种子点随机位移，最大距离为世界空间包围盒对角线一半乘以此值；0 '
                                                                                                                              '表示不扰动。',
 'Noise': '噪波',
 'Axis weights from 0.001 to 1 for cell dividing planes; changes their direction and distance, not object scale. Equal weights give the unscaled partition.': '胞体分割平面的轴向权重，各轴为 0.001–1，改变平面方向和距离，不缩放物体；三轴权重相等时与未缩放分区相同。',
 'Scale': '胞体缩放',
 'Additional shatter rounds after the first cut. 0 disables recursion; growth also depends on the fraction and stopping threshold.': '首次切割后的额外细分轮数；0 '
                                                                                                                                     '表示不递归。增长量还受参与比例与停止阈值控制。',
 'Recursion': '递归',
 'Maximum seed points sampled for each recursively cut shard, before deduplication; 0 keeps all available points.': '每个递归碎块在去重前最多采样的种子点数；0 '
                                                                                                                    '表示保留全部可用点。',
 'Stop further recursion when the current shard count per source object reaches this threshold. One cut or island split may exceed it. 0 disables the threshold; the first cut is not limited.': '每个源物体的当前碎块数达到此阈值后停止继续递归；单次细分或孤岛拆分可能超出阈值。0 '
                                                                                                                                                                                                 '表示关闭阈值，首次切割不受此项限制。',
 'Shard Stop Threshold': '碎块停止阈值',
 'Fraction of current shards selected each round, rounded down. 0 selects none; 1 selects all. The selection mode chooses which shards.': '每轮选中当前碎块的比例，数量向下取整；0 '
                                                                                                                                          '不选，1 '
                                                                                                                                          '全选。具体碎块由递归选择方式决定。',
 'Recursion Fraction': '递归参与比例',
 'Choose which shards participate when the recursion fraction is below 1. Size uses the world-space bounding-box diagonal; cursor distance uses object origins.': '递归比例小于 '
                                                                                                                                                                  '1 '
                                                                                                                                                                  '时决定选哪些碎块；大小按世界空间包围盒对角线衡量，游标距离按物体原点计算。',
 'Recurse Over': '递归对象',
 'Random': '随机',
 'Randomly choose the required fraction of shards.': '随机选取指定比例的碎块。',
 'Small Shards': '小碎块',
 'Choose shards with smaller world-space bounding-box diagonals first.': '优先选择世界空间包围盒对角线较小的碎块。',
 'Big Shards': '大碎块',
 'Choose shards with larger world-space bounding-box diagonals first.': '优先选择世界空间包围盒对角线较大的碎块。',
 'Cursor Close': '靠近游标',
 'Choose shard origins closest to the 3D cursor first.': '优先选择原点更靠近 3D 游标的碎块。',
 'Cursor Far': '远离游标',
 'Choose shard origins farthest from the 3D cursor first.': '优先选择原点更远离 3D 游标的碎块。',
 'Smooth-shade newly created interior faces; exterior shading comes from the source mesh.': '对新生成的内部断面使用平滑着色，外表面着色由源网格继承。',
 'Smooth Interior': '平滑内侧面',
 'Mark edges between interior and exterior faces as sharp, independently of smooth interior shading.': '把内部断面与外表面交界处的边标记为锐边，与内部平滑着色开关独立。',
 'Sharp Edges': '锐边',
 'Physically split all sharp edges, including inherited ones, when Sharp Edges is enabled. Duplicates vertices and can make closed shards non-watertight.': '启用锐边时实际拆开所有锐边，包括继承的锐边；会复制顶点，可能让原本封闭的碎块变为不水密网格。',
 'Apply Split Edge': '应用拆分边',
 'Copy material slots and create matching UV/color layers. Boolean interpolation transfers supported exterior data; interior UVs are not unwrapped.': '复制材质槽并创建对应的 '
                                                                                                                                                      'UV/颜色层；布尔插值传递受支持的外表面数据，内部断面不会自动展开 '
                                                                                                                                                      'UV。',
 'Match Data': '匹配数据',
 'Separate disconnected parts of each Boolean result into objects. This can produce more shards than seed points.': '将每个布尔结果的不连通部分拆成独立物体，可能使碎块数量多于种子点数量。',
 'Split Islands': '拆分孤岛',
 'Inset each seed-dividing plane by this world-space length in Blender units. Opposing faces have about twice this gap; large values can erase cells.': '每个种子分割平面向内偏移的世界空间长度，单位为 '
                                                                                                                                                        'Blender '
                                                                                                                                                        '单位；相对两面的间隙约为此值两倍，过大会使胞体消失。',
 'Margin': '内缩量',
 'Zero-based material slot index for interior faces; 0 selects the first slot. Requires matching data and an existing slot for a visible material.': '内部断面使用的材质槽索引，从 '
                                                                                                                                                     '0 '
                                                                                                                                                     '开始；0 '
                                                                                                                                                     '为第一个槽。需要启用匹配数据且存在对应材质槽才能显示材质。',
 'Material': '材质索引',
 'Create the vertex group 内部 for interior-only vertices before splitting sharp edges. Boundary vertices shared with exterior faces are excluded; split copies are not reclassified.': '在拆开锐边之前，为仅属于内部断面的顶点创建“内部”顶点组；排除与外表面共享的边界顶点，拆边副本不重新分类。',
 'Interior VGroup': '内部顶点组',
 'Compatibility placeholder for old presets. The fracture pipeline does not use this field or create or modify rigid bodies; no mass is assigned.': '兼容旧预设的占位参数；切割流程不使用它，也不创建或修改刚体，不分配碎块质量。',
 'Mass Mode': '质量模式',
 'By Volume': '按体积比例',
 'Inactive legacy option: proportional mass distribution by bounding-box volume is not performed.': '无效的旧版选项：当前不按包围盒体积分配质量。',
 'Uniform': '统一质量',
 'Inactive legacy option: uniform mass assignment is not performed.': '无效的旧版选项：当前不执行统一质量赋值。',
 'Compatibility placeholder in kilograms, kept for old presets. The fracture pipeline does not assign mass or create rigid bodies.': '兼容旧预设的占位参数，单位千克；切割流程不分配质量，也不创建刚体。',
 'Mass': '质量',
 'Move each shard origin to its geometry center using MEDIAN, without moving its geometry. This is not a volume-based center of mass.': '切割后用 MEDIAN 将碎块原点移到几何中心，不移动几何本身；不是按体积计算的质量中心。',
 'Recenter': '重设轴心',
 'Delete generated parent shards after successful recursive replacement. The originally selected source objects are always kept.': '递归成功替换后删除生成的上级碎块；最初选中的源物体始终保留。',
 'Remove Parent Shards': '移除上级碎块',
 'Output collection name. Empty uses the current collection. A named collection must be editable and visible in this view layer; create it only if no collection has that name. All shards are placed here or in its group collections.': '输出集合名称；留空使用当前集合。同名集合必须可编辑且在当前视图层可见，只有不存在同名集合时才新建。全部碎块直接放入此集合或其中的分组集合。',
 'Prefix used for shard names, for example cell_001 without grouping or cell_L1G1_001 with grouping. It is never used for collection names.': '碎块名前缀：不分组时为 cell_001 这样的形式，分组时为 cell_L1G1_001；该前缀不会出现在集合名里。',
 'Shard Prefix': '碎块前缀',
 'Put the shards of each recursively cut shard into its own collection named L<level>_G<group>, inside the output collection. When off, every shard is linked directly into the output collection.': '把每个被递归切开的碎块的子块放进各自独立的集合（名为 L<层>_G<组>，位于输出集合之内）；关闭时所有碎块直接平铺在输出集合里。',
 'Group Shards': '碎块分组',
 'Minimum digit width of the shard sequence number, padded with zeros. Does not pad level/group numbers or guarantee global creation-order sorting.': '碎块序号的最小补零位数；不对层号和组号补零，也不保证全局按创建顺序排序。',
 'Index Digits': '序号位数',
 'Collection': '集合',
 'Create a DebugPoints mesh showing sampled seeds, including recursive seeds. It is diagnostic geometry, not a shard.': '创建 '
                                                                                                                        'DebugPoints '
                                                                                                                        '网格显示采样种子，包含递归种子；它是诊断几何，不计为碎块。',
 'Refresh the viewport during cutting. Can slow large cuts; has no effect in background mode.': '切割过程中刷新视口，可能降低大规模切割速度；后台模式下无效。',
 'Show Progress Realtime': '实时显示进度',
 'Keep enabled Boolean modifiers on convex base meshes; evaluated geometry still shows the intersection. Skip recursion, island splitting and interior finishing.': '在凸胞体基础网格上保留启用的布尔修改器，求值几何仍显示求交结果；跳过递归、孤岛拆分和内部断面收尾。',
 'Seed Limit': '种子点上限',
 'Recursion Seed Limit': '递归种子上限',
 'Seed Points': '显示种子点',
 'Unapplied Boolean': '保留布尔修改器'}


def property_definitions():
    """生成 RNA 属性；上下文用于标签，数据名的值不经过翻译函数。"""
    from bpy import props
    definitions = {}
    for key, (kind, options) in PROPERTY_SPECS.items():
        definitions[key] = getattr(props, kind)(translation_context=CONTEXT, **options)
    return definitions
