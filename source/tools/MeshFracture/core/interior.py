"""断面收尾 - 全部切割完成后处理内部断面: 断面面属性, 断面顶点组, 锐边, 取消隐藏标记"""

from ....common import shards
from ....common.blender_context import mesh_edit
from ..constants import INTERIOR_VGROUP_NAME


def _mark_interior_group(cell, bm):
    """只属于内部断面的顶点计入断面顶点组, 与外表面共享的边界顶点不计入"""
    exterior = {vert for face in bm.faces if not face.hide for vert in face.verts}
    group = cell.vertex_groups.get(INTERIOR_VGROUP_NAME)
    if group is None:
        group = cell.vertex_groups.new(name=INTERIOR_VGROUP_NAME)
    deform = bm.verts.layers.deform.verify()
    for vert in bm.verts:
        if vert not in exterior:
            # 先建组再按组索引写权重, 否则会写进用户的其它组
            vert[deform][group.index] = 1.0


def _mark_sharp_edges(bm):
    """两侧隐藏状态不同的边就是内外交界边, 标记为锐边; 着色直接读取锐边, 不拆开几何"""
    for edge in bm.edges:
        if len({face.hide for face in edge.link_faces}) == 2:
            edge.smooth = False


def _mark_interior_faces(bm):
    """断面面属性: 面隐藏标记就是断面判定, 在取消隐藏之前把它存成布尔面属性, 之后仍能分出断面"""
    layer = bm.faces.layers.bool.get(shards.INTERIOR_ATTRIBUTE)
    if layer is None:
        layer = bm.faces.layers.bool.new(shards.INTERIOR_ATTRIBUTE)
    for face in bm.faces:
        face[layer] = face.hide


def finish_interior(cells, settings):
    """全部切割完成后统一处理内部断面: 断面面属性, 顶点组, 锐边, 最后取消全部隐藏"""
    for cell in cells:
        with mesh_edit(cell.data) as bm:
            _mark_interior_faces(bm)
            if settings.use_interior_vgroup:
                _mark_interior_group(cell, bm)
            if settings.use_sharp_edges:
                _mark_sharp_edges(bm)
            # 隐藏标记只用于内部判定, 收尾后全部取消
            for face in bm.faces:
                face.hide = False
            bm.to_mesh(cell.data)
