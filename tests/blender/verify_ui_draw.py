"""UI 绘制验证 - 在真实界面里驱动插件的全部绘制入口

测试目的:
    绘制回调出错时 Blender 不会中断程序, 只在控制台留一行
    "Python script error in <回调>", 界面上表现为面板只剩标题, 内容为空;
    其中一类成因是绘制期写了 ID 数据:
        AttributeError: Writing to ID classes in this context is not allowed
    这类缺陷在 -b 后台模式与函数级单测里都不会暴露, 必须真实绘制才会出现

测试过程:
    1. 探测侧栏当前激活的分类 - 启动配置不同, 激活的页也不同
    2. 装配检查: 注册插件, 把各工具的侧栏面板放进该分类, 交给 Blender 自己绘制,
       确认 draw 完整执行, 并确认它们都是顶层面板而不是挂在某个父面板下
    3. 内容检查: 另注册一个驱动面板, 在它的 draw 里用真实 layout 与真实绘制上下文
       逐个驱动插件声明的全部绘制入口 (工具面板, 工具菜单,
       预设入口, 框架预设菜单), 并用转发型 layout 代理记录控件调用
       入口与期望值全部由工具声明推导, 新增工具会自动纳入
    4. 自检: 在同一条路径里写 ID 数据必须被拒绝 - 否则说明驱动用的上下文并不受限,
       整个测试的结论都不成立

判据:
    - 每个入口都不抛异常
    - 控件数不低于该入口的期望值 (捕捉"静默提前返回"的空面板)
    - 声明了算子的工具, 它的侧栏面板必须提供其中某个算子的入口 (仓库约定: 面板是入口)
    - 只声明面板的工具没有算子可入口, 该条不适用, 只核对控件数
    - 带预设的工具在绘制期必须经布局上下文写入工具标识
    - 框架预设菜单必须请求到全部通用预设算子
    - bl_options 含 DEFAULT_CLOSED 的面板没有被 Blender 绘制, 其余面板都被绘制
      (实测: 收起的面板在正常绘制时不会调用 draw, 内容检查由驱动面板覆盖)

运行方式 (仓库根目录, 不能加 -b, 需要真实界面):
    <Blender 可执行文件> --factory-startup --python-exit-code 1 --python tests\\blender\\verify_ui_draw.py

实测环境: Blender 5.2.2 (Python 3.13) 与 Blender 4.2.23 (Python 3.11)
"""

import os
import shutil
import sys
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import bpy

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

# 隔离的预设目录: 菜单的列表分支需要有预设才会执行, 这里不写用户配置
PRESET_ROOT = REPO_ROOT / "tmp" / "output_tmp" / "ui_draw_preset"

# 候选分类: 只要有一个被绘制出来, 就能确定侧栏当前激活的是哪一页
PROBE_CATEGORIES = (
    "Tool",
    "Item",
    "View",
    "Object",
    "Scene",
    "Edit",
    "Render",
    "Output",
    "World",
    "Collection",
    "Modifier",
    "Shader",
    "Animation",
    "Physics",
    "QuickDestruction",
)

# 真正往界面里放东西的调用; 容器类调用 (row / box / column) 不计入控件数
WIDGET_CALLS = frozenset(
    {"label", "operator", "menu", "prop", "prop_enum", "prop_menu_enum", "prop_search"}
)

FAILURES = []
# 探针面板被绘制时记录自己所在的分类
PROBE_DREW = []
# Blender 自己绘制的面板: (类名, 异常)
SIDEBAR_RESULTS = []
# 驱动面板跑出来的入口结果
DRIVE_RESULTS = []
# 驱动只跑第一帧, 避免重复计数
DRIVEN = False
# 被替换掉的预设目录函数, 结束后还原
ORIGINAL_PRESET_DIRECTORY = None
# 本次运行的工具清单, 供结果阶段核对面板的预期绘制状态
TOOLS_SNAPSHOT = ()
# 面板类被包装前的 draw: 驱动面板必须调它, 否则会被记成"Blender 画过"
ORIGINAL_DRAWS = {}


