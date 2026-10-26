# keysnip

Keyboard-only screen region capture for Windows, macOS and Linux (X11/XWayland). Select an area without touching the mouse, then copy it to the clipboard (optionally saving a PNG too).
Only Windows has been tested; macOS and Linux support is untested.

## Install

Requires Python 3.9+ with tkinter.

    pip install -r requirements.txt

## Settings and autostart

All platforms share one settings file, `keysnip.json`, next to the script (override the path with `KEYSNIP_CONFIG`). It is created with the defaults on first run and is not committed to git; `keysnip.example.json` in the repo shows every option with its default value, so you can copy it to `keysnip.json` and edit.

```json
{
  "autostart": false,
  "save_dir": null,
  "save_to_disk": false,
  "hotkey_window_seconds": 1.5,
  "hotkey": "win+g,s",
  "keys": {
    "left": ["Left", "h"],
    "right": ["Right", "l"],
    "up": ["Up", "k"],
    "down": ["Down", "j"],
    "zoom": ["1", "2", "3", "4", "5", "6", "7", "8", "9"],
    "undo": ["BackSpace"],
    "reset": ["f"],
    "capture": ["Return", "space"],
    "cancel": ["Escape", "q"],
    "resize_end_modifier": "shift",
    "resize_start_modifier": "alt",
    "fine_modifier": "ctrl"
  }
}
```

You only need to list what you want to change; anything missing keeps its default. Invalid entries are reported on stderr and ignored.

