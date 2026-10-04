"""UI 绘制验证 - 在真实界面里驱动插件的全部绘制入口

测试目的:
    绘制回调出错时 Blender 不会中断程序, 只在控制台留一行 "Python script error",
    界面上表现为面板只剩标题; 绘制期写 ID 数据是其中最常见的成因
    这类缺陷在 -b 后台模式与单元测试里都不会暴露, 必须真实绘制才会出现
测试过程:
    1. 注册插件, 把各工具的面板放进出厂设置下侧栏默认显示的 Item 页, 交给 Blender 自己绘制
    2. 另注册一个驱动面板, 在它的 draw 里用真实 layout 与真实绘制上下文逐个驱动绘制入口:
       工具面板, 子面板与弹出面板, 标题栏预设入口, 预设菜单; 转发型 layout 代理记录每次控件调用
    3. 自检: 在同一上下文里写 ID 数据必须被拒绝, 否则说明驱动上下文并不受限, 整个结论不成立
    入口全部由 registration.TOOLS 推导, 新增工具会自动纳入
判据:
    - 工具的顶层面板没有父面板, 子面板挂在本工具的面板下; 自身或上级默认收起的面板没有被绘制,
      其余侧栏面板都被绘制且不抛异常; 弹出面板只在点开时绘制, 由驱动面板驱动
    - 每个驱动入口不抛异常并至少产生一个控件, 包括子面板与弹出面板的内容
    - 顶层面板提供本工具的算子入口; 标题栏预设入口经布局上下文写入工具标识;
  预设菜单请求到全部预设算子
    - 交互调用生成算子: 源网格有问题时先弹出确认框, 确认之前不切割 (后台模式没有窗口, 只能在这里测)
    - 交互调用烘焙算子: 立即进入模态并持有运行锁, 之后由定时器逐帧推进到完成,
  运行锁与状态栏都被释放,
      资产的模拟状态为输入未变 (模态任务需要事件循环, 后台模式无法运行)
    - 计划预览: 打开后真实窗口里绘制回调算出线段并建好批次, 没有记下错误
    - 烘焙进行中按 Esc (真实的事件): 烘焙被取消, 运行锁, 计时器与载入回调都被释放, 没有成果与动画
    - 烘焙进行中载入文件: 运行锁, 计时器与载入回调都被释放
    - 烘焙进行中停用插件 (Blender 注销算子时不调用 cancel): 场景回到烘焙之前,
  运行锁, 计时器与载入回调
      都被释放, 之后能重新启用
运行方式 (仓库根目录, 不能加 -b):
    <Blender 可执行文件> --enable-event-simulate --factory-startup --python-exit-code 1 --python tests\\blender\\verify_ui_draw.py
实测环境: Blender 5.2.2 (Python 3.13)
"""

import os
import shutil
import sys
import traceback
import uuid
from pathlib import Path
from types import SimpleNamespace

import bpy

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from source import registration  # noqa: E402
from source.common import asset, drivers  # noqa: E402
from source.common.status import CURRENT, simulation_status  # noqa: E402
from source.framework import preset_files, presets  # noqa: E402
from source.tools.Drivers.api import add_driver  # noqa: E402
from source.tools.MeshFracture.api import generate_fragments  # noqa: E402
from source.tools.Physics.api import apply_physics  # noqa: E402
from source.tools.Simulation.core import task  # noqa: E402

# 隔离的预设目录: 预设菜单要有预设才会绘制列表分支, 这里不碰用户目录
PRESET_ROOT = REPO_ROOT / "tmp" / "output_tmp" / "ui_draw_preset"
# 出厂设置下 3D 视图侧栏默认显示的分类
CATEGORY = "Item"
# 真正往界面里放东西的调用; row, box, column 这类容器不计入控件数
WIDGET_CALLS = frozenset({"label", "operator", "menu", "prop", "prop_enum", "popover"})

# 不成立的判据
FAILURES = []
# Blender 自己绘制过的面板: 类名 -> 异常, None 表示正常
DRAWN = {}
# 面板类被包装前的 draw, 驱动面板调用它, 以免被记成 Blender 绘制过
ORIGINAL_DRAWS = {}
# 驱动结果: (入口, 异常, 调用记录)
RESULTS = []
# 模态烘焙的启动结果与它作用的资产
BAKE = {}
# 中断用例的烘焙: 帧数足够多, 中断时一定还在进行
LONG_FRAMES = 400


def check(label, condition, detail=""):
    """记录并打印一条判据"""
    suffix = f" | {detail}" if detail else ""
    print(f"[{'PASS' if condition else 'FAIL'}] {label}{suffix}", flush=True)
    if not condition:
        FAILURES.append(label)


