"""QuickDestruction 算子与注册测试 - 从用户入口走完整条路径

测试目的:
    1. 生成算子: 读取场景里的参数, 成功时生成并选中碎块, 失败时报出可翻译的错误并保持场景不变,
       交互调用时干净的网格直接切割 (有问题的网格弹确认框需要真实窗口, 在 verify_ui_draw.py 里测)
    2. 预设算子: 保存, 覆盖保护, 载入, 删除, 坏文件与不合规文件的报告, 失败时参数保持不变
    3. 注册: 注销后干净, 重复注册被拒绝, 任一步失败都整体回滚, 不合规的工具声明在改动 Blender 之前被拒绝
测试过程:
    在后台 Blender 里注册插件, 用 bpy.ops 调用算子, 用 RuntimeError 的文字检查报告;
    预设目录换成临时目录; 每条判据输出 [PASS] 或 [FAIL], 有失败时以非零状态结束
运行方式 (仓库根目录, 需要 -b 后台模式):
    <Blender 可执行文件> -b --factory-startup --python-exit-code 1 --python tests\\blender\\verify_operator.py
实测环境: Blender 5.2.2 (Python 3.13) 与 Blender 4.2.23 (Python 3.11)
"""

import json
import sys
import tempfile
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
import harness
from harness import check, cube, reset, seeds, select

from source import registration
from source.framework import preset_files, presets
from source.framework.contract import ToolSpec
from source.tools.MeshFracture import interaction
from source.tools.MeshFracture.settings import SCHEMA

TOOL = "mesh_fracture"
PRESET_ROOT = Path(tempfile.mkdtemp(prefix="qd_operator_test_"))


def isolated_directory(tool_id, create=False):
    """替代 presets.preset_directory, 把预设目录指向临时目录"""
    target = PRESET_ROOT / tool_id
    if create:
        target.mkdir(parents=True, exist_ok=True)
    return target


def settings():
    """当前场景里 MeshFracture 的参数组"""
    return bpy.context.scene.qd_settings.mesh_fracture


def fail_text(call):
    """调用算子, 期望它失败; 返回报告文字, 没有失败时返回 None"""
    try:
        call()
    except RuntimeError as error:
        return str(error)
    return None


def shards_in_scene():
    """场景里带碎块标记的物体"""
    return [obj for obj in bpy.data.objects if obj.get("_qd_source")]


def prepare_scene(seed_count=6):
    """一个立方体加子物体种子, 只选中立方体; 参数先回到默认值, 再用一组小而快的取值"""
    reset()
    source = cube("Src")
    seeds(source, seed_count, 0.9, 3)
    select([source])
    config = settings()
    # 参数存在场景里, 清空物体不会重置它, 先回到默认值, 场景之间才互不影响
    for key, value in SCHEMA.defaults().items():
        setattr(config, key, value)
    config.source = {"VERT_CHILD"}
    config.source_noise = 0.0
    config.margin = 0.0
    config.recursion = 0
    config.source_limit = 0
    config.collection_name = ""
    return source


def case_generate_success():
    """生成算子: 读取场景参数, 生成碎块并只选中它们, 活动物体是第一块"""
    source = prepare_scene(6)
    settings().source_limit = 4
    result = bpy.ops.qd_mesh_fracture.generate("EXEC_DEFAULT")
    shards = shards_in_scene()
    check("算子完成", result == {"FINISHED"}, str(result))
    check("碎块数来自场景参数的首次切割上限", len(shards) == 4, str(len(shards)))
    selected = {obj.name for obj in bpy.context.selected_objects}
    check("只选中碎块", selected == {obj.name for obj in shards}, str(sorted(selected)[:3]))
    check("活动物体是碎块", bpy.context.view_layer.objects.active in shards)
    check("源物体保留", "Src" in bpy.data.objects and source.name in bpy.data.objects)


def case_generate_failures():
    """生成算子失败: 报可翻译的错误, 不留下数据, 选择集不变; 意外错误也走同一报告格式"""
    prepare_scene(6)
    select([])
    before = len(bpy.data.objects)
    text = fail_text(lambda: bpy.ops.qd_mesh_fracture.generate("EXEC_DEFAULT"))
    check(
        "没有选中物体时报错",
        text is not None and "Generate Fragments failed" in text and "Select editable mesh" in text,
        str(text),
    )
    check("失败后没有新增物体", len(bpy.data.objects) == before)

    prepare_scene(6)
    settings().material_index = 5
    text = fail_text(lambda: bpy.ops.qd_mesh_fracture.generate("EXEC_DEFAULT"))
    check("材质槽不存在时报错", text is not None and "Material slot 5" in text, str(text))
    check("失败后选择集仍是源物体", [o.name for o in bpy.context.selected_objects] == ["Src"])

    prepare_scene(6)
    original = interaction.generate_fragments

    def boom(*_args, **_kwargs):
        """模拟阶段 API 里的意外错误"""
        raise RuntimeError("injected failure")

    interaction.generate_fragments = boom
    try:
        text = fail_text(lambda: bpy.ops.qd_mesh_fracture.generate("EXEC_DEFAULT"))
    finally:
        interaction.generate_fragments = original
    check(
        "意外错误也按同一格式报告",
        text is not None and "Generate Fragments failed: injected failure" in text,
        str(text),
    )


