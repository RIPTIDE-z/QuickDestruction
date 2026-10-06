"""工具默认值单元测试

测试目的:
    几个工具的默认值决定了新场景的开箱体验, 而且彼此有一致性要求:
    物理设置默认休眠, 计划预览默认显示, 游戏导出默认面向虚幻引擎且轴向缩放的默认值等于引擎预设,
    默认只允许 ASCII 名称, 预设地面足够大
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
from source.tools.Physics import settings as physics_settings
from source.tools.Simulation import constants as simulation_constants

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
    assert export_settings.SCHEMA.defaults()["ascii_names"] is True


def test_ground_is_large():
    """预设地面的水平半边长下限是 100 米, 相对资产尺寸的倍数是 30"""
    LOG.info(
        "ground half %s ratio %s",
        simulation_constants.GROUND_MIN_HALF,
        simulation_constants.GROUND_EXTENT_RATIO,
    )
    assert simulation_constants.GROUND_MIN_HALF == 100.0
    assert simulation_constants.GROUND_EXTENT_RATIO == 30.0
