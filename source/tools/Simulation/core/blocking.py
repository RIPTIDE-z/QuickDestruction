"""受阻判定 - 起动时碎块会被不让路的物体挡住, 起动速度去掉朝向它的分量, 沿墙面的分量保留

交接的那一帧碎块是动画刚体, 动画刚体与被动刚体或另一块动画刚体之间没有碰撞响应, 会被动画径直推进去
交还模拟之后求解器按穿透深度把它们顶开, 修正速度约为 0.2 倍深度除以子步时长,
与质量无关, 与子步数成正比, 而且是一次性注入的动能:
表现为碎块向外飞一帧, 然后以更快的速度反向飞出
不让路的物体有三类: 冻结的碎块 (还没起动时也是动画刚体), 锚定的碎块, 环境里不带动画的被动刚体
休眠与自由的碎块是动态刚体, 被动画刚体推动时有正常的动量传递, 不在此列

判定方法: 把碎块按质心略微缩小, 沿这次起动的位姿变化分段采样, 每一段与近处不让路物体的网格求交
找到第一个相交的段后二分, 取最后一个不相交的位置; 相交处墙面的外法线给出滑动方向:
速度去掉朝向墙的分量 (同帧起动的墙取相对速度), 再重新判定
墙角处最多滑动几轮, 仍被挡住就按通行比例缩减
共面贴合的面缩小后留出间隙, 不会被当成穿透; 缩小量也是允许的最大穿透, 远小于会造成明显反弹的深度
紧贴的碎块稍一自转就会让角插进邻块, 若把它当作受阻按通行比例缩减, 平移速度会被一并压到接近零:
所以受阻时先看不带自转平移是否畅通, 畅通就只削减自转, 平移速度保持; 平移也受阻时去掉自转再按平移判定
同帧起动的墙同理: 判定里它们只按平移移动
否则互相紧贴又都在自转的碎块, 会因为转角扫进彼此而全部被压住
"""

from dataclasses import dataclass, field, replace
from math import ceil
from typing import NamedTuple

from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from ....common import impulse, shards
from ....common.blender_context import vertex_coordinates
from .keys import turned

# 判定时碎块按质心缩小到这个比例, 贴合的面因此留出间隙
SHRINK = 0.98
# 采样步长占碎块半径的比例; 步长小于半径, 薄壁才不会被整个跨过
STEP_RATIO = 0.25
# 起动位姿变化的比例取统一网格: 0 到 GRID 对应 0 到 1, 同帧起动的墙按同一格数移动, 求交树可以复用
GRID = 64
# 粗采样段数上限, 必须是 2 的幂且不大于 GRID / 2 ** REFINE; 大位移不会让判定耗时无限增长
MAX_STEPS = 16
# 找到相交段后的二分次数
REFINE = 3
# 滑动的轮数上限, 每轮去掉一面墙的法向分量; 墙角需要两三轮
SLIDE_ROUNDS = 3
# 朝向墙的速度分量小于它不算顶着墙
INWARD_EPSILON = 1e-4
# 同帧起动的碎块互相判定的轮数上限; 第一轮按前方优先的顺序已基本到位, 后面只处理互相顶着的环
SETTLE_ROUNDS = 3
# 判定结果的速度与自转变化小于它视为没有变化, 每模拟秒; 对应起动位移相差约 2 毫米
SETTLE_TOLERANCE = 0.05


class Contact(NamedTuple):
    """起动者与墙的接触"""

    # 墙面的世界外法线
    normal: Vector
    # 墙自己的速度; 留在原位的墙为零
    velocity: Vector


@dataclass(frozen=True)
class Geometry:
    """一个物体的局部几何"""

    # 局部顶点
    points: list
    # 每个面的顶点序号
    polygons: list
    # 每个面的局部外法线, 与 polygons 同序
    normals: list
    # 顶点到原点的最大距离
    radius: float


