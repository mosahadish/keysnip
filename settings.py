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
    "hotkey_window_seconds": 1.5,  # max time between keys of the hotkey sequence
    # Global hotkey that opens the overlay: "<modifier>+<key>,<key>,..." (win/cmd, ctrl, alt, shift or none)
    "hotkey": "win+g,s",
    # Overlay keys: tkinter keysym names (e.g. Left, Return, BackSpace, space, Escape) or single characters
    "keys": {
        "left": ["Left", "h"],
        "right": ["Right", "l"],
        "up": ["Up", "k"],
        "down": ["Down", "j"],
        "zoom": ["1", "2", "3", "4", "5", "6", "7", "8", "9"],  # cells in numpad order: 1 bottom-left ... 9 top-right
        "undo": ["BackSpace"],
        "reset": ["f"],
        "capture": ["Return", "space"],
        "cancel": ["Escape", "q"],
        "resize_end_modifier": "shift",    # + direction moves the bottom-right corner
        "resize_start_modifier": "alt",    # + direction moves the top-left corner
        "fine_modifier": "ctrl",           # + direction moves 1 px instead of 10
    },
}

MODIFIER_NAMES = {"none", "shift", "ctrl", "alt"}
HOTKEY_MODIFIERS = {"win": "meta", "cmd": "meta", "super": "meta", "meta": "meta",
                    "ctrl": "ctrl", "alt": "alt", "shift": "shift", "none": None}


def parse_hotkey(text):
    """'win+g,s' -> (['meta'], ['g', 's']); 'ctrl+alt+x' -> (['ctrl', 'alt'], ['x']); 'g,s' -> ([], ['g', 's'])."""
    *mod_parts, keys_part = text.split("+")
    modifiers = [HOTKEY_MODIFIERS.get(m.strip().lower(), "invalid") for m in mod_parts]
    modifiers = [m for m in modifiers if m is not None]  # "none" maps to None: no modifier
    keys = [k.strip().lower() for k in keys_part.split(",") if k.strip()]
    if "invalid" in modifiers or not keys or any(len(k) != 1 or not k.isalnum() for k in keys):
        raise ValueError(f"invalid hotkey {text!r}; expected e.g. 'win+g,s' or 'ctrl+alt+x' (single letters/digits)")
    return modifiers, keys


def _clean_keys(user_keys):
    keys = {k: (list(v) if isinstance(v, list) else v) for k, v in DEFAULTS["keys"].items()}
    for name, value in (user_keys or {}).items():
        if name not in keys:
            print(f"keysnip: unknown key action {name!r} in config, ignored", file=sys.stderr)
        elif name.endswith("_modifier"):
            if isinstance(value, str) and value.lower() in MODIFIER_NAMES:
                keys[name] = value.lower()
            else:
                print(f"keysnip: {name} must be one of {sorted(MODIFIER_NAMES)}, using default", file=sys.stderr)
        elif isinstance(value, list) and value and all(isinstance(x, str) and x for x in value) \
                and (name != "zoom" or len(value) == 9):
            keys[name] = value
        else:
            print(f"keysnip: {name} must be a non-empty list of key names"
                  f"{' (9 for zoom)' if name == 'zoom' else ''}, using default", file=sys.stderr)
    return keys


def load():
    cfg = json.loads(json.dumps(DEFAULTS))
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULTS, indent=2) + "\n", encoding="utf-8")
        return cfg
    try:
        user = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        user_keys = user.pop("keys", None)
        cfg.update(user)
        cfg["keys"] = _clean_keys(user_keys)
    except (OSError, ValueError) as exc:
        print(f"keysnip: ignoring unreadable config {CONFIG_PATH}: {exc}", file=sys.stderr)
    try:
        parse_hotkey(cfg["hotkey"])
    except (ValueError, AttributeError) as exc:
        print(f"keysnip: {exc}; using default", file=sys.stderr)
        cfg["hotkey"] = DEFAULTS["hotkey"]
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
