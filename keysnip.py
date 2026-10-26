"""keysnip - keyboard-only screen region capture (Windows / macOS / Linux).

Usage:
  python keysnip.py                  start the listener in the background (no terminal or console stays open)
  python keysnip.py --foreground     run the listener in this terminal (Ctrl+C quits); default hotkey Win/Cmd+G then S
  python keysnip.py --now            open the overlay immediately
  python keysnip.py --apply-config   sync the OS autostart entry with keysnip.json
  python keysnip.py --status         show config path, autostart state and whether the listener is running

Overlay keys (defaults; all configurable under "keys" in keysnip.json):
  1-9               zoom into that cell of a 3x3 grid (numpad layout)
  Backspace         undo last step
  Arrows / h j k l  move the selection (accelerates while held)
  Shift + Arrows    resize (bottom-right corner)
  Alt   + Arrows    resize (top-left corner)
  Ctrl  + any       fine step (1 px)
  F                 reset to the whole screen
  Enter / Space     capture (copies to clipboard; also saves a PNG if save_to_disk is on)
  Esc / Q           cancel
"""
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from pathlib import Path

from PIL import ImageEnhance, ImageGrab, ImageTk

import settings

SYSTEM = platform.system()
CONFIG = settings.load()
KEYS = CONFIG["keys"]
SAVE_DIR = Path(os.environ.get("KEYSNIP_DIR") or CONFIG["save_dir"] or Path.home() / "Pictures" / "keysnip").expanduser()
STEP, FINE = 10, 1


def enable_dpi_awareness():
    if SYSTEM == "Windows":
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()


def grab_screen():
    if SYSTEM == "Linux" and not os.environ.get("DISPLAY"):
        sys.exit("keysnip: no X11/XWayland display available.")
    return ImageGrab.grab()


def copy_image_windows(path):
    """Put the image on the Windows clipboard as a DIB using the Win32 API directly (no subprocess, so no
    console window flashes when running under pythonw, and it is much faster than shelling out)."""
    import ctypes
    import io
    from ctypes import wintypes
    from PIL import Image

    buf = io.BytesIO()
    Image.open(path).convert("RGB").save(buf, "BMP")
    data = buf.getvalue()[14:]  # drop the 14-byte BITMAPFILEHEADER; the rest is a DIB
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE

    handle = kernel32.GlobalAlloc(0x0002, len(data))  # GMEM_MOVEABLE
    if not handle:
        return False
    ctypes.memmove(kernel32.GlobalLock(handle), data, len(data))
    kernel32.GlobalUnlock(handle)
    for _ in range(20):  # another app may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        kernel32.GlobalFree(handle)
        return False
    try:
        user32.EmptyClipboard()
        if not user32.SetClipboardData(8, handle):  # CF_DIB; on success the clipboard owns the memory
            kernel32.GlobalFree(handle)
            return False
    finally:
        user32.CloseClipboard()
    return True


def copy_to_clipboard(path):
    try:
        if SYSTEM == "Windows":
            return copy_image_windows(path)
        elif SYSTEM == "Darwin":
            script = f'set the clipboard to (read (POSIX file "{path}") as «class PNGf»)'
            subprocess.run(["osascript", "-e", script], check=True, capture_output=True)
        elif shutil.which("wl-copy"):
            with open(path, "rb") as f:
                subprocess.run(["wl-copy", "--type", "image/png"], stdin=f, check=True)
        elif shutil.which("xclip"):
            subprocess.run(["xclip", "-selection", "clipboard", "-t", "image/png", "-i", path],
                           check=True)
        else:
            return False
        return True
    except Exception:
        return False


def force_foreground(hwnd):
    """Windows: make hwnd the foreground window without synthesizing keys (an Alt tap would put the window
    into menu mode and swallow keys). Attaching to the current foreground thread's input lifts the lock."""
    import ctypes
    from ctypes import wintypes
    u, k32 = ctypes.windll.user32, ctypes.windll.kernel32
    u.GetForegroundWindow.restype = wintypes.HWND
    u.SetForegroundWindow.argtypes = [wintypes.HWND]
    u.BringWindowToTop.argtypes = [wintypes.HWND]
    u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    k32.GetCurrentThreadId.restype = wintypes.DWORD
    fg = u.GetForegroundWindow()
    fg_tid = u.GetWindowThreadProcessId(fg, None) if fg else 0
    me = k32.GetCurrentThreadId()
    attached = bool(fg_tid and fg_tid != me and u.AttachThreadInput(me, fg_tid, True))
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(me, fg_tid, False)


