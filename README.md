# keysnip

Keyboard-only screen region capture for Windows, macOS and Linux (X11/XWayland). Select an area without touching the mouse, then copy it to the clipboard and save it as a PNG.

Only Windows has been tested; macOS and Linux support is untested.

## Install

Requires Python 3.9+ with tkinter.

    pip install -r requirements.txt

## Settings and autostart

All platforms share one settings file, `keysnip.json`, next to the script (created with defaults on first run; override the path with `KEYSNIP_CONFIG`).

```json
{
  "autostart": false,
  "save_dir": null,
  "save_to_disk": false,
  "hotkey_window_seconds": 1.5
}
```

| Setting | Meaning |
|---|---|
| `autostart` | `true` launches the hotkey listener at login |
| `save_dir` | Screenshot folder; `null` = `~/Pictures/keysnip`. The `KEYSNIP_DIR` env var overrides it |
| `save_to_disk` | `false` (default) = clipboard only, nothing kept (if the clipboard fails, the PNG is saved so the capture isn't lost). `true` = also save every capture as a PNG in `save_dir` |
| `hotkey_window_seconds` | Max time between G and S |

Starting the listener syncs the OS autostart entry with `autostart`. To apply a change without starting the listener, run `python keysnip.py --apply-config`. `python keysnip.py --status` shows the config path and whether autostart is registered.

| OS | Autostart mechanism |
|---|---|
| Windows | `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` value `keysnip`, launched with `pythonw.exe` (no console window) |
| macOS | LaunchAgent `~/Library/LaunchAgents/com.keysnip.listener.plist` |
| Linux | `~/.config/autostart/keysnip.desktop` |

On macOS, the interpreter launched at login needs the Accessibility/Input Monitoring and Screen Recording permissions. Only the Windows autostart has been tested.

## Usage

    python keysnip.py          # start the background listener
    python keysnip.py --now    # open the overlay immediately, capture once, exit

With the listener running, press **Win+G, then S** (Cmd+G, then S on macOS) within 1.5 s to open the overlay. Stop the listener with Ctrl+C in its terminal.

- On Windows, Win+G (Game Bar) and Win+S (Search) are swallowed while the listener runs.
- When started with `pythonw.exe` (as autostart does), stop it from Task Manager or with `Stop-Process -Id <PID>`.

## Overlay keys

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

- The capture is copied to the clipboard: PowerShell on Windows, `osascript` on macOS, `wl-copy` or `xclip` on Linux. With `save_to_disk` on, it is also saved as `keysnip_YYYYMMDD_HHMMSS.png` in `save_dir`. If no clipboard tool is available, the PNG is saved regardless.
- Exit code: `0` on capture, `1` on cancel (with `--now`).

## How it works

1. The hotkey listener (`pynput`) runs in the main loop and waits for the Win/Cmd+G, S sequence.
2. On trigger, it grabs the whole primary screen with Pillow.
3. A fullscreen tkinter overlay shows the dimmed screenshot with the selection highlighted. On Windows it is a borderless popup, so tiling window managers such as wtile ignore it.
4. On Enter, the selected region is cropped from the original grab, saved and copied.

## Platform notes

- **Windows:** the overlay forces itself to the foreground so it receives keystrokes. Mouse clicking it also grabs focus.
- **macOS:** grant the terminal Screen Recording (for capture) and Accessibility/Input Monitoring (for the hotkey).
- **Linux:** needs an X11 or XWayland session (`DISPLAY` set). Pure Wayland is not supported. The clipboard step needs `xclip` or `wl-copy`.

## Limitations

- Captures the primary screen only.
- Alt+Arrow detection differs per platform and is untested outside Windows.
- Typing Win+G then S is swallowed on Windows even if you wanted Game Bar or Search.

## Troubleshooting

- **Overlay doesn't accept keys:** click it once, or restart the listener.
- **Nothing happens on the hotkey:** check the listener is running, and that G and S are pressed within 1.5 s while holding Win.
- **Clipboard unavailable message:** the PNG is still saved; install `xclip` or `wl-copy` on Linux.