@dataclass
class Entry:
    """一个要被驱动的绘制入口"""

    # 显示名
    name: str
    # 调用体, 签名 (layout, context)
    call: object
    # 期望的最少控件数
    minimum: int = 1
    # 归属工具; 非空时要求本入口请求到该工具的某个算子
    tool: object = None
    # 期望被请求到的算子 ID; 非空时逐个核对
    operator_ids: frozenset = frozenset()
    # 是否要求绘制期写入工具标识 (带预设的工具)
    needs_token: bool = False
    # 非空表示期望抛出包含该片段的异常, 用于自检
    must_raise: str = ""


@dataclass
class DriveResult:
    """一个入口的驱动结果"""

    # 被驱动的入口
    entry: Entry
    # 抛出的异常, None 表示正常返回
    error: object = None
    # 异常调用栈
    detail: str = ""
    # 本次产生的全部 layout 调用名
    calls: list = field(default_factory=list)


def check(label, condition, detail=""):
    """记录一条必须成立的不变量"""
    mark = "PASS" if condition else "FAIL"
    suffix = f" | {detail}" if detail else ""
    print(f"[{mark}] {label}{suffix}", flush=True)
    if not condition:
        FAILURES.append(label)


class RecordingLayout:
    """把调用转发给真实 layout 并记录调用名, 用来判断绘制是否真的产生了控件"""

    def __init__(self, layout, calls):
        """保存真实 layout 与记录列表"""
        # 用 object.__setattr__ 写入, 否则会被下面的 __setattr__ 转发给真实 layout
        object.__setattr__(self, "real", layout)
        object.__setattr__(self, "calls", calls)

    def __getattr__(self, name):
        target = getattr(self.real, name)
        if not callable(target):
            return target

        def call(*args, **kwargs):
            """记录调用名与实参并转发; 只有返回真实布局时才继续包装"""
            result = target(*args, **kwargs)
            self.calls.append((name, args))
            # 子布局继续记录; 算子属性代理等其它返回值原样交回, 否则无法赋值
            if isinstance(result, bpy.types.UILayout):
                return RecordingLayout(result, self.calls)
            return result

        return call

    def __setattr__(self, name, value):
        """属性写入转发给真实 layout"""
        setattr(self.real, name, value)


def report_regions(tag):
    """打印侧栏 UI 区域的宽度, 用来确认它确实展开了"""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "UI":
                    print(f"{tag} UI 区域宽度 {region.width}", flush=True)


def open_sidebar():
    """展开侧栏并强制重绘, 绘制回调会在重绘里被调用"""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.show_region_ui = True
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def make_probe_draw(category):
    """生成探针面板的 draw - Blender 要求回调恰好两个参数"""

    def draw(_self, _context):
        """记录自己被绘制"""
        PROBE_DREW.append(category)

    return draw


def make_recorded_draw(cls):
    """生成记录绘制结果的 draw - 只能有 self 与 context 两个参数"""
    original = cls.draw

    def draw(self, context):
        """完整执行原 draw 并记录结果, 不把异常抛回 Blender"""
        try:
            original(self, context)
        except Exception as error:
            SIDEBAR_RESULTS.append((cls.__name__, error, traceback.format_exc()))
            return
        SIDEBAR_RESULTS.append((cls.__name__, None, ""))

    return draw


def register_probes():
    """在候选分类里各注册一个探针面板"""
    for index, category in enumerate(PROBE_CATEGORIES):
        probe = type(
            f"QD_PT_probe_{index}",
            (bpy.types.Panel,),
            {
                "__doc__": "侧栏分类探针",
                "bl_idname": f"QD_PT_probe_{index}",
                "bl_label": f"PROBE {category}",
                "bl_space_type": "VIEW_3D",
                "bl_region_type": "UI",
                "bl_category": category,
                "draw": make_probe_draw(category),
            },
        )
        bpy.utils.register_class(probe)


def discover():
    """探测阶段: 找出侧栏当前激活的分类"""
    print("== 1. 探测侧栏激活的分类 ==", flush=True)
    try:
        register_probes()
        open_sidebar()
    except Exception:
        traceback.print_exc()
        check("探测阶段执行完成", False, "注册探针或重绘失败")
    report_regions("探测后")
    check("探针面板被绘制", bool(PROBE_DREW), f"绘制过的分类 {sorted(set(PROBE_DREW))}")
    # 返回 None 表示只执行一次; 返回数字会变成往返定时器
    return None