class RecordingLayout:
    """把调用转发给真实 layout 并记录调用名与实参, 用来判断绘制是否真的产生了控件"""

    def __init__(self, layout, calls):
        """保存真实 layout 与记录列表; 用 object.__setattr__ 避免被转发"""
        object.__setattr__(self, "real", layout)
        object.__setattr__(self, "calls", calls)

    def __getattr__(self, name):
        """取真实 layout 的成员; 方法包装成记录调用的转发函数, 其余属性原样返回"""
        target = getattr(self.real, name)
        if not callable(target):
            return target

        def call(*args, **kwargs):
            """转发并记录; 返回的子布局继续包装, 算子属性代理等原样交回"""
            result = target(*args, **kwargs)
            self.calls.append((name, args))
            if isinstance(result, bpy.types.UILayout):
                return RecordingLayout(result, self.calls)
            return result

        return call

    def __setattr__(self, name, value):
        """属性写入转发给真实 layout"""
        setattr(self.real, name, value)


class Entry:
    """一个要驱动的绘制入口与它的期望"""

    def __init__(self, name, call, operators=(), *, any_operator=False, token=False, raises=""):
        """call 的签名是 (layout, context)"""
        # 显示名
        self.name = name
        # 绘制调用
        self.call = call
        # 期望请求到的算子 ID
        self.operators = frozenset(operators)
        # 为真时只要求请求到其中任意一个, 否则要求全部
        self.any_operator = any_operator
        # 是否要求经布局上下文写入工具标识
        self.token = token
        # 非空表示期望抛出包含该片段的异常, 用于自检
        self.raises = raises


def isolated_directory(tool_id, create=False):
    """替代 presets.preset_directory, 把预设目录指向仓库内的隔离目录"""
    target = PRESET_ROOT / tool_id
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def as_self(layout):
    """面板与菜单的 draw 只用到 self.layout, 一个带 layout 的替身就够"""
    return SimpleNamespace(layout=layout)


def tool_panels(tool):
    """工具声明的面板类"""
    return [cls for cls in tool.classes if issubclass(cls, bpy.types.Panel)]


def is_popover(cls):
    """弹出面板不在侧栏里, 只由按钮弹出"""
    return cls.bl_region_type != "UI"


def parent_of(cls):
    """子面板的父面板名, 顶层面板返回空串"""
    return cls.__dict__.get("bl_parent_id", "")


def tool_entries(tool, context):
    """一个工具的绘制入口: 面板内容, 以及带预设时的标题栏预设入口与预设菜单"""
    own = [cls.bl_idname for cls in tool.classes if issubclass(cls, bpy.types.Operator)]
    entries = []
    for cls in tool_panels(tool):
        # 顶层面板必须提供本工具的算子入口, 子面板与弹出面板只放参数
        entries.append(
            Entry(
                f"{tool.tool_id}/{cls.__name__}",
                lambda layout, ctx, draw=ORIGINAL_DRAWS[cls]: draw(as_self(layout), ctx),
                () if parent_of(cls) or is_popover(cls) else own,
                any_operator=True,
            )
        )
    if not tool.presets:
        return entries
    main = next(cls for cls in tool_panels(tool) if not parent_of(cls) and not is_popover(cls))
    menu_context = SimpleNamespace(**{presets.TOOL_MEMBER: tool.tool_id}, scene=context.scene)
    preset_ids = [cls.bl_idname for cls in presets.CLASSES if issubclass(cls, bpy.types.Operator)]
    entries.append(
        Entry(
            f"{tool.tool_id}/标题栏预设入口",
            lambda layout, ctx: main.draw_header_preset(
                SimpleNamespace(layout=layout, tool_id=main.tool_id), ctx
            ),
            token=True,
        )
    )
    entries.append(
        Entry(
            f"{tool.tool_id}/预设菜单",
            lambda layout, _ctx: presets.QD_MT_presets.draw(as_self(layout), menu_context),
            preset_ids,
        )
    )
    return entries


def build_entries(context):
    """由工具清单推导全部绘制入口, 最后加上写 ID 数据的自检"""
    entries = [entry for tool in registration.TOOLS for entry in tool_entries(tool, context)]
    owner = next(
        getattr(context.scene.qd_settings, tool.tool_id)
        for tool in registration.TOOLS
        if tool.schema is not None
    )
    entries.append(
        Entry(
            "自检/绘制期写 ID 数据被拒绝",
            lambda _layout, _ctx: setattr(owner, "preset_name", uuid.uuid4().hex),
            raises="Writing to ID classes",
        )
    )
    return entries


