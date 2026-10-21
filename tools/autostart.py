"""管理 Codex Gauge 的开机自启。

用法：
    python tools/autostart.py status      查看当前状态
    python tools/autostart.py install     安装开机自启
    python tools/autostart.py uninstall   取消开机自启

两种落地方式，install 会自动选可用的那个：

1. 启动文件夹（首选）
   %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Codex Gauge.bat
   用户能直接看到、直接删。但部分机器（装了安全软件或受控文件夹访问）
   会拒绝对这个目录写入 .bat，实测本机就写不进去。

2. 注册表 Run 键（备选）
   HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run → CodexGauge
   不需要文件系统权限，一定能写。缺点是藏在注册表里，
   所以这里提供 uninstall 子命令，不用你自己去翻。

两种方式都只是"开机把浮窗叫起来"，窗口显不显示由 follow_codex 决定。
"""

import os
import pathlib
import sys
import winreg

APP_NAME = "Codex Gauge"
ROOT = pathlib.Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "启动 Codex 额度浮窗.bat"
SCRIPT = ROOT / "src" / "codex_gauge.py"
STARTUP = pathlib.Path(os.environ["APPDATA"]) / (
    "Microsoft/Windows/Start Menu/Programs/Startup"
)
STARTUP_BAT = STARTUP / (APP_NAME + ".bat")

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "CodexGauge"


# ---------------------------------------------------------------- 启动文件夹


def startup_body() -> str:
    # /min 让 cmd 窗口最小化，减少开机时的视觉干扰
    return '@echo off\r\nstart "" /min "%s"\r\nexit\r\n' % LAUNCHER


def startup_install() -> bool:
    try:
        # cmd.exe 按系统 ANSI 代码页解析 .bat，必须用 GBK 写，
        # 否则中文路径会乱码，开机时报「找不到文件」
        STARTUP_BAT.write_bytes(startup_body().encode("gbk"))
        return True
    except Exception:
        return False


def startup_present() -> bool:
    return STARTUP_BAT.exists()


def startup_remove() -> bool:
    try:
        if STARTUP_BAT.exists():
            STARTUP_BAT.unlink()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 注册表


def reg_command() -> str:
    """优先用 pythonw 启动，避免开机弹控制台。"""
    exe = pathlib.Path(sys.executable)
    pyw = exe.with_name("pythonw.exe")
    if pyw.exists():
        exe = pyw
    return '"%s" "%s"' % (exe, SCRIPT)


def reg_install() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, reg_command())
        return True
    except Exception:
        return False


def reg_read():
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_QUERY_VALUE
        ) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return value
    except FileNotFoundError:
        return None
    except Exception:
        return None


def reg_remove() -> bool:
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.DeleteValue(key, VALUE_NAME)
        return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


# ---------------------------------------------------------------- 命令


def cmd_status() -> int:
    print("启动文件夹脚本: %s" % STARTUP_BAT)
    print("  存在        : %s" % ("是" if startup_present() else "否"))
    reg = reg_read()
    print("注册表 Run 项  : HKCU\\%s\\%s" % (RUN_KEY, VALUE_NAME))
    print("  值          : %s" % (reg if reg else "未设置"))
    if startup_present() or reg:
        print("\n状态: 已启用开机自启")
        return 0
    print("\n状态: 未启用")
    return 1


def cmd_install() -> int:
    if not LAUNCHER.exists():
        print("[错误] 找不到启动器: %s" % LAUNCHER)
        return 2
    if not SCRIPT.exists():
        print("[错误] 找不到主程序: %s" % SCRIPT)
        return 2

    if startup_install():
        print("已安装: 启动文件夹 → %s" % STARTUP_BAT.name)
        print("（可在「启动」文件夹里直接看到并删除）")
    elif reg_install():
        print("启动文件夹写入被系统拦截，已改用注册表。")
        print("已安装: HKCU\\%s" % RUN_KEY)
        print("  值: %s" % reg_command())
        print("取消方式: python tools/autostart.py uninstall")
    else:
        print("[失败] 两种方式都不可用，请手动把启动器放进启动文件夹。")
        print("  启动文件夹: %s" % STARTUP)
        return 1

    print("\n下次开机会静默启动；Codex 没开时窗口自动隐藏。")
    return 0


def cmd_uninstall() -> int:
    removed = []
    if startup_remove() and not startup_present():
        removed.append("启动文件夹脚本")
    if reg_remove():
        removed.append("注册表 Run 项")
    if removed:
        print("已取消开机自启（清理了 %s）" % "、".join(removed))
    else:
        print("本来就没装，无需操作。")
    return 0


def main() -> int:
    action = sys.argv[1] if len(sys.argv) > 1 else "status"
    table = {"status": cmd_status, "install": cmd_install, "uninstall": cmd_uninstall}
    if action not in table:
        print(__doc__)
        return 2
    return table[action]()


if __name__ == "__main__":
    sys.exit(main())