# --------------------------------------------------------------------------
# 绘制入口: 全部由工具声明推导, 不写死具体工具
# --------------------------------------------------------------------------


def panel_layout():
    """面板 draw 只用到 self.layout, 因此一个带 layout 的替身就够"""
    return lambda layout: SimpleNamespace(layout=layout)


def menu_context(context, tool_id):
    """菜单 draw 需要带工具标识的上下文; 工具标识走布局上下文成员"""
    from source.framework import presets

    return SimpleNamespace(**{presets.TOOL_MEMBER: tool_id}, scene=context.scene)


def tool_settings(tool):
    """取工具在场景里的参数组"""
    return getattr(bpy.context.scene.qd_settings, tool.tool_id)


def isolated_directory(tool_id, create=False):
    """替代 presets.preset_directory, 把预设目录指向仓库内的隔离目录"""
    target = PRESET_ROOT / tool_id
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def prepare_presets(tools):
    """换掉预设目录并放一个预设, 让菜单的列表分支也能被覆盖"""
    from source.framework import preset_files, presets

    original = presets.preset_directory
    presets.preset_directory = isolated_directory
    if PRESET_ROOT.exists():
        shutil.rmtree(PRESET_ROOT, ignore_errors=True)
    listed = next((tool for tool in tools if tool.presets), None)
    if listed is not None:
        # 空字典合法: 缺字段允许, 载入时保留界面现值
        directory = isolated_directory(listed.tool_id, create=True)
        envelope = preset_files.encode(listed.tool_id, {})
        preset_files.save_preset(directory, "listed", envelope, overwrite=True)
        print(f"隔离预设目录已就绪: {PRESET_ROOT}", flush=True)
    return original


def restore_presets(original):
    """还原预设目录并清掉隔离目录"""
    from source.framework import presets

    presets.preset_directory = original
    shutil.rmtree(PRESET_ROOT, ignore_errors=True)


def tool_operator_ids(tool):
    """列出工具声明的算子 ID"""
    return frozenset(cls.bl_idname for cls in tool.classes if issubclass(cls, bpy.types.Operator))


def class_entries(tool, base, kind):
    """把工具声明的某一类交互类转成入口"""
    as_self = panel_layout()
    entries = []
    for cls in tool.classes:
        if not issubclass(cls, base):
            continue
        entries.append(
            Entry(
                name=f"{tool.tool_id}/{kind}/{cls.__name__}",
                # 必须调包装前的方法: 否则这次驱动会被记成"Blender 画过这个面板"
                call=lambda layout, context, cls=cls, as_self=as_self: ORIGINAL_DRAWS.get(
                    cls, cls.draw
                )(as_self(layout), context),
                # 面板必须提供本工具的算子入口, 否则用户从界面上用不了这个工具
                tool=tool if kind == "panel" else None,
            )
        )
    return entries


def preset_entries(tool):
    """带预设的工具要驱动它的预设入口"""
    from source.framework import presets

    if not tool.presets:
        return []
    owner = tool_settings(tool)
    return [
        Entry(
            name=f"{tool.tool_id}/preset_row",
            call=lambda layout, _context: presets.draw_presets(layout, tool.tool_id, owner),
            needs_token=True,
        )
    ]


def framework_entries(tools):
    """框架自己的绘制入口: 预设菜单"""
    from source.framework import presets

    with_preset = next((tool for tool in tools if tool.presets), None)
    if with_preset is None:
        return []
    preset_ids = frozenset(
        cls.bl_idname for cls in presets.CLASSES if issubclass(cls, bpy.types.Operator)
    )

    def preset_menu(layout, context):
        """菜单绘制需要一个带工具标识的上下文, 标识来自绘制方"""
        menu = SimpleNamespace(layout=layout)
        presets.QD_MT_presets.draw(menu, menu_context(context, with_preset.tool_id))

    return [
        Entry(
            name="framework/预设菜单",
            call=preset_menu,
            operator_ids=preset_ids,
        )
    ]


def id_write_probe(tools):
    """自检用: 在绘制期写 ID 数据, 必须被 Blender 拒绝"""
    # 必须挑一个带场景参数组的工具: 没有参数组的工具没有可写的 ID 数据
    owner = next(tool_settings(tool) for tool in tools if tool.schema is not None)

    def call(_layout, _context):
        """写一个场景参数组的字段; 绘制回调里不允许这样做"""
        owner.preset_name = uuid.uuid4().hex

    return call