def driver_draw(self, context):
    """驱动面板的 draw: 第一次被绘制时逐个驱动全部入口"""
    if RESULTS:
        return
    for entry in build_entries(context):
        calls = []
        try:
            entry.call(RecordingLayout(self.layout, calls), context)
            RESULTS.append((entry, None, calls))
        except Exception as error:
            RESULTS.append((entry, error, calls))


def recorded_draw(cls):
    """包装面板的 draw: 完整执行并记录结果, 不把异常抛回 Blender"""
    original = cls.draw

    def draw(self, context):
        """记录这块面板被 Blender 绘制过"""
        try:
            original(self, context)
            DRAWN[cls.__name__] = None
        except Exception as error:
            DRAWN[cls.__name__] = error
            traceback.print_exc()

    return draw


def redraw():
    """展开侧栏并强制重绘, 绘制回调在重绘里被调用"""
    for area in bpy.context.screen.areas:
        if area.type == "VIEW_3D":
            area.spaces.active.show_region_ui = True
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def start():
    """注册插件与驱动面板, 换掉预设目录并放一个预设, 然后重绘"""
    for tool in registration.TOOLS:
        for cls in tool_panels(tool):
            if not is_popover(cls):
                cls.bl_category = CATEGORY
            ORIGINAL_DRAWS[cls] = cls.draw
            cls.draw = recorded_draw(cls)
    registration.register()
    presets.preset_directory = isolated_directory
    shutil.rmtree(PRESET_ROOT, ignore_errors=True)
    for tool in registration.TOOLS:
        if tool.presets:
            envelope = preset_files.encode(tool.tool_id, {})
            preset_files.save_preset(isolated_directory(tool.tool_id, True), "listed", envelope)
    driver = type(
        "QD_PT_ui_driver",
        (bpy.types.Panel,),
        {
            "bl_label": "UI DRIVER",
            "bl_space_type": "VIEW_3D",
            "bl_region_type": "UI",
            "bl_category": CATEGORY,
            "draw": driver_draw,
        },
    )
    bpy.utils.register_class(driver)
    redraw()


def folded(cls, panels):
    """面板或它的任一上级默认收起时, Blender 不会绘制它"""
    by_name = {panel.__name__: panel for panel in panels}
    while cls is not None:
        if "DEFAULT_CLOSED" in set(getattr(cls, "bl_options", ()) or ()):
            return True
        cls = by_name.get(parent_of(cls))
    return False


def check_sidebar():
    """Blender 自己绘制的侧栏面板: 顶层面板没有父面板, 子面板挂在本工具面板下

    收起的不绘制, 其余绘制且不抛异常
    """
    for tool in registration.TOOLS:
        panels = [cls for cls in tool_panels(tool) if not is_popover(cls)]
        names = {cls.__name__ for cls in panels}
        for cls in panels:
            name = cls.__name__
            parent = parent_of(cls)
            check(f"{name} 挂在本工具的面板下或是顶层面板", not parent or parent in names, parent)
            closed = folded(cls, panels)
            check(
                f"{name} 的绘制情况与折叠状态一致",
                (name in DRAWN) == (not closed),
                f"被绘制 {name in DRAWN}, 收起 {closed}",
            )
            if name in DRAWN:
                check(f"{name} 绘制没有抛异常", DRAWN[name] is None, repr(DRAWN[name]))


def check_entry(entry, error, calls):
    """驱动入口的判据"""
    if entry.raises:
        check(entry.name, error is not None and entry.raises in str(error), repr(error))
        return
    widgets = sum(1 for name, _args in calls if name in WIDGET_CALLS)
    check(f"{entry.name} 正常绘制出控件", error is None and widgets > 0, f"{widgets} {error!r}")
    requested = {args[0] for name, args in calls if name == "operator" and args}
    if entry.operators:
        hit = requested & entry.operators if entry.any_operator else entry.operators <= requested
        check(f"{entry.name} 请求到算子", bool(hit), f"请求 {sorted(requested)}")
    if entry.token:
        written = {args[0] for name, args in calls if name == "context_string_set" and args}
        check(f"{entry.name} 写入工具标识", presets.TOOL_MEMBER in written, str(sorted(written)))


def check_dialog():
    """真实窗口里调用生成算子: 源网格有问题时先弹出确认框而不切割"""
    bpy.ops.mesh.primitive_monkey_add()
    monkey = bpy.context.active_object
    before = len(bpy.data.objects)
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(area=area, region=region):
        result = bpy.ops.qd_mesh_fracture.generate("INVOKE_DEFAULT")
    check("有问题的网格先弹出确认框", result == {"RUNNING_MODAL"}, f"{monkey.name}: {result}")
    check("确认之前没有切割", len(bpy.data.objects) == before)


