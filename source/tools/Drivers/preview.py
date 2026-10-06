"""计划初始条件预览 - 在视口里给每块会被起动的碎块画一根箭头, 弹道另画路径与撞击点

箭头的方向与长度是交接时给出的速度, 颜色是起动时刻, 不是碰撞之后的轨迹
计算结果按输入缓存: 驱动, 选中的资产, 物理修订号, 时间换算, 预览设置都没变时, 重绘直接用缓存
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
from .api import compile_assets, plan_timing
from .constants import TOOL_ID

# 箭头头部两条短线占箭杆长度的比例
HEAD_RATIO = 0.25
# 箭头头部两条短线与箭杆的张角, 弧度
HEAD_ANGLE = math.radians(25.0)
# 撞击点十字标记的半长, 世界单位
MARK_SIZE = 0.1
# 颜色: 最早起动的蓝
EARLY = (0.2, 0.5, 1.0, 1.0)
# 颜色: 最晚起动的红
LATE = (1.0, 0.25, 0.2, 1.0)
# 颜色: 不按时间着色时的橙
PLAIN = (1.0, 0.6, 0.1, 1.0)
# 颜色: 弹道路径的青
PATH = (0.2, 0.9, 0.9, 1.0)
# 颜色: 弹道入口标记的绿
ENTRY = (0.2, 1.0, 0.3, 1.0)
# 颜色: 弹道出口标记的红
EXIT = (1.0, 0.2, 0.2, 1.0)
# 线宽, 像素
LINE_WIDTH = 2.0

# 预览的运行状态: 绘制句柄, 缓存键, 缓存的线段, 缓存的批次, 最近一次计算出错的原因
STATE = {"handle": None, "key": None, "lines": [], "batch": None, "error": ""}


def _blend(fraction):
    """从最早到最晚的渐变色

    fraction 起动时刻在最早与最晚之间的位置, 0 到 1
    返回 RGBA 元组
    """
    return tuple(a + (b - a) * fraction for a, b in zip(EARLY, LATE, strict=True))


def _arrow(start, tip, color):
    """一根箭头的线段: 箭杆与头部两条短线

    start 箭杆起点
    tip 箭头尖端
    color 颜色
    返回 [(起点, 终点, 颜色)]; 箭杆长度为零时为空
    """
    shaft = tip - start
    if shaft.length <= impulse.TINY:
        return []
    direction = shaft.normalized()
    # 选一个与箭杆不太平行的辅助轴求侧向, 避免叉积接近零
    helper = Vector((0.0, 0.0, 1.0)) if abs(direction.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    side = direction.cross(helper).normalized()
    back = -direction * (shaft.length * HEAD_RATIO)
    lines = [(start, tip, color)]
    # 头部两条短线: 把回指向箭杆的向量绕侧向轴正反各转一个张角
    for sign in (1.0, -1.0):
        wing = back.copy()
        wing.rotate(Quaternion(side, sign * HEAD_ANGLE))
        lines.append((tip, tip + wing, color))
    return lines


def _cross(point, color):
    """撞击点的三轴十字标记

    point 撞击点
    color 颜色
    返回 [(起点, 终点, 颜色)]
    """
    lines = []
    for axis in (Vector((1, 0, 0)), Vector((0, 1, 0)), Vector((0, 0, 1))):
        lines.append((point - axis * MARK_SIZE, point + axis * MARK_SIZE, color))
    return lines


def _velocity_lines(items, scale, by_time):
    """每块被起动的碎块一根箭头, 起点是质心, 指向第一次起动的速度

    items AssetPlan 列表
    scale 箭头长度相对速度的比例
    by_time 为真时按起动时刻渐变着色
    返回 [(起点, 终点, 颜色)]
    """
    firsts = []
    for item in items:
        for info in item.infos:
            events = item.plan.shards[info.key].events
            # 没有起动事件的碎块不画箭头
            if events:
                firsts.append((info.center, events[0]))
    if not firsts:
        return []
    # 着色按起动帧在全部碎块里的相对位置; 跨度至少为 1 避免除零
    frames = [event.frame for _center, event in firsts]
    low, span = min(frames), max(1, max(frames) - min(frames))
    lines = []
    for center, event in firsts:
        color = _blend((event.frame - low) / span) if by_time else PLAIN
        start = Vector(center)
        lines += _arrow(start, start + Vector(event.velocity) * scale, color)
    return lines


def _path_lines(snapshots, items):
    """弹道的路径 (到子弹最远飞到的位置) 与入口, 出口标记

    snapshots 驱动快照列表
    items AssetPlan 列表, 提供各弹道的飞行计划
    返回 [(起点, 终点, 颜色)]
    """
    lines = []
    for driver in snapshots:
        # 只有至少两个点的弹道才有路径
        if driver.role != drivers.PROJECTILE or len(driver.path) < 2:
            continue
        flights = [item.flights[driver.name] for item in items if driver.name in item.flights]
        lengths = ballistics.path_lengths(driver.path)
        # 路径画到子弹在各资产里最远飞到的位置; 没有计划时画整条
        end = max(
            (lengths[-1] if flight.stop_arc is None else flight.stop_arc for flight in flights),
            default=lengths[-1],
        )
        points = [
            Vector(point) for point, arc in zip(driver.path, lengths, strict=True) if arc < end
        ]
        points.append(Vector(ballistics.point_at(driver.path, lengths, end)[0]))
        lines += [(a, b, PATH) for a, b in pairwise(points)]
        # 入口与出口各画一个十字, 通道是路径本身
        for flight in flights:
            for strike in flight.strikes:
                if strike.kind == impulse.STRIKE_ENTRY:
                    lines += _cross(Vector(strike.start), ENTRY)
                elif strike.kind == impulse.STRIKE_EXIT:
                    lines += _cross(Vector(strike.start), EXIT)
    return lines


def preview_lines(context, roots, scale, by_time):
    """预览要画的线段; 与模拟烘焙编译同一个函数, 所以看到的就是实际使用的

    context Blender 上下文
    roots 资产根集合序列
    scale 箭头长度相对速度的比例
    by_time 为真时按起动时刻渐变着色
    返回 [(起点, 终点, 颜色)]
    """
    # 绘制回调里不能刷新视图层, 所以 refresh=False
    items = compile_assets(context, roots, refresh=False)
    snapshots = [drivers.snapshot(obj) for obj in drivers.driver_objects(context.scene)]
    return _velocity_lines(items, scale, by_time) + _path_lines(snapshots, items)


def _roots(context):
    """预览的资产: 选中的资产; 没有选中时是场景里全部应用过物理的资产

    context Blender 上下文
    返回 按名字排序的根集合列表
    """
    roots, _reason = selected_assets(context)
    if roots:
        return roots
    found = []
    # 被复制的资产 (标识对应多个根集合) 不参与预览, 避免预览出歧义的内容
    for group in asset.roots_by_id().values():
        if len(group) == 1 and asset.physics_record(group[0]) is not None:
            found.append(group[0])
    return sorted(found, key=lambda root: root.name)


def _key(context, roots, settings):
    """缓存键: 驱动摘要, 各资产的标识与物理修订号, 时间换算, 预览设置

    context Blender 上下文
    roots 资产根集合列表
    settings 工具参数组
    返回 可比较的元组
    """
    revisions = tuple(
        (shards.asset_of(root), (asset.physics_record(root) or {}).get("rev")) for root in roots
    )
    # 时间换算决定起动帧的钳制与波前到达帧: 改模拟的帧范围或时间缩放时预览也要重算
    timing = plan_timing(context)
    return (
        drivers_signature(context.scene),
        revisions,
        (timing.frame_start, round(timing.dt, 9)),
        round(settings.preview_scale, 6),
        settings.color_by_time,
    )


def _refresh(context, settings):
    """输入变了就重新计算线段与批次

    context Blender 上下文
    settings 工具参数组
    返回 当前批次; 没有可画的内容时为 None
    """
    roots = [root for root in _roots(context) if asset.physics_record(root) is not None]
    key = _key(context, roots, settings)
    # 输入没变: 直接用缓存, 重绘不重复计算
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
        # 每条线段两个端点, 颜色按端点各存一份
        coords = [tuple(point) for start, end, _color in STATE["lines"] for point in (start, end)]
        colors = [color for _start, _end, color in STATE["lines"] for _ in (0, 1)]
        shader = gpu.shader.from_builtin("FLAT_COLOR")
        STATE["batch"] = batch_for_shader(shader, "LINES", {"pos": coords, "color": colors})
    return STATE["batch"]


def _draw():
    """视口绘制回调: 预览开着时画缓存的批次; 任何异常都记下来而不抛出"""
    context = bpy.context
    settings = getattr(getattr(context.scene, "qd_settings", None), TOOL_ID, None)
    # 预览没开启或参数组不存在 (插件注销中) 时什么也不画
    if settings is None or not settings.show_preview:
        return
    try:
        batch = _refresh(context, settings)
        if batch is None:
            return
        shader = gpu.shader.from_builtin("FLAT_COLOR")
        gpu.state.line_width_set(LINE_WIDTH)
        gpu.state.blend_set("ALPHA")
        try:
            shader.bind()
            batch.draw(shader)
        finally:
            # 绘制状态无论成败都还原, 不影响视口里其他内容
            gpu.state.line_width_set(1.0)
            gpu.state.blend_set("NONE")
        STATE["error"] = ""
    except Exception:
        # 异常不能进入绘制回调, 否则会反复刷屏; 记下原因供排查
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
