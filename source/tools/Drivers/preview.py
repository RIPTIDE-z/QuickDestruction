"""计划初始条件预览 - 在视口里给每块会被起动的碎块画一根箭头, 弹道另画路径与撞击点

箭头的方向与长度是交接时给出的速度, 颜色是起动时刻, 不是碰撞之后的轨迹
计算结果按输入缓存: 驱动, 选中的资产, 物理修订号, 预览设置都没变时, 重绘直接用缓存
绘制回调里只读数据, 不写任何 ID; 计算出错时记下原因, 不让异常进入绘制回调
"""

import math
import traceback
from itertools import pairwise

import bpy
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Quaternion, Vector

from ...common import asset, ballistics, drivers, impulse, shards
from ...common.errors import DomainError
from ...common.status import drivers_signature
from ...framework.ui import selected_assets
from .api import compile_assets
from .constants import TOOL_ID

# 箭头头部两条短线占箭杆长度的比例与张角
HEAD_RATIO = 0.25
HEAD_ANGLE = math.radians(25.0)
# 撞击点十字标记的半长, 世界单位
MARK_SIZE = 0.1
# 颜色: 最早起动的蓝, 最晚起动的红, 不按时间着色时的橙, 弹道路径的青
EARLY = (0.2, 0.5, 1.0, 1.0)
LATE = (1.0, 0.25, 0.2, 1.0)
PLAIN = (1.0, 0.6, 0.1, 1.0)
PATH = (0.2, 0.9, 0.9, 1.0)
ENTRY = (0.2, 1.0, 0.3, 1.0)
EXIT = (1.0, 0.2, 0.2, 1.0)
# 线宽, 像素
LINE_WIDTH = 2.0

# 预览的运行状态: 绘制句柄, 缓存键, 缓存的线段, 缓存的批次, 最近一次计算出错的原因
STATE = {"handle": None, "key": None, "lines": [], "batch": None, "error": ""}


def _blend(fraction):
    """从最早到最晚的渐变色"""
    return tuple(a + (b - a) * fraction for a, b in zip(EARLY, LATE, strict=True))


def _arrow(start, tip, color):
    """一根箭头的线段: 箭杆与头部两条短线"""
    shaft = tip - start
    if shaft.length <= impulse.TINY:
        return []
    direction = shaft.normalized()
    helper = Vector((0.0, 0.0, 1.0)) if abs(direction.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    side = direction.cross(helper).normalized()
    back = -direction * (shaft.length * HEAD_RATIO)
    lines = [(start, tip, color)]
    for sign in (1.0, -1.0):
        wing = back.copy()
        wing.rotate(Quaternion(side, sign * HEAD_ANGLE))
        lines.append((tip, tip + wing, color))
    return lines


def _cross(point, color):
    """撞击点的三轴十字标记"""
    lines = []
    for axis in (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))):
        lines.append((point - axis * MARK_SIZE, point + axis * MARK_SIZE, color))
    return lines


def _velocity_lines(items, scale, by_time):
    """每块被起动的碎块一根箭头, 起点是质心, 指向第一次起动的速度"""
    firsts = []
    for item in items:
        for info in item.infos:
            events = item.plan.shards[info.key].events
            if events:
                firsts.append((info.center, events[0]))
    if not firsts:
        return []
    frames = [event.frame for _center, event in firsts]
    low, span = min(frames), max(1, max(frames) - min(frames))
    lines = []
    for center, event in firsts:
        color = _blend((event.frame - low) / span) if by_time else PLAIN
        start = Vector(center)
        lines += _arrow(start, start + Vector(event.velocity) * scale, color)
    return lines


