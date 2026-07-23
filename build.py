#!/usr/bin/env python3
"""打包脚本：先结束运行中的实例，再用 PyInstaller 构建并替换 dist 目录。"""
import os
import shutil
import subprocess
import sys
import time


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
SPEC_FILE = os.path.join(PROJECT_DIR, "BilibiliMonitor.spec")
BUILD_DIR = os.path.join(PROJECT_DIR, "build")
DIST_DIR = os.path.join(PROJECT_DIR, "dist")
NEW_DIST_NAME = "bilibili-monitor"
NEW_DIST_PATH = os.path.join(DIST_DIR, NEW_DIST_NAME)
TARGET_DIST_DIR = r"D:\BI\bilibili-monitor-dist"
TARGET_DIST_OLD_DIR = r"D:\BI\bilibili-monitor-dist-old"
EXE_NAME = "bilibili-monitor.exe"


def terminate_running_instance():
    """结束正在运行的 bilibili-monitor.exe 进程。"""
    print("[Pack] 检查并结束运行中的进程...")
    try:
        result = subprocess.run(
            ["taskkill", "/F", "/IM", EXE_NAME, "/T"],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0:
            print(f"[Pack] 已结束进程: {EXE_NAME}")
        else:
            if "找不到进程" in result.stderr or "not found" in result.stderr.lower():
                print("[Pack] 没有运行中的实例")
            else:
                print(f"[Pack] taskkill 输出: {result.stderr.strip()}")
    except Exception as e:
        print(f"[Pack] 结束进程时出错: {e}")
    # 等待一下确保文件句柄释放
    time.sleep(1)


def clean_old_build():
    """清理上次的 build 和 dist 目录。"""
    print("[Pack] 清理旧构建目录...")
    for path in (BUILD_DIR, DIST_DIR):
        if os.path.exists(path):
            try:
                shutil.rmtree(path)
                print(f"[Pack] 已删除: {path}")
            except Exception as e:
                print(f"[Pack] 删除 {path} 失败: {e}")
                sys.exit(1)


def run_pyinstaller():
    """执行 PyInstaller 构建。"""
    print("[Pack] 开始 PyInstaller 构建...")
    cmd = [sys.executable, "-m", "PyInstaller", SPEC_FILE, "--clean", "--noconfirm"]
    result = subprocess.run(cmd, cwd=PROJECT_DIR)
    if result.returncode != 0:
        print("[Pack] PyInstaller 构建失败")
        sys.exit(1)
    print("[Pack] PyInstaller 构建完成")


def _force_remove(path: str):
    """尝试删除目录/文件，失败则尝试重命名；返回 None 表示目标已不存在或已重命名。"""
    if not os.path.exists(path):
        return None
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        print(f"[Pack] 已删除: {path}")
        return None
    except Exception as e:
        print(f"[Pack] 删除 {path} 失败: {e}")
        # 无法删除时重命名，避免阻塞新目录移动
        timestamp = time.strftime("%Y%m%d%H%M%S")
        renamed = f"{path}-locked-{timestamp}"
        try:
            shutil.move(path, renamed)
            print(f"[Pack] 已重命名为: {renamed}")
            return None
        except Exception as e2:
            print(f"[Pack] 重命名 {path} 也失败: {e2}")
            return path


def _restore_user_data(source_dir: str, target_dir: str):
    """从备份目录还原用户数据到新构建目录。"""
    for name in ("config.yaml", "data", "logs"):
        src = os.path.join(source_dir, name)
        dst = os.path.join(target_dir, name)
        if not os.path.exists(src):
            continue
        try:
            if os.path.isdir(src):
                if os.path.exists(dst):
                    shutil.rmtree(dst)
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            print(f"[Pack] 已还原用户数据: {name}")
        except Exception as e:
            print(f"[Pack] 还原 {name} 失败: {e}")


def _copy_internal_with_robocopy(src: str, dst: str):
    """使用 robocopy 复制 _internal 目录，避免 Windows 长路径问题。"""
    import subprocess

    os.makedirs(dst, exist_ok=True)
    # /MIR：镜像同步；/E：包含空目录；/R:3 /W:5：失败重试 3 次，间隔 5 秒；/MT:8：多线程
    cmd = [
        "robocopy",
        src,
        dst,
        "/MIR",
        "/R:3",
        "/W:5",
        "/MT:8",
        "/NP",
        "/NFL",
        "/NDL",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    # robocopy 退出码 0-7 通常表示成功（含文件被复制/跳过的正常情况）
    if result.returncode >= 8:
        raise RuntimeError(
            f"robocopy 复制 _internal 失败 (exit {result.returncode}): {result.stdout}\n{result.stderr}"
        )
    print(f"[Pack] robocopy 复制完成 (exit {result.returncode})")


def replace_dist():
    """用新构建的 dist 替换目标目录，并自动还原用户数据。

    为避免目标目录被占用导致整目录移动失败，采用只替换 exe 和 _internal
    的方式，并先备份/再还原 config.yaml、data、logs 等用户数据。
    """
    if not os.path.isdir(NEW_DIST_PATH):
        print(f"[Pack] 找不到新构建目录: {NEW_DIST_PATH}")
        sys.exit(1)

    print("[Pack] 替换目标发布目录...")

    # 清理旧的备份目录
    _force_remove(TARGET_DIST_OLD_DIR)
    os.makedirs(TARGET_DIST_OLD_DIR, exist_ok=True)

    # 备份当前目标目录中的用户数据
    for name in ("config.yaml", "data", "logs"):
        src = os.path.join(TARGET_DIST_DIR, name)
        dst = os.path.join(TARGET_DIST_OLD_DIR, name)
        if not os.path.exists(src):
            continue
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            print(f"[Pack] 已备份用户数据: {name}")
        except Exception as e:
            print(f"[Pack] 备份 {name} 失败: {e}")

    # 替换 exe 和 _internal
    for name in (EXE_NAME, "_internal"):
        dst = os.path.join(TARGET_DIST_DIR, name)
        leftover = _force_remove(dst)
        if leftover:
            print(f"[Pack] 错误: 无法清理 {dst}，请关闭占用该目录的程序后重试。")
            sys.exit(1)
        src = os.path.join(NEW_DIST_PATH, name)
        try:
            if name == "_internal" and os.path.isdir(src):
                # _internal 可能包含超长路径，shutil.copytree 在 Windows 上容易失败，
                # 改用 robocopy 保证完整复制。
                _copy_internal_with_robocopy(src, dst)
            elif os.path.isdir(src):
                shutil.copytree(src, dst)
            else:
                shutil.copy2(src, dst)
            print(f"[Pack] 已部署: {name}")
        except Exception as e:
            print(f"[Pack] 部署 {name} 失败: {e}")
            sys.exit(1)

    # 验证可执行文件确实在根目录
    exe_path = os.path.join(TARGET_DIST_DIR, EXE_NAME)
    if not os.path.isfile(exe_path):
        print(f"[Pack] 错误: 发布目录中找不到 {EXE_NAME}")
        sys.exit(1)

    # 验证 _internal 关键文件存在，防止复制不完整导致启动失败
    base_lib = os.path.join(TARGET_DIST_DIR, "_internal", "base_library.zip")
    if not os.path.isfile(base_lib):
        print(f"[Pack] 错误: _internal 中缺少 {base_lib}，复制可能不完整")
        sys.exit(1)
    print("[Pack] _internal 完整性检查通过")

    # 还原用户数据
    _restore_user_data(TARGET_DIST_OLD_DIR, TARGET_DIST_DIR)

    # 清理嵌套的构建目录（PyInstaller 有时会留下）
    nested = os.path.join(TARGET_DIST_DIR, NEW_DIST_NAME)
    if os.path.exists(nested):
        _force_remove(nested)


def main():
    terminate_running_instance()
    clean_old_build()
    run_pyinstaller()
    replace_dist()
    print("[Pack] 打包完成!")


if __name__ == "__main__":
    main()