def invoke_bake(x, frame_end):
    """在 x 处切出小资产, 应用物理并放爆炸驱动, 只选中一块碎块后以界面方式调用烘焙

    返回 (启动结果, 根集合, 碎块列表, 算子实例); 没有进入模态时算子实例为 None
    """
    scene = bpy.context.scene
    scene.frame_start, scene.frame_end = 1, frame_end
    bpy.ops.mesh.primitive_cube_add(size=2.0, location=(x, 0.0, 1.0))
    source = bpy.context.active_object
    result = generate_fragments(bpy.context, [source], {"recursion": 0})
    root = result.source_to_asset[source]
    apply_physics(bpy.context, [root], {})
    add_driver(bpy.context, drivers.EXPLOSION, location=(x, 0.0, 1.0), frame=3)
    for obj in bpy.context.view_layer.objects.selected:
        obj.select_set(False)
    result.shards[0].select_set(True)
    bpy.context.view_layer.objects.active = result.shards[0]
    area = next(a for a in bpy.context.screen.areas if a.type == "VIEW_3D")
    region = next(r for r in area.regions if r.type == "WINDOW")
    with bpy.context.temp_override(area=area, region=region):
        started = bpy.ops.qd_simulation.bake("INVOKE_DEFAULT")
    # 运行锁里登记的是算子的取消函数, 由它找到算子实例, 之后核对计时器是否释放
    operator = task.RUNNING[0].__self__ if task.RUNNING else None
    return started, root, result.shards, operator


def start_bake():
    """以界面方式调用一次完整烘焙; 结果在 check_main 里核对"""
    started, root, _shards, _operator = invoke_bake(30.0, 12)
    BAKE["started"] = started
    BAKE["busy"] = task.busy()
    BAKE["root"] = root


def check_bake():
    """模态烘焙: 启动时进入模态并持有运行锁, 结束后锁已释放, 成果与状态正确"""
    check("烘焙以模态方式启动", BAKE.get("started") == {"RUNNING_MODAL"}, str(BAKE.get("started")))
    check("烘焙进行中持有运行锁", BAKE.get("busy") is True)
    root = BAKE.get("root")
    check("烘焙结束后释放运行锁", not task.busy())
    done = root is not None and asset.sim_action(root) is not None
    check("烘焙结束后有成果动作", done)
    if done:
        state = simulation_status(root, bpy.context.scene).state
        check("烘焙结束后模拟状态为输入未变", state == CURRENT, state)


def enable_preview():
    """打开计划预览并重绘: 绘制回调第一次执行时算出线段与批次"""
    bpy.context.scene.qd_settings.drivers.show_preview = True
    redraw()


def check_preview():
    """预览的绘制回调在真实窗口里跑过: 建好了批次, 没有记下错误"""
    from source.tools.Drivers import preview

    check(
        "计划预览建好了批次", preview.STATE["batch"] is not None, str(len(preview.STATE["lines"]))
    )
    check("计划预览绘制没有出错", preview.STATE["error"] == "", preview.STATE["error"])
    check("预览的线段不止一条", len(preview.STATE["lines"]) > 1)


def start_escape():
    """启动一个帧数很多的烘焙, 交给随后的定时器发 Esc"""
    started, root, shards, operator = invoke_bake(90.0, LONG_FRAMES)
    BAKE["escape"] = SimpleNamespace(started=started, root=root, shards=shards, operator=operator)


def send_escape():
    """向窗口发一个真实的 Esc 按下事件, 由模态算子的 modal 处理"""
    bpy.context.window.event_simulate(type="ESC", value="PRESS")


def check_escape():
    """按 Esc 之后: 烘焙被取消, 资源释放, 场景回到烘焙之前"""
    run = BAKE["escape"]
    check("按 Esc 前烘焙以模态方式进行中", run.started == {"RUNNING_MODAL"})
    check_released("按 Esc 取消", run)
    check("按 Esc 后没有模拟成果", asset.sim_record(run.root) is None)
    animated = [s.name for s in run.shards if s.animation_data and s.animation_data.action]
    check("按 Esc 后碎块不带动画", not animated, str(animated[:3]))
    gap = max(
        (s.matrix_world.translation - asset.rest_matrix(s).translation).length for s in run.shards
    )
    check("按 Esc 后碎块在静止姿态", gap < 1e-6, f"{gap:.2e}")


