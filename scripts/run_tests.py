"""测试总入口 - 一条命令跑完单元测试, 再对每个给定的 Blender 依次跑全部 Blender 测试

运行方式 (仓库根目录):
    uv run python scripts/run_tests.py <Blender 可执行文件> [<另一个 Blender 可执行文件> ...]
选项:
    --skip-unit   不跑单元测试
    --skip-ui     不跑需要真实界面的测试 (会短暂弹出 Blender 窗口)
输出:
    每个测试一行 [PASS] 或 [FAIL] 与耗时, 失败时再列出失败的判据与末尾几行输出, 最后是汇总
    只要有一项失败, 退出码就是 1
"""

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests" / "blender"
# 后台运行的 Blender 测试
BACKGROUND_TESTS = (
    "verify_fracture.py",
    "verify_inputs.py",
    "verify_operator.py",
    "verify_dynamics.py",
    "verify_export.py",
)
# 需要真实界面的 Blender 测试, 不能加 -b
UI_TESTS = ("verify_ui_draw.py",)
# 单个测试的时间上限, 单位秒
TIMEOUT = 900


def run(label, command, marker="ALL PASS"):
    """运行一条命令并返回是否通过; 通过要求退出码为 0 且输出里有 marker"""
    started = time.perf_counter()
    try:
        done = subprocess.run(
            command,
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT,
            check=False,
        )
        output = done.stdout + done.stderr
        passed = done.returncode == 0 and marker in output
    except subprocess.TimeoutExpired:
        output, passed = f"timed out after {TIMEOUT} s", False
    elapsed = time.perf_counter() - started
    print(f"[{'PASS' if passed else 'FAIL'}] {label} ({elapsed:.1f} s)", flush=True)
    if not passed:
        failed = [line for line in output.splitlines() if line.startswith("[FAIL]")]
        for line in failed or output.splitlines()[-15:]:
            print(f"    {line}", flush=True)
    return passed


def blender_command(blender, script, *, background):
    """Blender 的命令行: 出厂设置, Python 出错时以非零状态退出

    界面测试要发真实的键盘事件, 需要开启事件模拟
    """
    command = [blender, "--factory-startup", "--python-exit-code", "1", "--python", str(script)]
    if background:
        return [command[0], "-b", *command[1:]]
    return [command[0], "--enable-event-simulate", *command[1:]]


def main(argv):
    """解析参数并依次运行, 返回退出码"""
    flags = {arg for arg in argv if arg.startswith("--")}
    blenders = [arg for arg in argv if not arg.startswith("--")]
    if not blenders and "--skip-unit" in flags:
        print("nothing to run: pass a Blender executable or drop --skip-unit")
        return 1
    results = []
    if "--skip-unit" not in flags:
        results.append(run("unit tests", [sys.executable, "-m", "pytest", "-q"], marker=" passed"))
    for blender in blenders:
        if not Path(blender).is_file():
            print(f"[FAIL] Blender not found: {blender}")
            results.append(False)
            continue
        name = Path(blender).parent.name
        for test in BACKGROUND_TESTS:
            command = blender_command(blender, TESTS / test, background=True)
            results.append(run(f"{name}: {test}", command))
        if "--skip-ui" not in flags:
            for test in UI_TESTS:
                command = blender_command(blender, TESTS / test, background=False)
                results.append(run(f"{name}: {test}", command))
    print(f"{sum(results)} of {len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