def _geometry(obj):
    """读取物体网格的局部顶点, 面与面法线

    obj 物体
    返回 Geometry
    """
    flat = vertex_coordinates(obj.data)
    points = [Vector(flat[index : index + 3]) for index in range(0, len(flat), 3)]
    polygons = [tuple(polygon.vertices) for polygon in obj.data.polygons]
    normals = [polygon.normal.copy() for polygon in obj.data.polygons]
    return Geometry(points, polygons, normals, max((point.length for point in points), default=0.0))


def _box(geometry, matrix):
    """求几何在 matrix 下的世界包围盒

    geometry 局部几何
    matrix 世界矩阵
    返回 (最小角, 最大角)
    """
    world = [matrix @ point for point in geometry.points]
    low = Vector(tuple(min(point[axis] for point in world) for axis in range(3)))
    high = Vector(tuple(max(point[axis] for point in world) for axis in range(3)))
    return low, high


def _pose(matrix, event, fraction, dt):
    """起动位姿变化的 fraction 倍之后的世界矩阵, 旋转绕原点, 与交接关键帧的写法一致

    matrix 起动时的世界矩阵
    event 起动事件
    fraction 位姿变化的比例, 0 到 1
    dt 一帧经过的模拟秒
    返回 世界矩阵
    """
    location, rotation, scale = matrix.decompose()
    moved = location + Vector(event.velocity) * (dt * fraction)
    return Matrix.LocRotScale(moved, turned(rotation, event.spin, dt * fraction), scale)


def _reach(geometry, scale, event, dt):
    """一次起动让碎块表面最多移动的距离: 平移加上最远点的转弧

    geometry 局部几何
    scale 物体缩放的最大分量
    event 起动事件
    dt 一帧经过的模拟秒
    返回 距离, 世界单位
    """
    return (
        Vector(event.velocity).length + Vector(event.spin).length * geometry.radius * scale
    ) * dt


def _same(first, second):
    """判断两个事件的速度与自转是否在容差内一致

    first 事件
    second 事件
    返回 一致为真
    """
    return all(
        abs(one - other) <= SETTLE_TOLERANCE
        for one, other in zip(
            (*first.velocity, *first.spin), (*second.velocity, *second.spin), strict=True
        )
    )


def _swept(matrix, geometry, event, dt):
    """起动者在这次起动里扫过的世界包围盒: 起点与终点的位置包围盒, 向四周放大碎块半径与转弧

    matrix 起动时的世界矩阵
    geometry 局部几何
    event 起动事件
    dt 一帧经过的模拟秒
    返回 (最小角, 最大角)
    """
    center = matrix.translation
    shift = Vector(event.velocity) * dt
    radius = geometry.radius * max(matrix.to_scale())
    margin = radius * (1.0 + Vector(event.spin).length * dt)
    end = center + shift
    low = Vector(tuple(min(center[axis], end[axis]) - margin for axis in range(3)))
    high = Vector(tuple(max(center[axis], end[axis]) + margin for axis in range(3)))
    return low, high


def _scaled(event, fraction):
    """速度与自转同比缩减后的事件

    event 起动事件
    fraction 通行比例, 不小于 1 时原样返回
    返回 事件
    """
    if fraction >= 1.0:
        return event
    return replace(
        event,
        velocity=tuple(value * fraction for value in event.velocity),
        spin=tuple(value * fraction for value in event.spin),
    )


def _spin_scaled(event, fraction):
    """只有自转同比缩减后的事件, 平移速度不变

    event 起动事件
    fraction 通行比例
    返回 事件
    """
    return replace(event, spin=tuple(value * fraction for value in event.spin))


def _translation_only(event):
    """去掉自转的事件: 同帧起动的墙在判定里只按平移移动, 它们的自转扫过的浅相交由求解器消化

    event 起动事件
    返回 自转为零的事件
    """
    return replace(event, spin=(0.0, 0.0, 0.0))


def _inward(event, contact):
    """起动的相对速度朝向墙的分量, 为负表示在顶着墙

    event 起动事件
    contact 与墙的接触
    返回 速度沿墙面法线的分量
    """
    return (Vector(event.velocity) - contact.velocity).dot(contact.normal)