class Selector:
    def __init__(self, shot, master=None):
        self.shot = shot
        self.w, self.h = shot.size
        self.dim = ImageEnhance.Brightness(shot).enhance(0.4)
        self.x1, self.y1, self.x2, self.y2 = 0, 0, self.w, self.h
        self.history, self.streak, self.last_move = [], 0, 0.0
        self.result = None

        self.master = master
        self.root = tk.Toplevel(master) if master else tk.Tk()
        self.root.geometry(f"{self.w}x{self.h}+0+0")
        if SYSTEM == "Windows":
            # Borderless popup (no caption/frame, no taskbar entry) so tiling managers ignore it
            self.root.overrideredirect(True)
        else:
            self.root.attributes("-fullscreen", True)
        self.root.attributes("-topmost", True)
        self.canvas = tk.Canvas(self.root, width=self.w, height=self.h,
                                highlightthickness=0, cursor="none")
        self.canvas.pack()
        self.dim_tk = ImageTk.PhotoImage(self.dim)
        self.canvas.create_image(0, 0, image=self.dim_tk, anchor="nw")
        self.root.bind("<Key>", self.on_key)
        self.root.bind("<Button-3>", lambda e: self.root.destroy())
        self.root.bind("<Button-1>", lambda e: self.take_focus())
        self.draw()
        self.root.update_idletasks()
        self.take_focus()
        self.root.after(150, self.keep_focus)

    def hwnd(self):
        import ctypes
        return ctypes.windll.user32.GetParent(self.root.winfo_id()) or self.root.winfo_id()

    def keep_focus(self):
        """Reclaim the foreground if something (Start menu, a stray Win key, ...) took it from the overlay."""
        try:
            if SYSTEM == "Windows":
                import ctypes
                if ctypes.windll.user32.GetForegroundWindow() != self.hwnd():
                    self.take_focus()
            self.root.after(150, self.keep_focus)
        except tk.TclError:  # overlay already closed
            pass

    def take_focus(self):
        try:
            if SYSTEM == "Windows":
                force_foreground(self.hwnd())
            self.root.lift()
            self.root.focus_force()
            self.canvas.focus_set()
        except tk.TclError:  # overlay already closed
            pass

    def draw(self):
        self.canvas.delete("sel")
        crop = self.shot.crop((self.x1, self.y1, self.x2, self.y2))
        self.bright_tk = ImageTk.PhotoImage(crop)
        self.canvas.create_image(self.x1, self.y1, image=self.bright_tk, anchor="nw", tags="sel")
        self.canvas.create_rectangle(self.x1, self.y1, self.x2, self.y2,
                                     outline="#00aaff", width=2, tags="sel")
        cw, ch = (self.x2 - self.x1) / 3, (self.y2 - self.y1) / 3
        if cw > 40 and ch > 30:
            for i in range(1, 3):
                self.canvas.create_line(self.x1 + i * cw, self.y1, self.x1 + i * cw, self.y2,
                                        fill="#00aaff", dash=(4, 4), tags="sel")
                self.canvas.create_line(self.x1, self.y1 + i * ch, self.x2, self.y1 + i * ch,
                                        fill="#00aaff", dash=(4, 4), tags="sel")
            for n in range(9):
                cx = self.x1 + (n % 3 + 0.5) * cw
                cy = self.y1 + (2 - n // 3 + 0.5) * ch
                self.canvas.create_text(cx, cy, text=KEYS["zoom"][n], fill="#00aaff",
                                        font=("Segoe UI", 20, "bold"), tags="sel")
        label = f"{self.x2 - self.x1} x {self.y2 - self.y1}  @ {self.x1},{self.y1}"
        ty = self.y1 - 22 if self.y1 > 30 else self.y2 + 6
        self.canvas.create_text(self.x1 + 4, ty, text=label, anchor="nw",
                                fill="white", font=("Segoe UI", 11, "bold"), tags="sel")

    def clamp(self):
        self.x1, self.x2 = max(0, self.x1), min(self.w, self.x2)
        self.y1, self.y2 = max(0, self.y1), min(self.h, self.y2)
        if self.x2 - self.x1 < 2:
            self.x2 = min(self.w, self.x1 + 2)
            self.x1 = self.x2 - 2
        if self.y2 - self.y1 < 2:
            self.y2 = min(self.h, self.y1 + 2)
            self.y1 = self.y2 - 2

    def move(self, dx, dy):
        w, h = self.x2 - self.x1, self.y2 - self.y1
        self.x1 = min(max(0, self.x1 + dx), self.w - w)
        self.y1 = min(max(0, self.y1 + dy), self.h - h)
        self.x2, self.y2 = self.x1 + w, self.y1 + h

    @staticmethod
    def key_matches(configured, keysym):
        return keysym == configured or (len(configured) == 1 and keysym.lower() == configured.lower())

    def matches(self, action, keysym):
        return any(self.key_matches(k, keysym) for k in KEYS[action])

    def zoom_index(self, keysym):
        return next((i for i, k in enumerate(KEYS["zoom"]) if self.key_matches(k, keysym)), None)

    def modifier_down(self, name, state):
        alt_mask = 0x20000 if SYSTEM == "Windows" else 0x8
        mask = {"shift": 0x1, "ctrl": 0x4, "alt": alt_mask}.get(name)
        return bool(mask and state & mask)

    def on_key(self, e):
        keysym = e.keysym
        direction = next(((dx, dy) for name, (dx, dy) in
                          (("left", (-1, 0)), ("right", (1, 0)), ("up", (0, -1)), ("down", (0, 1)))
                          if self.matches(name, keysym)), None)
        if direction:
            fine = self.modifier_down(KEYS["fine_modifier"], e.state)
            resize_end = self.modifier_down(KEYS["resize_end_modifier"], e.state)
            resize_start = self.modifier_down(KEYS["resize_start_modifier"], e.state)
            now = time.time()
            self.streak = self.streak + 1 if now - self.last_move < 0.15 else 0
            self.last_move = now
            step = FINE if fine else STEP * min(1 + self.streak // 4, 10)
            dx, dy = direction[0] * step, direction[1] * step
            self.push()
            if resize_end:
                self.x2 += dx
                self.y2 += dy
            elif resize_start:
                self.x1 += dx
                self.y1 += dy
            else:
                self.move(dx, dy)
            self.clamp()
            self.draw()
        elif self.zoom_index(keysym) is not None:
            n = self.zoom_index(keysym)
            col, row = n % 3, 2 - n // 3  # numpad layout: cell 7 8 9 on top
            cw, ch = (self.x2 - self.x1) / 3, (self.y2 - self.y1) / 3
            self.push()
            x1, y1 = self.x1, self.y1
            self.x1, self.x2 = round(x1 + col * cw), round(x1 + (col + 1) * cw)
            self.y1, self.y2 = round(y1 + row * ch), round(y1 + (row + 1) * ch)
            self.clamp()
            self.draw()
        elif self.matches("undo", keysym):
            if self.history:
                self.x1, self.y1, self.x2, self.y2 = self.history.pop()
                self.draw()
        elif self.matches("reset", keysym):
            self.push()
            self.x1, self.y1, self.x2, self.y2 = 0, 0, self.w, self.h
            self.draw()
        elif self.matches("capture", keysym):
            self.result = (self.x1, self.y1, self.x2, self.y2)
            self.root.destroy()
        elif self.matches("cancel", keysym):
            self.root.destroy()

    def push(self):
        self.history.append((self.x1, self.y1, self.x2, self.y2))
        del self.history[:-50]

    def run(self):
        if self.master:
            self.root.wait_window()
        else:
            self.root.mainloop()
        return self.result


def session_locked():
    """True while the Windows lock screen (secure desktop) is the input desktop."""
    if SYSTEM != "Windows":
        return False
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desktop:
        return True
    user32.CloseDesktop(desktop)
    return False


def wait_modifiers_released(timeout=1.5):
    """Windows: hold off until Win/Shift/Ctrl/Alt are physically up, so the overlay's keys and our focus
    taps don't combine with a still-held modifier into system shortcuts (Win+<digit>, Win+Alt, ...)."""
    if SYSTEM != "Windows":
        return
    import ctypes
    down = ctypes.windll.user32.GetAsyncKeyState
    deadline = time.time() + timeout
    while time.time() < deadline and any(down(vk) & 0x8000 for vk in (0x5B, 0x5C, 0x10, 0x11, 0x12)):
        time.sleep(0.01)


OVERLAY_OPEN = threading.Event()  # set while an overlay is showing; the hotkey is ignored meanwhile
MASTER = None  # hidden Tk root kept alive by the listener so each capture skips Tk startup


def capture_once():
    OVERLAY_OPEN.set()
    try:
        shot = grab_screen().convert("RGB")
        wait_modifiers_released()
        box = Selector(shot, MASTER).run()
    finally:
        OVERLAY_OPEN.clear()
    if not box:
        print("cancelled")
        return 1
    img = shot.crop(box)
    name = time.strftime("keysnip_%Y%m%d_%H%M%S.png")
    if CONFIG["save_to_disk"]:
        SAVE_DIR.mkdir(parents=True, exist_ok=True)
        path = SAVE_DIR / name
        img.save(path)
        ok = copy_to_clipboard(str(path))
        print(f"saved {path}" + (" and copied to clipboard" if ok else " (clipboard unavailable)"))
        return 0
    # Clipboard only: stage in a temp file, and keep it if the clipboard fails so the capture isn't lost
    path = Path(tempfile.gettempdir()) / name
    img.save(path)
    if copy_to_clipboard(str(path)):
        path.unlink(missing_ok=True)
        print("copied to clipboard")
        return 0
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    kept = SAVE_DIR / name
    shutil.move(str(path), kept)
    print(f"clipboard unavailable, saved {kept} instead")
    return 0


def listen(window=1.5):
    """Wait in the background; the configured hotkey (default Win/Cmd+G then S) starts a capture."""
    import queue
    from pynput import keyboard

    modifiers, sequence = settings.parse_hotkey(CONFIG["hotkey"])
    groups = {
        "meta": {keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r},
        "ctrl": {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r},
        "alt": {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r, keyboard.Key.alt_gr},
        "shift": {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r},
    }
    modifier_keys = set().union(*(groups[m] for m in modifiers)) if modifiers else set()

    triggers = queue.Queue()
    state = {"pos": 0, "last": 0.0}
    held = set()

    def modifiers_held():
        return all(held & groups[m] for m in modifiers)

    def on_press(key):
        if key in modifier_keys:
            held.add(key)
            return
        if OVERLAY_OPEN.is_set():  # ignore the hotkey (and reset the sequence) while the overlay is up
            state["pos"] = 0
            return
        if not modifiers_held():
            return
        ch = (getattr(key, "char", None) or "").lower()
        if not ch.isalnum():  # e.g. Ctrl+G reports a control character; fall back to the virtual-key code
            vk = getattr(key, "vk", None)
            ch = chr(vk).lower() if vk and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A) else ""
        if not ch:  # non-character keys (including our own dummy key) must not break a sequence
            return
        now = time.time()
        if now - state["last"] > window:
            state["pos"] = 0
        state["last"] = now
        if ch == sequence[state["pos"]]:
            state["pos"] += 1
            if state["pos"] == len(sequence):
                state["pos"] = 0
                triggers.put(1)
        else:
            state["pos"] = 1 if ch == sequence[0] else 0

    def on_release(key):
        held.discard(key)

    kwargs = {}
    current = {"listener": None}
    if SYSTEM == "Windows" and "meta" in modifiers:
        import ctypes

        vks = {ord(k.upper()) for k in sequence}

        def win_filter(msg, data):
            # Swallow Win+<key> so Windows shortcuts (Game Bar, Search, ...) don't fire; a dummy key stops
            # the Start menu opening on Win release.
            user32 = ctypes.windll.user32
            win_down = (user32.GetAsyncKeyState(0x5B) & 0x8000) or (user32.GetAsyncKeyState(0x5C) & 0x8000)
            if data.vkCode in vks and win_down and msg in (0x100, 0x101):
                if msg == 0x100:
                    user32.keybd_event(0xFF, 0, 0, 0)
                    user32.keybd_event(0xFF, 0, 2, 0)
                    on_press(keyboard.KeyCode.from_char(chr(data.vkCode).lower()))
                current["listener"].suppress_event()
            return True

        kwargs["win32_event_filter"] = win_filter

    def start_listener():
        held.clear()
        state.update(pos=0, last=0.0)
        current["listener"] = keyboard.Listener(on_press=on_press, on_release=on_release, **kwargs)
        current["listener"].start()

    start_listener()
    global MASTER
    MASTER = tk.Tk()
    MASTER.withdraw()
    print(f"keysnip listening: press {CONFIG['hotkey']} to capture (Ctrl+C here to quit)")
    was_locked = False
    try:
        while True:
            try:
                triggers.get(timeout=0.5)
            except queue.Empty:
                # Hooks can go stale across a lock/unlock (or die), so recreate them after unlocking
                locked = session_locked()
                if (was_locked and not locked) or not current["listener"].is_alive():
                    current["listener"].stop()
                    start_listener()
                    print("keysnip: keyboard hook restarted", flush=True)
                was_locked = locked
                continue
            capture_once()
            while not triggers.empty():
                triggers.get_nowait()
    except KeyboardInterrupt:
        current["listener"].stop()
    return 0


_LOCK_HANDLE = None  # kept open for the life of the process to hold the single-instance lock


def acquire_single_instance():
    """Take an OS-level exclusive lock; returns False if another keysnip listener already holds it.
    The OS releases the lock if the process exits or crashes, so it can't go stale."""
    global _LOCK_HANDLE
    import getpass
    path = Path(tempfile.gettempdir()) / f"keysnip-{getpass.getuser()}.lock"
    handle = open(path, "a+b")
    try:
        if SYSTEM == "Windows":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    _LOCK_HANDLE = handle
    return True


def already_running():
    """Peek at the single-instance lock without keeping it."""
    global _LOCK_HANDLE
    if not acquire_single_instance():
        return True
    _LOCK_HANDLE.close()
    _LOCK_HANDLE = None
    return False


def spawn_background():
    """Re-launch this script as a detached listener with no console or terminal, then let the caller exit."""
    args = [sys.executable, str(Path(__file__).resolve()), "--foreground"]
    quiet = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True)
    if SYSTEM == "Windows":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            args[0] = str(pythonw)
        # DETACHED_PROCESS | CREATE_NO_WINDOW: no console, and it survives the launching terminal closing
        subprocess.Popen(args, creationflags=0x00000008 | 0x08000000, **quiet)
    else:
        subprocess.Popen(args, start_new_session=True, **quiet)


def main():
    if "--apply-config" in sys.argv:
        print(settings.apply(CONFIG))
        return 0
    if "--status" in sys.argv:
        print(f"config: {settings.CONFIG_PATH}\nautostart configured: {CONFIG['autostart']}, "
              f"registered with OS: {settings.is_enabled()}\nlistener running: {already_running()}")
        return 0
    enable_dpi_awareness()
    if "--now" in sys.argv:
        return capture_once()
    # pythonw (and --foreground) is already detached; anything else hands off to a background copy
    foreground = "--foreground" in sys.argv or Path(sys.executable).name.lower() == "pythonw.exe"
    if not foreground:
        if already_running():
            print("keysnip is already running.")
            return 2
        spawn_background()
        print("keysnip started in the background. Use `python keysnip.py --foreground` to run it in this "
              "terminal instead.")
        return 0
    if not acquire_single_instance():
        print("keysnip is already running; exiting.")
        return 2
    print(settings.apply(CONFIG))
    return listen(window=float(CONFIG["hotkey_window_seconds"]))


if __name__ == "__main__":
    sys.exit(main())
