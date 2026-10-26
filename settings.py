"""keysnip settings: one JSON file for all platforms, plus per-OS autostart handling.

Settings file: keysnip.json next to this script (override with KEYSNIP_CONFIG).
"""
import json
import os
import platform
import plistlib
import sys
from pathlib import Path

SYSTEM = platform.system()
APP_DIR = Path(__file__).resolve().parent
SCRIPT = APP_DIR / "keysnip.py"
CONFIG_PATH = Path(os.environ.get("KEYSNIP_CONFIG", APP_DIR / "keysnip.json"))

DEFAULTS = {
    "autostart": False,       # launch the hotkey listener at login (Windows, macOS, Linux)
    "save_dir": None,         # None = ~/Pictures/keysnip (KEYSNIP_DIR env var still wins)
    "save_to_disk": False,    # True = also keep a PNG in save_dir; False = clipboard only
    "hotkey_window_seconds": 1.5,  # max time between G and S
}


def load():
    cfg = dict(DEFAULTS)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
        return cfg
    try:
        cfg.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        print(f"keysnip: ignoring unreadable config {CONFIG_PATH}: {exc}", file=sys.stderr)
    return cfg


def _launcher():
    """Interpreter used at login; pythonw avoids a console window on Windows."""
    exe = Path(sys.executable)
    if SYSTEM == "Windows":
        pyw = exe.with_name("pythonw.exe")
        if pyw.exists():
            exe = pyw
    return str(exe)


def _win_command():
    return f'"{_launcher()}" "{SCRIPT}"'


_WIN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_MAC_PLIST = Path.home() / "Library" / "LaunchAgents" / "com.keysnip.listener.plist"
_LINUX_DESKTOP = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autostart" / "keysnip.desktop"


def is_enabled():
    if SYSTEM == "Windows":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_KEY) as k:
                winreg.QueryValueEx(k, "keysnip")
            return True
        except FileNotFoundError:
            return False
    if SYSTEM == "Darwin":
        return _MAC_PLIST.exists()
    return _LINUX_DESKTOP.exists()


def enable():
    if SYSTEM == "Windows":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "keysnip", 0, winreg.REG_SZ, _win_command())
    elif SYSTEM == "Darwin":
        _MAC_PLIST.parent.mkdir(parents=True, exist_ok=True)
        with open(_MAC_PLIST, "wb") as f:
            plistlib.dump({
                "Label": "com.keysnip.listener",
                "ProgramArguments": [_launcher(), str(SCRIPT)],
                "RunAtLoad": True,
                "KeepAlive": False,
            }, f)
    else:
        _LINUX_DESKTOP.parent.mkdir(parents=True, exist_ok=True)
        _LINUX_DESKTOP.write_text(
            "[Desktop Entry]\nType=Application\nName=keysnip\n"
            f'Exec="{_launcher()}" "{SCRIPT}"\nX-GNOME-Autostart-enabled=true\n',
            encoding="utf-8")


def disable():
    if SYSTEM == "Windows":
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _WIN_KEY, 0, winreg.KEY_SET_VALUE) as k:
                winreg.DeleteValue(k, "keysnip")
        except FileNotFoundError:
            pass
    else:
        (_MAC_PLIST if SYSTEM == "Darwin" else _LINUX_DESKTOP).unlink(missing_ok=True)


def apply(cfg):
    """Make the OS autostart entry match cfg['autostart']; returns a status message."""
    want = bool(cfg.get("autostart"))
    if want == is_enabled():
        return f"autostart already {'enabled' if want else 'disabled'}"
    enable() if want else disable()
    return f"autostart {'enabled' if want else 'disabled'}"
