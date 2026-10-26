"""keysnip - keyboard-only screen region capture (Windows / macOS / Linux).

Usage:
  python keysnip.py         listen for Win/Cmd+G then S, then open the overlay
  python keysnip.py --now   open the overlay immediately
  python keysnip.py --apply-config   sync the OS autostart entry with keysnip.json
  python keysnip.py --status         show config path and autostart state

Overlay keys:
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
import time
import tkinter as tk
from pathlib import Path

from PIL import ImageEnhance, ImageGrab, ImageTk

import settings

SYSTEM = platform.system()
CONFIG = settings.load()
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


def copy_to_clipboard(path):
    try:
        if SYSTEM == "Windows":
            ps = ("Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
                  f"[Windows.Forms.Clipboard]::SetImage([Drawing.Image]::FromFile('{path}'))")
            subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps],
                           check=True, capture_output=True)
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


class Selector:
    def __init__(self, shot):
        self.shot = shot
        self.w, self.h = shot.size
        self.dim = ImageEnhance.Brightness(shot).enhance(0.4)
        self.x1, self.y1, self.x2, self.y2 = 0, 0, self.w, self.h
        self.history, self.streak, self.last_move = [], 0, 0.0
        self.result = None

        self.root = tk.Tk()
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
        self.root.bind("<FocusOut>", lambda e: self.root.after(200, self.root.focus_force))
        self.root.bind("<Button-1>", lambda e: self.take_focus())
        self.root.after(50, self.take_focus)
        self.draw()

    def take_focus(self):
        if SYSTEM == "Windows":
            import ctypes
            u = ctypes.windll.user32
            u.keybd_event(0x12, 0, 0, 0)  # Alt tap lets us bypass the foreground lock
            u.keybd_event(0x12, 0, 2, 0)
        self.root.lift()
        self.root.focus_force()
        self.canvas.focus_set()

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
                self.canvas.create_text(cx, cy, text=str(n + 1), fill="#00aaff",
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

    def on_key(self, e):
        keysym = e.keysym
        ctrl, shift = bool(e.state & 0x4), bool(e.state & 0x1)
        alt = bool(e.state & 0x20000) if SYSTEM == "Windows" else bool(e.state & 0x8)
        dirs = {"Left": (-1, 0), "Right": (1, 0), "Up": (0, -1), "Down": (0, 1),
                "h": (-1, 0), "l": (1, 0), "k": (0, -1), "j": (0, 1)}
        if keysym in dirs or keysym.lower() in dirs and not keysym.isupper():
            d = dirs.get(keysym) or dirs[keysym.lower()]
            now = time.time()
            self.streak = self.streak + 1 if now - self.last_move < 0.15 else 0
            self.last_move = now
            step = FINE if ctrl else STEP * min(1 + self.streak // 4, 10)
            dx, dy = d[0] * step, d[1] * step
            self.push()
            if shift:
                self.x2 += dx
                self.y2 += dy
            elif alt:
                self.x1 += dx
                self.y1 += dy
            else:
                self.move(dx, dy)
            self.clamp()
            self.draw()
        elif keysym in "123456789" and len(keysym) == 1:
            n = int(keysym) - 1
            col, row = n % 3, 2 - n // 3  # numpad layout: 7 8 9 on top
            cw, ch = (self.x2 - self.x1) / 3, (self.y2 - self.y1) / 3
            self.push()
            x1, y1 = self.x1, self.y1
            self.x1, self.x2 = round(x1 + col * cw), round(x1 + (col + 1) * cw)
            self.y1, self.y2 = round(y1 + row * ch), round(y1 + (row + 1) * ch)
            self.clamp()
            self.draw()
        elif keysym == "BackSpace" and self.history:
            self.x1, self.y1, self.x2, self.y2 = self.history.pop()
            self.draw()
        elif keysym.lower() == "f":
            self.push()
            self.x1, self.y1, self.x2, self.y2 = 0, 0, self.w, self.h
            self.draw()
        elif keysym in ("Return", "space"):
            self.result = (self.x1, self.y1, self.x2, self.y2)
            self.root.destroy()
        elif keysym == "Escape" or keysym.lower() == "q":
            self.root.destroy()

    def push(self):
        self.history.append((self.x1, self.y1, self.x2, self.y2))
        del self.history[:-50]

    def run(self):
        self.root.mainloop()
        return self.result


def capture_once():
    time.sleep(0.15)
    shot = grab_screen().convert("RGB")
    box = Selector(shot).run()
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
    """Wait in the background; pressing Win/Cmd+G then S starts a capture."""
    import queue
    from pynput import keyboard

    triggers = queue.Queue()
    state = {"got_g": False, "last": 0.0}
    held = set()
    meta = {keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r}

    def on_press(key):
        if key in meta:
            held.add(key)
            return
        ch = (getattr(key, "char", None) or "").lower()
        if not held or ch not in ("g", "s"):
            return
        now = time.time()
        if now - state["last"] > window:
            state["got_g"] = False
        state["last"] = now
        if ch == "g":
            state["got_g"] = True
        elif state["got_g"]:
            state["got_g"] = False
            triggers.put(1)

    def on_release(key):
        held.discard(key)

    kwargs = {}
    if SYSTEM == "Windows":
        import ctypes

        def win_filter(msg, data):
            # Swallow Win+G / Win+S (Game Bar / Search); a dummy key stops the Start menu opening on Win release.
            user32 = ctypes.windll.user32
            win_down = (user32.GetAsyncKeyState(0x5B) & 0x8000) or (user32.GetAsyncKeyState(0x5C) & 0x8000)
            if data.vkCode in (0x47, 0x53) and win_down and msg in (0x100, 0x101):
                if msg == 0x100:
                    user32.keybd_event(0xFF, 0, 0, 0)
                    user32.keybd_event(0xFF, 0, 2, 0)
                    on_press(keyboard.KeyCode.from_char(chr(data.vkCode).lower()))
                listener.suppress_event()
            return True

        kwargs["win32_event_filter"] = win_filter

    listener = keyboard.Listener(on_press=on_press, on_release=on_release, **kwargs)
    listener.start()
    print("keysnip listening: press Win+G then S to capture (Ctrl+C here to quit)")
    try:
        while True:
            try:
                triggers.get(timeout=0.5)
            except queue.Empty:
                continue
            capture_once()
            while not triggers.empty():
                triggers.get_nowait()
    except KeyboardInterrupt:
        listener.stop()
    return 0


def main():
    if "--apply-config" in sys.argv:
        print(settings.apply(CONFIG))
        return 0
    if "--status" in sys.argv:
        print(f"config: {settings.CONFIG_PATH}\nautostart configured: {CONFIG['autostart']}, "
              f"registered with OS: {settings.is_enabled()}")
        return 0
    enable_dpi_awareness()
    if "--now" in sys.argv:
        return capture_once()
    print(settings.apply(CONFIG))
    return listen(window=float(CONFIG["hotkey_window_seconds"]))


if __name__ == "__main__":
    sys.exit(main())