def case_generate_invoke():
    """交互调用 (INVOKE_DEFAULT): 干净的网格直接切割; 有问题的网格弹确认框, 需要真实窗口, 见 verify_ui_draw.py"""
    prepare_scene(4)
    settings().source_limit = 4
    result = bpy.ops.qd_mesh_fracture.generate("INVOKE_DEFAULT")
    check("干净的网格直接切割", result == {"FINISHED"} and len(shards_in_scene()) == 4, str(result))


def case_language():
    """中文界面下失败报告也是中文"""
    prepare_scene(6)
    select([])
    view = bpy.context.preferences.view
    old = (view.language, view.use_translate_reports)
    view.language, view.use_translate_reports = "zh_HANS", True
    try:
        text = fail_text(lambda: bpy.ops.qd_mesh_fracture.generate("EXEC_DEFAULT"))
    finally:
        view.language, view.use_translate_reports = old
    check("中文报告", text is not None and "生成碎块失败" in text, str(text))


def write_preset(name, data):
    """直接往预设目录写一个文件"""
    directory = isolated_directory(TOOL, create=True)
    (directory / f"{name}.json").write_text(
        data if isinstance(data, str) else json.dumps(data), encoding="utf-8"
    )


def case_presets():
    """预设: 保存, 覆盖保护, 载入, 删除, 名称记录"""
    prepare_scene()
    config = settings()
    config.margin, config.recursion_clamp = 0.02, 77
    result = bpy.ops.qd.preset_save("EXEC_DEFAULT", tool_id=TOOL, name="mine")
    path = isolated_directory(TOOL) / "mine.json"
    check("保存成功并写出文件", result == {"FINISHED"} and path.is_file())
    check("记住当前预设名", config.preset_name == "mine")
    check(
        "预设文件是带信封的 JSON",
        json.loads(path.read_text(encoding="utf-8"))["tool_id"] == TOOL,
    )
    text = fail_text(lambda: bpy.ops.qd.preset_save("EXEC_DEFAULT", tool_id=TOOL, name="mine"))
    check("不覆盖时拒绝同名预设", text is not None and "already exists" in text, str(text))
    result = bpy.ops.qd.preset_save("EXEC_DEFAULT", tool_id=TOOL, name="mine", overwrite=True)
    check("勾选覆盖后可以保存", result == {"FINISHED"})

    config.margin, config.recursion_clamp = 0.5, 5
    result = bpy.ops.qd.preset_load("EXEC_DEFAULT", tool_id=TOOL, name="mine")
    check(
        "载入后参数恢复",
        result == {"FINISHED"}
        and abs(config.margin - 0.02) < 1e-6
        and config.recursion_clamp == 77,
        f"{config.margin} {config.recursion_clamp}",
    )

    result = bpy.ops.qd.preset_remove("EXEC_DEFAULT", tool_id=TOOL, name="mine")
    check(
        "删除成功并清空当前预设名",
        result == {"FINISHED"} and not path.exists() and config.preset_name == "",
    )