def _slide(event, contact):
    """去掉相对速度里朝向墙的分量后的事件

    event 起动事件
    contact 与墙的接触
    返回 事件; 没有顶着墙时为 None
    """
    inward = _inward(event, contact)
    # 速度没有朝向墙, 滑动没有意义
    if inward >= -INWARD_EPSILON:
        return None
    return replace(event, velocity=tuple(Vector(event.velocity) - contact.normal * inward))


@dataclass
class Wall:
    """一个不让路的物体"""

    # 对应的参与碎块; 环境里的被动刚体为 None
    owner: object
    # 局部几何
    geometry: Geometry
    # 它的世界矩阵, 碎块取静止姿态
    matrix: Matrix
    # 它自己的第一次起动, 冻结的碎块才有; 到期之前它一直留在原位
    event: impulse.Event | None
    # 静止位置与自己起动终点的包围盒并集, 已放大转弧; 用基础事件算, 滑动与缩减只会让它变小
    box: tuple
    # 自己的第一次起动实际得到的事件, 滑动与缩减之后的结果; 判定期间逐轮更新
    final: impulse.Event | None = None
    # final 每变一次加一, 用作求交树缓存的键
    version: int = 0
    # 包围球半径, 原点在质心, 球心就是世界矩阵的位置
    radius: float = 0.0
    # 世界网格的求交树与世界矩阵缓存, 键为 (版本, 网格格数); 留在原位时键为 (0, 0)
    trees: dict = field(default_factory=dict)


