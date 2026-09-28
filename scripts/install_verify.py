"""安装验证 - 由 install.bat 在 Blender 后台调用, 判定扩展是否按刚打出的包完整安装并成功启用

Blender 自带的 extension install-file 出错时只打印信息, 退出码仍为 0, 所以安装结果由这里判定:
    1. 包里的每个文件都已安装且逐字节一致, 安装目录里没有包外的陈旧文件
    2. 扩展在偏好设置里已启用, 并且注册成功
运行方式:
    <Blender 可执行文件> -b --python-exit-code 1 --python scripts/install_verify.py -- <扩展包>
输出:
    每项检查一行 [PASS] 或 [FAIL], 任一项失败时以非零退出码结束
"""

import sys
import tomllib
import zipfile
from pathlib import Path

import addon_utils
import bpy

# 运行时生成的缓存, 不属于扩展包的内容
CACHE_PARTS = frozenset({"__pycache__"})


def check(label, condition, detail=""):
    """打印一项检查结果, 返回它是否通过"""
    print(
        f"[{'PASS' if condition else 'FAIL'}] {label}{f' | {detail}' if detail else ''}", flush=True
    )
    return condition


def _same(archive, root, name):
    """已安装的文件与包内内容逐字节一致; 缺失或无法读取都算不一致"""
    try:
        return (root / name).read_bytes() == archive.read(name)
    except OSError:
        return False


def compare_files(archive, root):
    """比较包内文件与安装目录, 返回 (缺失或内容不同的文件, 包外的多余文件)"""
    packaged = {name for name in archive.namelist() if not name.endswith("/")}
    different = sorted(name for name in packaged if not _same(archive, root, name))
    installed = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and not CACHE_PARTS & set(path.parts)
    }
    return different, sorted(installed - packaged)


def main():
    """读取包内清单, 核对安装目录与启用状态"""
    package = Path(sys.argv[sys.argv.index("--") + 1])
    with zipfile.ZipFile(package) as archive:
        manifest = tomllib.loads(archive.read("blender_manifest.toml").decode("utf-8"))
        extension_id = manifest["id"]
        root = Path(bpy.utils.user_resource("EXTENSIONS", path=f"user_default/{extension_id}"))
        different, extra = compare_files(archive, root)
    module = f"bl_ext.user_default.{extension_id}"
    enabled, loaded = addon_utils.check(module)
    results = [
        check("installed files match the package", not different, ", ".join(different[:5])),
        check("no stale files left in the install", not extra, ", ".join(extra[:5])),
        check("extension is enabled and registered", enabled and loaded, module),
    ]
    print(f"Installed {extension_id} {manifest['version']} at {root}", flush=True)
    if not all(results):
        sys.exit(1)


main()