def _path_lines(snapshots, items):
    """弹道的路径 (到子弹最远飞到的位置) 与入口, 出口标记"""
    lines = []
    for driver in snapshots:
        if driver.role != drivers.PROJECTILE or len(driver.path) < 2:
            continue
        flights = [item.flights[driver.name] for item in items if driver.name in item.flights]
        lengths = ballistics.path_lengths(driver.path)
        end = max(
            (lengths[-1] if flight.stop_arc is None else flight.stop_arc for flight in flights),
            default=lengths[-1],
        )
        points = [
            Vector(point) for point, arc in zip(driver.path, lengths, strict=True) if arc < end
        ]
        points.append(Vector(ballistics.point_at(driver.path, lengths, end)[0]))
        lines += [(a, b, PATH) for a, b in pairwise(points)]
        for flight in flights:
            for strike in flight.strikes:
                if strike.kind == impulse.STRIKE_ENTRY:
                    lines += _cross(Vector(strike.start), ENTRY)
                elif strike.kind == impulse.STRIKE_EXIT:
                    lines += _cross(Vector(strike.start), EXIT)
    return lines


def preview_lines(context, roots, scale, by_time):
    """预览要画的线段 [(起点, 终点, 颜色)]; 与模拟烘焙编译同一个函数, 所以看到的就是实际使用的"""
    items = compile_assets(context, roots, refresh=False)
    snapshots = [drivers.snapshot(obj) for obj in drivers.driver_objects(context.scene)]
    return _velocity_lines(items, scale, by_time) + _path_lines(snapshots, items)


def _roots(context):
    """预览的资产: 选中的资产; 没有选中时是场景里全部应用过物理的资产"""
    roots, _reason = selected_assets(context)
    if roots:
        return roots
    found = []
    for group in asset.roots_by_id().values():
        if len(group) == 1 and asset.physics_record(group[0]) is not None:
            found.append(group[0])
    return sorted(found, key=lambda root: root.name)


def _key(context, roots, settings):
    """缓存键: 驱动摘要, 各资产的标识与物理修订号, 预览设置"""
    revisions = tuple(
        (shards.asset_of(root), (asset.physics_record(root) or {}).get("rev")) for root in roots
    )
    return (
        drivers_signature(context.scene),
        revisions,
        round(settings.preview_scale, 6),
        settings.color_by_time,
    )


def _refresh(context, settings):
    """输入变了就重新计算线段与批次; 返回当前批次, 没有可画的内容时为 None"""
    roots = [root for root in _roots(context) if asset.physics_record(root) is not None]
    key = _key(context, roots, settings)
    if STATE["key"] == key:
        return STATE["batch"]
    STATE["key"] = key
    STATE["batch"] = None
    STATE["lines"] = []
    if not roots:
        return None
    try:
        STATE["lines"] = preview_lines(
            context, roots, settings.preview_scale, settings.color_by_time
        )
    except DomainError:
        # 资产的输入不满足 (例如弹道的源物体已删除): 预览什么也不画, 报错由烘焙时给出
        return None
    if STATE["lines"]:
        coords = [tuple(point) for start, end, _color in STATE["lines"] for point in (start, end)]
        colors = [color for _start, _end, color in STATE["lines"] for _ in (0, 1)]
        shader = gpu.shader.from_builtin("FLAT_COLOR")
        STATE["batch"] = batch_for_shader(shader, "LINES", {"pos": coords, "color": colors})
    return STATE["batch"]


def _draw():
    """视口绘制回调: 预览开着时画缓存的批次; 任何异常都记下来而不抛出"""
    context = bpy.context
    settings = getattr(getattr(context.scene, "qd_settings", None), TOOL_ID, None)
    if settings is None or not settings.show_preview:
        return
    try:
        batch = _refresh(context, settings)
        if batch is None:
            return
        shader = gpu.shader.from_builtin("FLAT_COLOR")
        gpu.state.line_width_set(LINE_WIDTH)
        gpu.state.blend_set("ALPHA")
        shader.bind()
        batch.draw(shader)
        gpu.state.line_width_set(1.0)
        gpu.state.blend_set("NONE")
        STATE["error"] = ""
    except Exception:
        STATE["error"] = traceback.format_exc()


def start():
    """注册视口绘制回调"""
    STATE["handle"] = bpy.types.SpaceView3D.draw_handler_add(_draw, (), "WINDOW", "POST_VIEW")


def stop():
    """移除绘制回调并清空缓存"""
    handle = STATE["handle"]
    if handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handle, "WINDOW")
    STATE.update({"handle": None, "key": None, "lines": [], "batch": None, "error": ""})
