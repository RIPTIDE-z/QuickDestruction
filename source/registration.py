"""扩展装配 - 注册顺序与原入口保持一致"""
import bpy
from bpy.app.handlers import persistent
from bpy.props import PointerProperty

from .CellFracture import panel, presets
from .CellFracture.constants import CONTEXT
from .CellFracture.operators import OBJECT_OT_add_quick_destruction_objects
from .CellFracture.properties import CellFractureSettings
from .i18n import TRANSLATIONS_DOMAIN, translations_dict
from .properties import QDSettings


@persistent
def clear_sessions(_unused):
    """加载新文件前清空对话框会话, 避免旧会话指向已失效的场景"""
    presets.SESSIONS.clear()


def menu_func(self, context):
    """3D 视图 物体 -> 快速效果 里的入口, 与上游保持同样的分隔线写法"""
    layout = self.layout
    layout.separator()
    layout.operator("object.add_quick_destruction_objects",
                    text="Quick Destruction Effect", text_ctxt=CONTEXT)


# 属性依赖先注册 - Scene 指针之后才接入交互类
CLASSES = (
    OBJECT_OT_add_quick_destruction_objects,
    *presets.CLASSES,
    panel.QD_PT_settings,
)


def register():
    """按属性组与交互类的依赖顺序显式注册"""
    bpy.app.translations.register(TRANSLATIONS_DOMAIN, translations_dict)
    bpy.utils.register_class(CellFractureSettings)
    bpy.utils.register_class(QDSettings)
    bpy.types.Scene.qd_settings = PointerProperty(type=QDSettings)
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_object_quick_effects.append(menu_func)
    bpy.app.handlers.load_pre.append(clear_sessions)


def unregister():
    """先移除交互引用再逆序注销属性组"""
    bpy.app.handlers.load_pre.remove(clear_sessions)
    bpy.types.VIEW3D_MT_object_quick_effects.remove(menu_func)
    presets.SESSIONS.clear()
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
    del bpy.types.Scene.qd_settings
    bpy.utils.unregister_class(QDSettings)
    bpy.utils.unregister_class(CellFractureSettings)
    bpy.app.translations.unregister(TRANSLATIONS_DOMAIN)
