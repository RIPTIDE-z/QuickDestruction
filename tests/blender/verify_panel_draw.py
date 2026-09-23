"""面板绘制验证 - 在真实界面里画一次插件面板, 捕捉绘制回调里的异常

测试目的:
    1. 面板 draw 抛异常时 Blender 只画出标题栏, 内容为空, 并且只在控制台留下
       "Python script error in <面板>.draw"; 参数, 按钮与提示会整体消失
    2. 绘制回调有上下文限制: 读 ID 数据可以, 写 ID 数据会抛
       "Writing to ID classes in this context is not allowed"
    3. 这两类缺陷只有在真实界面绘制时才会暴露: -b 后台模式没有绘制回调,
       普通的函数级单测又能拿到不受限制的上下文, 都覆盖不到

测试过程:
    1. 探测阶段: 在每个候选侧栏分类里各注册一个探针面板, 展开侧栏并强制重绘,
       记录真正被绘制的分类 -- 启动配置不同, 侧栏激活的页也不同
    2. 校验阶段: 把插件的父面板与各工具的侧栏面板临时放进该分类, 并给它们的 draw
       套一层记录包装 (必须在注册之前: Blender 在注册时就取走 draw 回调),
       然后注册插件并强制重绘, 逐个面板确认 draw 完整执行
    3. 顺带确认会话令牌没有落在场景参数组上, 也就是绘制期没有写 ID 数据
    4. 逐项输出 PASS / FAIL, 结尾汇总; 有失败时以非零退出码结束

运行方式 (仓库根目录, 不能加 -b, 需要真实界面):
    <Blender 可执行文件> --factory-startup --python-exit-code 1 --python tests\\blender\\verify_panel_draw.py

实测环境: Blender 5.2.2 (Python 3.13) 与 Blender 4.2.23 (Python 3.11)
"""

import os
import sys
import traceback
from pathlib import Path

import bpy

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

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

FAILURES = []
# 探针面板被绘制时记录自己所在的分类
PROBE_DREW = []
# 父面板与各工具面板的绘制结果: (类名, 异常, 调用栈)
DRAW_RESULTS = []


def check(label, condition, detail=""):
    """记录一条必须成立的不变量"""
    mark = "PASS" if condition else "FAIL"
    suffix = f" | {detail}" if detail else ""
    print(f"[{mark}] {label}{suffix}", flush=True)
    if not condition:
        FAILURES.append(label)


def report_regions(tag):
    """打印视图区各区域的宽度, 用来确认侧栏确实展开了"""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for region in area.regions:
                if region.type == "UI":
                    print(f"{tag} UI 区域宽度 {region.width}", flush=True)


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
            DRAW_RESULTS.append((cls.__name__, error, traceback.format_exc()))
            return
        DRAW_RESULTS.append((cls.__name__, None, ""))

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


def open_sidebar():
    """展开侧栏并强制重绘, 面板的 draw 会在重绘里被调用"""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            for space in area.spaces:
                if space.type == "VIEW_3D":
                    space.show_region_ui = True
    bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=1)


def tool_panels():
    """列出各工具要注册的侧栏面板类"""
    from source.tool_registry import ordered_tools

    panels = []
    for _tool in ordered_tools():
        for cls in _tool.classes():
            if isinstance(cls, type) and issubclass(cls, bpy.types.Panel):
                panels.append(cls)
    return panels


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


def verify():
    """校验阶段: 在激活的分类里把插件的面板画一遍"""
    print("\n== 2. 插件面板绘制 ==", flush=True)
    from source import panel as root_panel

    category = PROBE_DREW[0] if PROBE_DREW else "Item"
    panels = [root_panel.QD_PT_main, *tool_panels()]
    # 临时把面板放进激活的分类; 绘制包装必须在注册之前套好
    for cls in panels:
        cls.bl_category = category
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

    group = bpy.context.scene.qd_settings.mesh_fracture
    check("场景参数组带当前预设名", hasattr(group, "preset_name"))
    check(
        "会话令牌不落在场景参数组上",
        not hasattr(group, "preset_session"),
        "参数组不应再有绘制期要写的临时字段",
    )

    open_sidebar()
    report_regions("绘制后")
    return None


def finish():
    """汇总结果: 定时器里无法给 Blender 传退出码, 失败时直接以非零状态结束进程"""
    print("\n== 3. 绘制结果 ==", flush=True)
    for name, error, detail in DRAW_RESULTS:
        check(f"{name}.draw 完整执行", error is None, "" if error is None else repr(error))
        if detail:
            print(detail, flush=True)
    if not DRAW_RESULTS:
        check("面板 draw 被调用", False, "重绘后仍没有面板被绘制")
    if not bpy.app.background:
        print("\n提示: 本脚本需要真实界面, 用 -b 运行不会触发绘制", flush=True)

    print(f"\n{'失败 ' + str(len(FAILURES)) + ' 项:' if FAILURES else 'ALL PASS'}", flush=True)
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
bpy.app.timers.register(finish, first_interval=4.5)