def has_load_handler():
    """载入回调是否还登记着; 按名字找, 不导入工具的私有函数"""
    return any(
        getattr(func, "__name__", "") == "_abort_on_load" for func in bpy.app.handlers.load_pre
    )


def timer_released(operator):
    """算子实例上的计时器是否已释放

    算子被 Blender 释放后实例的 RNA 指针失效, 按属性读取会报错; 自定义属性存在实例字典里,
    用 object.__getattribute__ 绕过 RNA 直接读字典
    """
    return object.__getattribute__(operator, "__dict__").get("_timer") is None


def start_interrupted(key):
    """启动一个帧数很多的烘焙, 交给随后的定时器中断; 结果记在 BAKE[key]"""
    started, root, shards, operator = invoke_bake(60.0, LONG_FRAMES)
    BAKE[key] = SimpleNamespace(started=started, root=root, shards=shards, operator=operator)


def check_released(label, run):
    """中断之后: 运行锁, 载入回调与算子的计时器都已释放"""
    check(f"{label}后释放运行锁", not task.busy())
    check(f"{label}后移除载入回调", not has_load_handler())
    operator = run.operator
    check(f"{label}后释放计时器", operator is not None and timer_released(operator))


def interrupt_by_load():
    """烘焙进行中载入文件: 载入回调取消烘焙, 旧窗口的处理器随后被移除"""
    run = BAKE["load"]
    check("载入文件前烘焙以模态方式进行中", run.started == {"RUNNING_MODAL"} and task.busy())
    bpy.ops.wm.read_homefile(use_empty=True)
    check_released("载入文件", run)


def interrupt_by_disable():
    """烘焙进行中停用插件: 场景回到烘焙之前, 资源都被释放, 之后能重新启用"""
    run = BAKE["disable"]
    check("停用插件前烘焙以模态方式进行中", run.started == {"RUNNING_MODAL"} and task.busy())
    registration.unregister()
    check_released("停用插件", run)
    check("停用插件后没有模拟成果", asset.sim_record(run.root) is None)
    animated = [shard.name for shard in run.shards if shard.animation_data is not None]
    check("停用插件后碎块不带动画数据", not animated, str(animated[:3]))
    gap = max(
        (shard.matrix_world.translation - asset.rest_matrix(shard).translation).length
        for shard in run.shards
    )
    check("停用插件后碎块回到静止姿态", gap < 1e-6, f"{gap:.2e}")
    registration.register()
    check("停用后能重新启用", hasattr(bpy.types, "QD_SIMULATION_OT_bake"))


def check_main():
    """侧栏, 绘制入口与完整烘焙的判据"""
    check_sidebar()
    check_bake()
    check("驱动面板被执行", bool(RESULTS))
    for entry, error, calls in RESULTS:
        check_entry(entry, error, calls)


def finish():
    """汇总判据; 定时器里无法传退出码, 失败时直接以非零状态结束进程"""
    check_dialog()
    shutil.rmtree(PRESET_ROOT, ignore_errors=True)
    print("ALL PASS" if not FAILURES else f"失败 {len(FAILURES)} 项: {FAILURES}", flush=True)
    if FAILURES:
        os._exit(1)
    bpy.ops.wm.quit_blender()


if bpy.app.background:
    # 后台模式没有绘制回调, 直接报错退出而不是给出假通过
    print("[FAIL] 本脚本需要真实界面, 请去掉 -b 运行", flush=True)
    raise SystemExit(1)

bpy.app.timers.register(start, first_interval=1.0)
bpy.app.timers.register(start_bake, first_interval=2.0)
bpy.app.timers.register(enable_preview, first_interval=3.0)
bpy.app.timers.register(check_main, first_interval=3.5)
bpy.app.timers.register(check_preview, first_interval=3.6)
bpy.app.timers.register(start_escape, first_interval=3.8)
bpy.app.timers.register(send_escape, first_interval=4.2)
bpy.app.timers.register(check_escape, first_interval=4.5)
# 之后的定时器要跨过载入文件, 必须是 persistent, 否则载入时被移除
bpy.app.timers.register(lambda: start_interrupted("load"), first_interval=4.7, persistent=True)
bpy.app.timers.register(interrupt_by_load, first_interval=4.8, persistent=True)
bpy.app.timers.register(lambda: start_interrupted("disable"), first_interval=5.1, persistent=True)
bpy.app.timers.register(interrupt_by_disable, first_interval=5.4, persistent=True)
bpy.app.timers.register(finish, first_interval=5.7, persistent=True)