def build_entries(tools):
    """汇总要驱动的全部绘制入口"""
    entries = []
    for tool in tools:
        entries.extend(class_entries(tool, bpy.types.Panel, "panel"))
        entries.extend(class_entries(tool, bpy.types.Menu, "menu"))
        entries.extend(preset_entries(tool))
    entries.extend(framework_entries(tools))
    # 自检: 这条路径必须仍然禁止写 ID 数据, 否则整份测试的上下文不受限
    entries.append(
        Entry(
            name="自检/绘制期写 ID 数据被拒绝",
            call=id_write_probe(tools),
            minimum=0,
            must_raise="Writing to ID classes",
        )
    )
    return entries


def make_driver_draw(entries):
    """生成驱动面板的 draw - 在这里用真实 layout 与真实上下文驱动全部入口"""

    def draw(_self, context):
        """逐个驱动绘制入口, 只跑第一帧"""
        global DRIVEN
        if DRIVEN:
            return
        DRIVEN = True
        for entry in entries:
            calls = []
            result = DriveResult(entry, calls=calls)
            try:
                entry.call(RecordingLayout(_self.layout, calls), context)
            except Exception as error:
                result.error = error
                result.detail = traceback.format_exc()
            DRIVE_RESULTS.append(result)

    return draw


def verify():
    """装配与内容阶段: 注册插件, 让 Blender 画面板, 再驱动全部入口"""
    print("\n== 2. 装配检查: 由 Blender 绘制侧栏面板 ==", flush=True)
    from source import registration

    global TOOLS_SNAPSHOT
    tools = registration.TOOLS
    TOOLS_SNAPSHOT = tools
    category = PROBE_DREW[0] if PROBE_DREW else "Item"
    panels = [cls for tool in tools for cls in tool.classes if issubclass(cls, bpy.types.Panel)]
    # 顶层面板之间是并列关系: 由 Blender 自己的面板顺序决定, 不依赖任何父面板
    check(
        "工具面板都是顶层面板",
        all(not getattr(cls, "bl_parent_id", "") for cls in panels),
        str([(cls.__name__, getattr(cls, "bl_parent_id", "")) for cls in panels]),
    )
    # 临时把面板放进激活的分类; 绘制包装必须在注册之前套好
    for cls in panels:
        cls.bl_category = category
        ORIGINAL_DRAWS[cls] = cls.draw
        cls.draw = make_recorded_draw(cls)
    print(f"放入分类 {category!r} 的面板: {[cls.__name__ for cls in panels]}", flush=True)

    try:
        import source

        source.register()
    except Exception:
        traceback.print_exc()
        check("插件注册成功", False, "register() 抛错")
        return None
    check("插件注册成功", True)
    check("通用预设菜单已注册", hasattr(bpy.types, "QD_MT_presets"))
    check("通用预设算子已注册", hasattr(bpy.types, "QD_OT_preset_save"))
    check(
        "工具面板与场景参数组都在",
        all(
            hasattr(bpy.types, cls.__name__)
            for tool in tools
            for cls in tool.classes
            if issubclass(cls, bpy.types.Panel)
        ),
    )

    open_sidebar()
    report_regions("绘制后")

    print("\n== 3. 内容检查: 驱动全部绘制入口 ==", flush=True)
    global ORIGINAL_PRESET_DIRECTORY
    ORIGINAL_PRESET_DIRECTORY = prepare_presets(tools)
    entries = build_entries(tools)
    driver = type(
        "QD_PT_ui_driver",
        (bpy.types.Panel,),
        {
            "__doc__": "绘制入口驱动器",
            "bl_idname": "QD_PT_ui_driver",
            "bl_label": "UI DRIVER",
            "bl_space_type": "VIEW_3D",
            "bl_region_type": "UI",
            "bl_category": category,
            "draw": make_driver_draw(entries),
        },
    )
    bpy.utils.register_class(driver)
    print(f"待驱动入口 {len(entries)} 个", flush=True)
    open_sidebar()
    return None