def case_preset_errors():
    """坏预设: 各种错误都有可翻译的报告, 载入失败时参数保持不变"""
    prepare_scene()
    config = settings()
    config.margin = 0.03
    write_preset("garbage", "{not json")
    write_preset("binary", "")
    (isolated_directory(TOOL) / "binary.json").write_bytes(b"\xff\xfe\x00\x01")
    write_preset("unknown_field", preset_files.encode(TOOL, {"margin": 0.4, "no_such_field": 1}))
    write_preset("other_tool", preset_files.encode("other", {}))
    write_preset(
        "bad_value", preset_files.encode(TOOL, {"margin": 0.4, "recursion_source_limit": 1})
    )
    expectations = (
        ("garbage", "format is invalid"),
        ("binary", "format is invalid"),
        ("missing", "could not be read"),
        ("unknown_field", "Invalid parameter"),
        ("other_tool", "belongs to another tool"),
        ("bad_value", "Invalid parameter"),
    )
    for name, message in expectations:
        text = fail_text(
            lambda name=name: bpy.ops.qd.preset_load("EXEC_DEFAULT", tool_id=TOOL, name=name)
        )
        check(f"载入 {name}", text is not None and message in text, str(text))
    check("失败后参数保持不变", abs(config.margin - 0.03) < 1e-6, str(config.margin))
    text = fail_text(lambda: bpy.ops.qd.preset_remove("EXEC_DEFAULT", tool_id=TOOL, name="missing"))
    check("删除不存在的预设", text is not None and "could not be removed" in text, str(text))
    text = fail_text(
        lambda: bpy.ops.qd.preset_load("EXEC_DEFAULT", tool_id="no_such_tool", name="x")
    )
    check("目标工具不存在", text is not None and "unavailable" in text, str(text))

    view = bpy.context.preferences.view
    old = (view.language, view.use_translate_reports)
    view.language, view.use_translate_reports = "zh_HANS", True
    try:
        text = fail_text(
            lambda: bpy.ops.qd.preset_load("EXEC_DEFAULT", tool_id=TOOL, name="garbage")
        )
    finally:
        view.language, view.use_translate_reports = old
    check("中文报告", text is not None and "预设格式无效" in text, str(text))


def registered():
    """插件的类与场景参数是否处于注册状态"""
    return "qd_settings" in bpy.types.Scene.bl_rna.properties and bool(presets.SCHEMAS)


def case_registration():
    """注册: 注销后干净, 重复注册被拒绝, 再次注册可用"""
    check("开始时已注册", registered())
    registration.unregister()
    check("注销后场景参数与预设登记都清空", not registered())
    check("注销后算子类已移除", not hasattr(bpy.types, "QD_OT_preset_save"))
    registration.register()
    check("再次注册可用", registered())
    try:
        registration.register()
        check("重复注册被拒绝", False)
    except RuntimeError as error:
        check("重复注册被拒绝", "already registered" in str(error), str(error))
    check("重复注册没有破坏已有注册", registered())


def case_registration_rollback():
    """任一步失败都整体回滚: 场景参数, 预设登记与翻译都不残留, 之后仍能正常注册"""
    registration.unregister()
    original = bpy.utils.register_class
    calls = []

    def flaky(cls):
        """第 8 个类注册时失败"""
        calls.append(cls)
        if len(calls) == 8:
            raise RuntimeError("injected registration failure")
        return original(cls)

    bpy.utils.register_class = flaky
    try:
        registration.register()
        check("注册失败会抛出", False)
    except RuntimeError as error:
        check("注册失败会抛出", "injected" in str(error), str(error))
    finally:
        bpy.utils.register_class = original
    check("回滚后没有残留", not registered() and not hasattr(bpy.types, "QD_OT_preset_save"))
    registration.register()
    check("回滚后可以重新注册", registered())


def case_registration_checks():
    """不合规的工具声明在改动 Blender 之前被拒绝, 报错指明是类名还是算子 ID"""

    class QD_BAD_OT_thing(bpy.types.Operator):
        bl_idname = "wrong.thing"
        bl_label = "Bad"

    class NotPrefixed(bpy.types.Operator):
        bl_idname = "qd_bad.thing"
        bl_label = "Bad"

    registration.unregister()
    original = registration.TOOLS
    try:
        for label, classes, message in (
            ("算子 ID 前缀不对", (QD_BAD_OT_thing,), "Operator wrong.thing"),
            ("类名前缀不对", (NotPrefixed,), "Class NotPrefixed"),
        ):
            bad = ToolSpec(tool_id="bad", locales=Path(), classes=classes)
            registration.TOOLS = (*original, bad)
            try:
                registration.register()
                check(label, False, "没有报错")
            except ValueError as error:
                check(label, message in str(error), str(error))
            check(f"{label}: 报错之前没有改动 Blender", not registered())
        registration.TOOLS = (*original, original[0])
        try:
            registration.register()
            check("重复的工具标识被拒绝", False)
        except ValueError as error:
            check("重复的工具标识被拒绝", "Duplicate tool_id" in str(error), str(error))
    finally:
        registration.TOOLS = original
    registration.register()
    check("恢复注册", registered())


def main():
    """注册插件, 预设目录换成临时目录, 运行全部场景, 最后清理"""
    registration.register()
    presets.preset_directory = isolated_directory
    harness.run_cases(CASES)


CASES = (
    case_generate_success,
    case_generate_failures,
    case_generate_invoke,
    case_language,
    case_presets,
    case_preset_errors,
    case_registration,
    case_registration_rollback,
    case_registration_checks,
)

main()
