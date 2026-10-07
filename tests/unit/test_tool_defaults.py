"""工具默认值单元测试

测试目的:
    几个工具的默认值决定了新场景的开箱体验, 而且彼此有一致性要求:
    物理设置默认休眠, 计划预览默认显示, 游戏导出默认面向虚幻引擎且轴向缩放的默认值等于引擎预设,
    默认只允许 ASCII 名称, 预设地面足够大, 烘焙默认 0 到 90 帧而不跟随场景,
    原物体切换的隐藏缩放默认 0.001 (虚幻引擎不接受 0 缩放), 待机 2 帧, 两个开关帧默认第 0 帧,
    生成碎块后默认隐藏源物体, 递归细分默认先切大碎块
    轴向缩放的默认值不会触发引擎联动回调, 两者不一致时新场景的面板会显示一个引擎却用另一套轴向导出
测试过程:
    直接读各工具的参数规格与常量, 不启动 Blender
运行方式 (仓库根目录):
    uv run pytest tests/unit/test_tool_defaults.py
"""

import logging

from source.tools.Drivers import settings as drivers_settings
from source.tools.GameExport import settings as export_settings
from source.tools.GameExport.constants import ENGINE_FIELDS, ENGINE_PRESETS
from source.tools.MeshFracture import settings as fracture_settings
from source.tools.Physics import settings as physics_settings
from source.tools.Rig import settings as rig_settings
from source.tools.Simulation import constants as simulation_constants
from source.tools.Simulation import settings as simulation_settings

LOG = logging.getLogger(__name__)


def test_physics_defaults_to_sleeping():
    """物理设置的受击前状态默认是休眠: 冲击能在相互接触的碎块之间传递"""
    default = physics_settings.SCHEMA.defaults()["activation"]
    LOG.info("activation default %s", default)
    assert default == "SLEEPING"


def test_plan_preview_shown_by_default():
    """计划预览默认显示"""
    assert drivers_settings.SCHEMA.defaults()["show_preview"] is True


def test_export_defaults_match_unreal_preset():
    """游戏导出默认面向虚幻引擎, 轴向与缩放的默认值逐项等于该引擎的预设"""
    defaults = export_settings.SCHEMA.defaults()
    LOG.info("engine default %s", defaults["engine"])
    assert defaults["engine"] == "UNREAL"
    preset = dict(zip(ENGINE_FIELDS, ENGINE_PRESETS["UNREAL"], strict=True))
    for key, value in preset.items():
        LOG.info("%s default %r preset %r", key, defaults[key], value)
        assert defaults[key] == value


def test_ascii_names_required_by_default():
    """默认只允许 ASCII 名称"""
    assert rig_settings.SCHEMA.defaults()["ascii_names"] is True


def test_ground_is_large():
    """预设地面的水平半边长下限是 100 米, 相对资产尺寸的倍数是 30"""
    LOG.info(
        "ground half %s ratio %s",
        simulation_constants.GROUND_MIN_HALF,
        simulation_constants.GROUND_EXTENT_RATIO,
    )
    assert simulation_constants.GROUND_MIN_HALF == 100.0
    assert simulation_constants.GROUND_EXTENT_RATIO == 30.0


def test_bake_range_defaults_to_0_90():
    """烘焙默认不跟随场景帧范围, 而是 0 到 90 帧: 第 0 帧是静止姿态, 引擎里动画也从 0 开始"""
    defaults = simulation_settings.SCHEMA.defaults()
    LOG.info(
        "scene range %s, %s to %s",
        defaults["use_scene_range"],
        defaults["frame_start"],
        defaults["frame_end"],
    )
    assert defaults["use_scene_range"] is False
    assert (defaults["frame_start"], defaults["frame_end"]) == (0, 90)


def test_swap_defaults():
    """原物体切换: 隐藏缩放默认 0.001 而不是 0; 待机动画默认 2 帧;
    破碎动画的两个开关帧默认第 0 帧, 即破碎动画一开始原物体就隐藏, 碎块就显示
    """
    defaults = rig_settings.SCHEMA.defaults()
    LOG.info(
        "hidden scale %s, idle %s, hide %s, show %s",
        defaults["hidden_scale"],
        defaults["idle_length"],
        defaults["intact_hide_frame"],
        defaults["shards_show_frame"],
    )
    assert defaults["hidden_scale"] == 0.001
    assert defaults["idle_length"] == 2
    assert defaults["intact_hide_frame"] == 0
    assert defaults["shards_show_frame"] == 0


def test_source_hidden_by_default():
    """生成碎块后默认隐藏源物体"""
    assert fracture_settings.SCHEMA.defaults()["hide_source"] is True


def test_split_priority_defaults_to_big_shards():
    """递归细分名额不够时默认先切大碎块"""
    assert fracture_settings.SCHEMA.defaults()["recursion_chance_select"] == "SIZE_MAX"