def written_tokens(calls):
    """列出本入口通过 context_string_set 写进布局上下文的成员名"""
    return {args[0] for name, args in calls if name == "context_string_set" and args}


def requested_operators(calls):
    """列出本入口请求绘制的算子 ID"""
    return {args[0] for name, args in calls if name == "operator" and args}


def check_entry(result):
    """核对一个入口的驱动结果"""
    entry = result.entry
    calls = result.calls
    widgets = sum(1 for name, _args in calls if name in WIDGET_CALLS)
    if entry.must_raise:
        # 自检入口: 期望失败, 而且失败原因必须是绘制期限制
        hit = result.error is not None and entry.must_raise in str(result.error)
        check(entry.name, hit, f"实际 {result.error!r}")
        return
    check(
        f"{entry.name} 控件 {widgets}",
        result.error is None,
        "" if result.error is None else repr(result.error),
    )
    if result.error is not None:
        print(result.detail, flush=True)
        return
    check(
        f"{entry.name} 产生了控件",
        widgets >= entry.minimum,
        f"{widgets} 个, 期望不低于 {entry.minimum}",
    )
    operators = requested_operators(calls)
    if entry.tool is not None:
        own = tool_operator_ids(entry.tool)
        if own:
            check(
                f"{entry.name} 提供本工具算子入口",
                bool(operators & own),
                f"请求 {sorted(operators)} 本工具 {sorted(own)}",
            )
        else:
            # 只声明了面板的工具没有算子可入口, 控件数已在上一条核对, 这里不重复断言
            print(f"       {entry.name} 未声明算子, 跳过算子入口核对", flush=True)
    if entry.operator_ids:
        check(
            f"{entry.name} 请求到声明的全部算子",
            entry.operator_ids <= operators,
            f"缺 {sorted(entry.operator_ids - operators)}",
        )
    if entry.needs_token:
        from source.framework import presets

        check(
            f"{entry.name} 绘制期写入了工具标识",
            presets.TOOL_MEMBER in written_tokens(calls),
            f"写入 {sorted(written_tokens(calls))}",
        )


def check_sidebar():
    """核对 Blender 自己绘制的面板: 不抛异常, 默认收起的面板没被绘制, 其余面板都被绘制"""
    drawn = {name for name, _error, _detail in SIDEBAR_RESULTS}
    for name, error, detail in SIDEBAR_RESULTS:
        check(
            f"{name}.draw 由 Blender 绘制完成", error is None, "" if error is None else repr(error)
        )
        if detail:
            print(detail, flush=True)
    for tool in TOOLS_SNAPSHOT:
        for cls in tool.classes:
            if not issubclass(cls, bpy.types.Panel):
                continue
            closed = "DEFAULT_CLOSED" in set(getattr(cls, "bl_options", ()) or ())
            check(
                f"{cls.__name__} 的绘制情况与折叠状态一致",
                (cls.__name__ in drawn) == (not closed),
                f"被绘制 {cls.__name__ in drawn}, 默认收起 {closed}",
            )


def finish():
    """汇总结果: 定时器里无法给 Blender 传退出码, 失败时直接以非零状态结束进程"""
    print("\n== 4. 结果 ==", flush=True)
    check_sidebar()
    for result in DRIVE_RESULTS:
        check_entry(result)
    if not DRIVE_RESULTS:
        check("驱动面板被执行", False, "重绘后驱动面板没有运行")
    if ORIGINAL_PRESET_DIRECTORY is not None:
        restore_presets(ORIGINAL_PRESET_DIRECTORY)

    summary = f"失败 {len(FAILURES)} 项:" if FAILURES else "ALL PASS"
    print(f"\n{summary}", flush=True)
    for item in FAILURES:
        print(f"  - {item}", flush=True)
    sys.stdout.flush()
    if FAILURES:
        os._exit(1)
    bpy.ops.wm.quit_blender()
    return None


if bpy.app.background:
    # 后台模式没有绘制回调, 直接报错退出而不是给出假通过
    print("[FAIL] 本脚本需要真实界面, 请去掉 -b 运行", flush=True)
    raise SystemExit(1)

bpy.app.timers.register(discover, first_interval=1.0)
bpy.app.timers.register(verify, first_interval=2.5)
bpy.app.timers.register(finish, first_interval=5.0)
