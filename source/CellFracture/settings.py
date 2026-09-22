"""预切割参数唯一来源与阶段转换接口"""
from ..common import schema, rna
from .constants import CONTEXT, CELL_SCALE_MIN, RANDOM_SEED_DEFAULT

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
 'random_seed': ('IntProperty',
                 {'name': 'Random Seed',
                  'description': 'Seed for sampling, noise, recursive selection and numerical jitter. '
                                 'The same inputs and seed reproduce the cut within the same Blender '
                                 'version. Change this value for a different pattern; 0 is a fixed seed.',
                  'min': 0,
                  'max': 2147483647,
                  'default': RANDOM_SEED_DEFAULT}),
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
                                            'shard, before deduplication; 0 keeps all available points. '
                                            'Has no further effect once it reaches the parent shard\'s '
                                            'vertex count.',
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
                          'description': 'Minimum digit width of the shard sequence number, padded with zeros. '
                                         'Beyond 3 digits this only shows when the stop threshold allows '
                                         'more than 999 shards. Does not pad level/group numbers or '
                                         'guarantee global creation-order sorting.',
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

def field_label(field):
    """从参数规格读取界面标签 - 未知字段原样返回"""
    return PROPERTY_SPECS.get(field, ('', {'name': field}))[1]['name']

def property_definitions():
    """为算子或持久化参数组生成一套新的属性定义"""
    return rna.property_definitions(PROPERTY_SPECS, CONTEXT)


def settings_to_dict(owner):
    """读取预切割字段并转换为可序列化字典"""
    return schema.settings_to_dict(owner, PROPERTY_SPECS)


def validated_values(data):
    """校验已提供的字段 - 缺字段保留由调用者决定"""
    return schema.validated_values(data, PROPERTY_SPECS)


def dict_to_settings(owner, data):
    """整体校验后写回字段 - 赋值失败恢复旧值"""
    return schema.dict_to_settings(owner, data, PROPERTY_SPECS)


def operator_keywords(owner):
    """生成可传入 Blender 算子的关键字参数"""
    return schema.operator_keywords(owner, PROPERTY_SPECS)