| Setting | Meaning |
|---|---|
| `autostart` | `true` launches the hotkey listener at login |
| `save_dir` | Screenshot folder; `null` = `~/Pictures/keysnip`. The `KEYSNIP_DIR` env var overrides it |
| `save_to_disk` | `false` (default) = clipboard only, nothing kept (if the clipboard fails, the PNG is saved so the capture isn't lost). `true` = also save every capture as a PNG in `save_dir` |
| `hotkey_window_seconds` | Max time between the keys of the hotkey sequence |
| `hotkey` | Global hotkey that opens the overlay (see below) |
| `keys` | Overlay key bindings (see below) |

### Global hotkey

Format: `<modifiers>+<key>,<key>,...`. Modifiers are `win` (Cmd on macOS), `ctrl`, `alt` and `shift`, joined with `+`. Keys are single letters or digits, pressed in order within `hotkey_window_seconds`.

| Example | Meaning |
|---|---|
| `win+g,s` | Hold Win, press G, then S (default) |
| `ctrl+alt+x` | A single chord |
| `g,s` | No modifier: press G then S anywhere (will also fire while typing "gs") |

On Windows, when the hotkey uses `win`, those Win+key combinations are swallowed so the built-in shortcut doesn't fire.

### Overlay keys

Each action takes a list of keys. Use a single character (`"h"`, case-insensitive) or a tkinter key name (`"Left"`, `"Return"`, `"BackSpace"`, `"space"`, `"Escape"`, `"Tab"`, `"F5"`, ...). Setting an action replaces its default list, so include every key you want. `zoom` needs exactly 9 keys, in numpad order (1 = bottom-left ... 9 = top-right); they are also the labels drawn on the grid. The three `*_modifier` settings accept `shift`, `ctrl`, `alt` or `none`.

Starting the listener syncs the OS autostart entry with `autostart` (the entry runs `keysnip.py --foreground`, since the OS already supervises it). To apply a change without starting the listener, run `python keysnip.py --apply-config`. `python keysnip.py --status` shows the config path, whether autostart is registered and whether the listener is running.

| OS | Autostart mechanism |
|---|---|
| Windows | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value `keysnip`, launched with `pythonw.exe` (no console window) |
| macOS | LaunchAgent `~/Library/LaunchAgents/com.keysnip.listener.plist` |
| Linux | `~/.config/autostart/keysnip.desktop` |

On macOS, the interpreter launched at login needs the Accessibility/Input Monitoring and Screen Recording permissions. Only the Windows autostart has been tested.

## Usage

One file, the same command on every platform:

    python keysnip.py              # start the listener in the background and return (no console stays open)
    python keysnip.py --foreground # run the listener in this terminal instead (Ctrl+C quits, errors are visible)
    python keysnip.py --now        # open the overlay immediately, capture once, exit
    python keysnip.py --status     # config path, autostart state, whether the listener is running

The first form starts a detached copy of itself (`pythonw.exe` on Windows, a new session on macOS/Linux) and exits, so there is no terminal to keep open and it keeps running if you close the one you started it from. On Windows, `pythonw keysnip.py` avoids even the brief console flash of `python`. Launching it twice is harmless: a second copy sees the first and exits.

With the listener running, press the configured hotkey (default **Win+G, then S**; Cmd+G, then S on macOS) within 1.5 s to open the overlay.

- On Windows, with the default hotkey, Win+G (Game Bar) and Win+S (Search) are swallowed while the listener runs.
- **Stopping it:** Windows `Get-CimInstance Win32_Process | Where-Object CommandLine -match keysnip` to find the PID, then `Stop-Process -Id <PID>`; macOS/Linux `pkill -f keysnip.py`.

## Overlay keys

Defaults (all configurable under `keys`):
| Keys | Action |
|---|---|
| 1-9 | Zoom the selection into that cell of a 3x3 grid (numpad layout: 7 8 9 on top). Repeat to narrow quickly |
| Backspace | Undo last step |
| Arrows / h j k l | Move selection; step accelerates while a key is held |
| Shift+Arrows | Resize bottom-right corner |
| Alt+Arrows | Resize top-left corner |
| Ctrl+any | 1 px steps |
| F | Reset to whole screen |
| Enter / Space | Capture: copy to clipboard (and save a PNG if `save_to_disk` is on) |
| Esc / Q | Cancel |

The overlay starts with the whole screen selected. Typical flow: press digits to zoom onto the area (2-4 presses), nudge with arrows, press Enter.

A right-click also cancels. The label above the selection shows its size and position.

## Output and configuration

- The capture is copied to the clipboard: the Win32 clipboard API directly on Windows (no helper process, so no console window flashes), `osascript` on macOS, `wl-copy` or `xclip` on Linux. With `save_to_disk` on, it is also saved as `keysnip_YYYYMMDD_HHMMSS.png` in `save_dir`. If the clipboard can't be used, the PNG is saved regardless.
- Exit code: `0` on capture, `1` on cancel (with `--now`).

## How it works

1. The hotkey listener (`pynput`) runs in the main loop and waits for the configured hotkey. It keeps one hidden Tk window alive so each capture opens quickly.
2. On trigger, it grabs the whole primary screen with Pillow.
3. A fullscreen tkinter overlay shows the dimmed screenshot with the selection highlighted. On Windows it is a borderless popup, so tiling window managers such as wtile ignore it.
4. On Enter, the selected region is cropped from the original grab and copied (and saved if `save_to_disk` is on).

## Platform notes

- **Windows:** the overlay takes the foreground with `SetForegroundWindow` (attaching to the current foreground thread's input to get past the foreground lock) and takes it back if something steals it. It never synthesizes Alt key presses, because those put a window into menu mode and swallow keys. It waits for Win/Shift/Ctrl/Alt to be released before appearing, so a still-held modifier can't turn its keys into system shortcuts. Pressing the hotkey while the overlay is open is ignored. The listener detects when the session is unlocked and recreates its keyboard hook, so the hotkey keeps working after the lock screen. It also recreates the hook if it ever stops.
- **Single instance:** only one listener runs at a time (an OS file lock in the temp folder). A second `python keysnip.py` prints "keysnip is already running" and exits with code 2, which also makes autostart harmless if you start it manually too. The lock is released automatically if the process exits or crashes. `--now`, `--status` and `--apply-config` are not affected.
- **macOS:** grant the terminal Screen Recording (for capture) and Accessibility/Input Monitoring (for the hotkey).
- **Linux:** needs an X11 or XWayland session (`DISPLAY` set). Pure Wayland is not supported. The clipboard step needs `xclip` or `wl-copy`.

## Limitations

- Captures the primary screen only.
- Alt+Arrow detection differs per platform and is untested outside Windows.
- Typing Win+G then S is swallowed on Windows even if you wanted Game Bar or Search.

## Troubleshooting

- **Overlay doesn't accept keys:** click it once to take focus, or restart the listener.
- **Nothing happens on the hotkey:** check the listener is running, and that the keys are pressed in order within `hotkey_window_seconds` while holding the modifiers. After a lock/unlock the hook restarts itself within about a second.
- **"keysnip is already running":** another listener holds the lock. Stop it first (see Usage), or just use it. `python keysnip.py --status` confirms it is running.
- **Config change has no effect:** restart the listener; settings are read at startup. Warnings about invalid entries are printed to stderr, which the background copy discards; run `python keysnip.py --foreground` in a terminal to see them.
- **Listener starts and nothing happens, no error:** the background copy hides startup errors. Run `python keysnip.py --foreground` to see them.
- **Clipboard unavailable message:** the PNG is saved instead so the capture isn't lost; install `xclip` or `wl-copy` on Linux.