class Walls:
    """会话里全部不让路的物体, 以及对起动的受阻判定"""

    def __init__(self, participants, scene, dt):
        """收集全部墙, 建空间索引并完成冻结碎块第一次起动的判定

        participants 参与者列表
        scene 场景, 提供环境刚体
        dt 一帧经过的模拟秒
        """
        # 一帧经过的模拟秒
        self.dt = dt
        # 因受阻而改变了速度的起动次数, 写进烘焙结果
        self.blocked = 0
        # 局部几何缓存: 物体名 -> Geometry
        self._cache = {}
        # 全部墙: 冻结与锚定的碎块, 加上环境里的被动刚体
        self._items = [self._wall(item, dt) for item in participants if self._is_wall(item)]
        self._items += [self._environment(obj) for obj in self._environment_bodies(scene)]
        # 有起动事件的碎块墙: 参与者标识 -> 墙, 起动者的判定结果据此取回
        self._owned = {id(wall.owner): wall for wall in self._items if wall.event is not None}
        self._index()
        self._settle()

    def _index(self):
        """碎块墙按包围盒中心建空间索引; 环境墙往往很大, 不进索引, 每次直接检查"""
        # 环境墙 (owner 为 None) 往往是大地面, 放进索引会让查询半径过大, 单独存放逐个检查
        self._large = [wall for wall in self._items if wall.owner is None]
        self._small = [wall for wall in self._items if wall.owner is not None]
        self._tree_index = KDTree(len(self._small))
        # 索引按中心查询, 查询半径要再加上最大的包围盒半对角线
        self._half = 0.0
        for number, wall in enumerate(self._small):
            low, high = wall.box
            self._tree_index.insert((low + high) / 2.0, number)
            self._half = max(self._half, (high - low).length / 2.0)
        self._tree_index.balance()

    @staticmethod
    def _is_wall(participant):
        """冻结与锚定的碎块不让路; 冻结的碎块只在它自己的起动帧之前如此

        participant 参与者
        返回 是墙为真
        """
        return participant.plan.mode in (impulse.FROZEN, impulse.ANCHORED)

    @staticmethod
    def _environment_bodies(scene):
        """找出刚体世界里不属于任何资产, 不带动画的被动刚体

        scene 场景
        返回 物体列表
        """
        world = scene.rigidbody_world
        if world is None or world.collection is None:
            return []
        # 条件依次是: 网格, 有刚体, 不属于资产, 被动, 启用, 不受动画控制
        return [
            obj
            for obj in world.collection.objects
            if obj.type == "MESH"
            and obj.rigid_body is not None
            and shards.asset_of(obj) is None
            and obj.rigid_body.type == "PASSIVE"
            and obj.rigid_body.enabled
            and not obj.rigid_body.kinematic
        ]

    def _geometry_of(self, obj):
        """物体的局部几何, 同一物体只读一次

        obj 物体
        返回 Geometry
        """
        if obj.name not in self._cache:
            self._cache[obj.name] = _geometry(obj)
        return self._cache[obj.name]

    def _wall(self, participant, dt):
        """碎块墙: 静止姿态, 第一次起动; 包围盒放大到它起动后能到达的范围

        participant 参与者
        dt 一帧经过的模拟秒
        返回 Wall
        """
        geometry = self._geometry_of(participant.shard)
        events = participant.plan.events
        # 只有冻结的碎块有"起动前不让路"的第一次事件; 锚定的碎块永远不动
        event = events[0] if participant.plan.mode == impulse.FROZEN and events else None
        return self._box_wall(participant, geometry, participant.rest, event, dt)

    def _environment(self, obj):
        """环境墙: 当前世界矩阵, 没有起动

        obj 环境里的被动刚体物体
        返回 Wall
        """
        geometry = self._geometry_of(obj)
        return self._box_wall(None, geometry, obj.matrix_world.copy(), None, self.dt)

    @staticmethod
    def _box_wall(owner, geometry, matrix, event, dt):
        """建一个墙; 有起动时包围盒覆盖静止位置到起动终点, 并放大最远点的转弧

        owner 对应的参与者, 环境墙为 None
        geometry 局部几何
        matrix 墙的世界矩阵
        event 墙自己的第一次起动, 没有时为 None
        dt 一帧经过的模拟秒
        返回 Wall
        """
        low, high = _box(geometry, matrix)
        # 有起动时, 墙在起动后会移动: 包围盒要覆盖从静止位置到起动终点的整段
        if event is not None:
            shift = Vector(event.velocity) * dt
            arc = geometry.radius * max(matrix.to_scale()) * Vector(event.spin).length * dt
            margin = Vector((arc, arc, arc))
            low, high = (
                Vector(min(a, b) for a, b in zip(low, low + shift, strict=True)) - margin,
                Vector(max(a, b) for a, b in zip(high, high + shift, strict=True)) + margin,
            )
        radius = geometry.radius * max(matrix.to_scale())
        return Wall(owner, geometry, matrix, event, (low, high), final=event, radius=radius)

    def _tree(self, wall, frame, grid):
        """求帧 frame 起动, 起动者走到网格 grid 格时这面墙的求交树与世界矩阵

        起动时间晚于 frame 的墙留在原位; 同帧起动的墙与起动者同步移动, 按它自己当前的实际事件;
        起动时间早于 frame 的墙已经不在原位, 位置要等推进到那里才知道, 不参与判定
        wall 墙
        frame 起动者的起动帧
        grid 起动者走到的网格格数
        返回 (求交树, 世界矩阵); 不参与判定时为 None
        """
        event = wall.event
        # 墙比起动者更早起动: 它此刻的位置未知, 不参与
        if event is not None and event.frame < frame:
            return None
        moving = event is not None and event.frame == frame
        # 留在原位的墙只有一棵树 (键 (0, 0)); 同帧起动的墙随版本与格数各有一棵
        key = (wall.version, grid) if moving else (0, 0)
        if key not in wall.trees:
            matrix = (
                _pose(wall.matrix, _translation_only(wall.final), grid / GRID, self.dt)
                if moving
                else wall.matrix
            )
            world = [matrix @ point for point in wall.geometry.points]
            wall.trees[key] = (BVHTree.FromPolygons(world, wall.geometry.polygons), matrix)
        return wall.trees[key]

    @staticmethod
    def _near(wall, low, high):
        """判断墙的包围盒与包围盒 (low, high) 是否相交

        wall 墙
        low 包围盒最小角
        high 包围盒最大角
        返回 相交为真
        """
        wall_low, wall_high = wall.box
        return all(
            wall_low[axis] <= high[axis] and low[axis] <= wall_high[axis] for axis in range(3)
        )

    def _candidates(self, participant, low, high):
        """可能挡住包围盒 (low, high) 内起动的墙, 不含起动者自己

        participant 起动者
        low 起动者扫过的包围盒最小角
        high 起动者扫过的包围盒最大角
        返回 墙列表
        """
        center, radius = (low + high) / 2.0, (high - low).length / 2.0
        # 碎块墙经空间索引粗筛, 环境墙无条件加入, 最后统一按包围盒精筛
        found = [
            self._small[number]
            for _co, number, _dist in self._tree_index.find_range(center, radius + self._half)
        ]
        found += self._large
        return [
            wall for wall in found if wall.owner is not participant and self._near(wall, low, high)
        ]

    @staticmethod
    def _contact(wall, matrix, faces, event):
        """墙在 faces 这些面上与起动者相交时, 顶得最实的那个面的接触

        wall 墙
        matrix 墙此刻的世界矩阵
        faces 相交的墙面序号集合
        event 起动事件
        返回 Contact
        """
        # 同帧起动的墙在移动, 接触要带上它的速度才能取相对速度
        moving = wall.event is not None and wall.event.frame == event.frame
        speed = Vector(wall.final.velocity) if moving else Vector()
        # 非等比缩放下法线要用逆转置矩阵变换
        rotation = matrix.to_3x3().inverted_safe().transposed()
        contacts = [
            Contact((rotation @ wall.geometry.normals[face]).normalized(), speed) for face in faces
        ]
        return min(contacts, key=lambda contact: _inward(event, contact))

    def _blocked(self, geometry, matrix, walls, event, grid):
        """起动者按质心缩小, 走到网格 grid 格的位姿下, 是否与墙相交

        geometry 起动者的局部几何
        matrix 起动时的世界矩阵
        walls 候选墙
        event 起动事件
        grid 位姿变化的网格格数, 0 到 GRID
        返回 顶得最实的接触; 不相交时为 None
        """
        pose = _pose(matrix, event, grid / GRID, self.dt)
        world = [pose @ (point * SHRINK) for point in geometry.points]
        tree = BVHTree.FromPolygons(world, geometry.polygons)
        reach = geometry.radius * max(matrix.to_scale())
        contacts = []
        for wall in walls:
            entry = self._tree(wall, event.frame, grid)
            # 包围球不相交的墙不可能碰到, 省掉网格求交
            if (
                entry is None
                or (pose.translation - entry[1].translation).length > reach + wall.radius
            ):
                continue
            pairs = tree.overlap(entry[0])
            if pairs:
                contacts.append(
                    self._contact(wall, entry[1], {face for _own, face in pairs}, event)
                )
        return min(contacts, key=lambda contact: _inward(event, contact), default=None)

    def _scan(self, geometry, matrix, event, walls, reach):
        """沿起动位姿变化粗采样, 找第一个相交处

        geometry 起动者的局部几何
        matrix 起动时的世界矩阵
        event 起动事件
        walls 候选墙
        reach 这次起动表面最多移动的距离
        返回 (前一个不相交的格数, 相交的格数, 接触); 一路畅通为 None
        """
        wanted = ceil(reach / max(geometry.radius * STEP_RATIO, 1e-6))
        # 粗采样段数取不小于所需的 2 的幂, 格数才是整数, 二分后仍落在网格上
        steps = min(MAX_STEPS, 1 << max(0, wanted - 1).bit_length())
        size = GRID // steps
        # 从起点往终点逐段试, 第一个相交的段就是要找的
        for grid in range(size, GRID + 1, size):
            contact = self._blocked(geometry, matrix, walls, event, grid)
            if contact is not None:
                return grid - size, grid, contact
        return None

    def _refine(self, geometry, matrix, event, walls, found):
        """在粗采样找到的相交段里二分

        粗采样的第一个相交点往往已经插进墙里一格深, 那里的面法线不准
        二分后的接触才用来决定滑动方向
        geometry 起动者的局部几何
        matrix 起动时的世界矩阵
        event 起动事件
        walls 候选墙
        found _scan 的返回值
        返回 (可以通行的比例, 最接近接触点处的接触)
        """
        free, stuck, contact = found
        for _ in range(REFINE):
            middle = (free + stuck) // 2
            result = self._blocked(geometry, matrix, walls, event, middle)
            # 中点畅通: 通行范围推进到中点; 仍相交: 相交位置前移并更新接触
            if result is None:
                free = middle
            else:
                stuck, contact = middle, result
        return free / GRID, contact

    def _relieve_spin(self, geometry, matrix, current, near, found):
        """起动被挡住时先看是不是自转造成的

        碎块紧贴着, 稍一转动角就会插进邻块; 若不带自转平移是畅通的, 只削减自转, 平移速度保持
        否则去掉自转后再按平移判定: 顶着墙的碎块转不动, 自转对结果没有意义
        geometry 起动者的局部几何
        matrix 起动时的世界矩阵
        current 当前的起动事件
        near 候选墙
        found 带自转的事件的粗采样相交结果
        返回 (最终事件或 None, 继续判定用的事件, 对应的相交结果)
        """
        # 本来就没有自转, 受阻不可能由自转引起
        if not any(current.spin):
            return None, current, found
        scale = max(matrix.to_scale())
        spinless = replace(current, spin=(0.0, 0.0, 0.0))
        probe = self._scan(
            geometry, matrix, spinless, near, _reach(geometry, scale, spinless, self.dt)
        )
        # 去掉自转后一路畅通: 只按通行比例缩减自转, 平移速度保持
        if probe is None:
            fraction, _contact = self._refine(geometry, matrix, current, near, found)
            return _spin_scaled(current, fraction), current, found
        return None, spinless, probe

    def _resolve(self, participant, matrix, event, walls=None):
        """起动在 matrix 位姿上发生时实际得到的事件: 去掉朝向墙的分量, 墙角处仍被挡住的按通行比例缩减

        participant 起动者
        matrix 起动时的世界矩阵
        event 计划里的起动事件
        walls 预先查好的候选墙, 缺省时每一轮按当时的位移范围查
        返回 调整后的事件
        """
        geometry = self._geometry_of(participant.shard)
        scale = max(matrix.to_scale())
        current = event
        # 每一轮去掉一面墙的法向分量; 墙角需要多轮, 轮数用完仍被挡住就按通行比例缩减
        for turn in range(SLIDE_ROUNDS + 1):
            reach = _reach(geometry, scale, current, self.dt)
            box = _swept(matrix, geometry, current, self.dt)
            near = walls if walls is not None else self._candidates(participant, *box)
            # 没有位移或附近没有墙: 不受阻
            if reach <= 0.0 or not near:
                return current
            found = self._scan(geometry, matrix, current, near, reach)
            # 一路畅通: 不受阻
            if found is None:
                return current
            relieved, current, found = self._relieve_spin(geometry, matrix, current, near, found)
            if relieved is not None:
                return relieved
            fraction, contact = self._refine(geometry, matrix, current, near, found)
            # 最后一轮不再滑动, 直接缩减; 其余轮次去掉朝墙的分量后重新判定
            slid = None if turn == SLIDE_ROUNDS else _slide(current, contact)
            if slid is None:
                return _scaled(current, fraction)
            current = slid
        return current

    def _neighbours(self, wall):
        """第一次起动按原速度发生时可能挡路的墙; 滑动与缩减只会让范围变小, 它在每一轮都够用

        wall 墙
        返回 候选墙列表
        """
        box = _swept(wall.matrix, wall.geometry, wall.event, self.dt)
        return self._candidates(wall.owner, *box)

    @staticmethod
    def _order(owned, neighbours):
        """判定顺序: 起动者朝向的前方的墙先判定, 它顶着的墙才有最终事件; 互相顶着的环任意断开

        只有同帧起动的墙会互相移动, 其余的墙不随起动者变化, 不影响顺序
        owned 有起动事件的碎块墙列表
        neighbours {墙标识: 候选墙列表}
        返回 判定顺序的墙列表, 前方的墙排在前面
        """
        # ahead[墙] = 它前方同帧起动的邻墙: 它们的结果决定这面墙的判定
        ahead = {}
        for wall in owned:
            velocity, center = Vector(wall.event.velocity), wall.matrix.translation
            ahead[id(wall)] = [
                other
                for other in neighbours[id(wall)]
                if other.event is not None
                and other.event.frame == wall.event.frame
                and (other.matrix.translation - center).dot(velocity) > 0.0
            ]
        order, seen = [], set()
        for root in owned:
            if id(root) in seen:
                continue
            seen.add(id(root))
            # 迭代的深度优先, 后序输出: 一块碎块前面的墙都排在它之前
            stack = [(root, iter(ahead[id(root)]))]
            while stack:
                wall, children = stack[-1]
                for child in children:
                    # 已经访问过的邻墙跳过: 互相顶着的环在这里被断开
                    if id(child) not in seen:
                        seen.add(id(child))
                        stack.append((child, iter(ahead[id(child)])))
                        break
                else:
                    order.append(wall)
                    stack.pop()
        return order

    def _settle(self):
        """冻结碎块的第一次起动在静止姿态上判定; 同帧起动的互相挡路

        第一轮按前方优先的顺序判定全部; 之后只重算在判定时用了过时结果的墙: 邻居在它之后才变化,
        或它本轮没有重算而邻居变了; 到不再变化或轮数用完为止
        """
        owned = list(self._owned.values())
        neighbours = {id(wall): self._neighbours(wall) for wall in owned}
        order = self._order(owned, neighbours)
        position = {id(wall): number for number, wall in enumerate(order)}
        # 需要重算的墙: 第一轮是全部
        dirty = set(position)
        for _ in range(SETTLE_ROUNDS):
            changed = set()
            for wall in order:
                if id(wall) not in dirty:
                    continue
                result = self._resolve(wall.owner, wall.matrix, wall.event, neighbours[id(wall)])
                # 结果变了才更新版本: 求交树缓存据版本判断是否过时
                if not _same(result, wall.final):
                    wall.final = result
                    wall.version += 1
                    # 旧版本的树再也用不上, 只留位置不变的那一棵
                    wall.trees = {key: tree for key, tree in wall.trees.items() if key == (0, 0)}
                    changed.add(id(wall))
            # 下一轮只重算用了过时邻居结果的墙: 邻居在它之后才变化, 或它本轮没重算而邻居变了
            dirty = {
                id(wall)
                for wall in order
                if any(
                    id(other) in changed
                    and (id(wall) not in dirty or position[id(other)] > position[id(wall)])
                    for other in neighbours[id(wall)]
                )
            }
            if not dirty:
                break
        # 判定后与原计划不同的起动都算作被挡住
        self.blocked = sum(1 for wall in owned if not _same(wall.final, wall.event))

    def first_event(self, participant):
        """冻结碎块的第一次起动, 已按受阻判定调整

        participant 参与者
        返回 事件
        """
        return self._owned[id(participant)].final

    def limit(self, participant, matrix, event):
        """途中起动在 matrix 位姿上发生时的受阻判定; 速度被改变时计入 blocked

        participant 起动者
        matrix 起动时的世界矩阵
        event 计划里的起动事件
        返回 调整后的事件
        """
        result = self._resolve(participant, matrix, event)
        if not _same(result, event):
            self.blocked += 1
        return result
