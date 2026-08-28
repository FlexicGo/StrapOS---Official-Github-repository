"""
StrapOS - a tiny desktop-style OS simulation built with Python + pygame.

Features:
  - Desktop with a wallpaper (arch pattern) and app icons
  - File Manager: browses your Android app's external storage folder
    (Android/data/<package>/files) instead of the process home directory —
    falls back sensibly on desktop/other platforms
  - Settings: change the wallpaper (built-in colors, or upload your own image
    from your device — Settings → Wallpaper → Upload tile), switch the taskbar
    clock between 12-hour and 24-hour format, and more. The whole Settings
    screen scrolls, so it has room to grow. A Software Reset button (with a
    confirmation step) wipes everything — config, imported apps, custom
    wallpapers, logs — back to a fresh first-launch state.
  - Calculator: a working calculator (safe arithmetic evaluator, no eval())
  - Terminal: a terminal-style window you can type into — no commands are
    wired up yet, it's just the shell interface for now
  - Strap App Loader: import any .py file from your device, and it appears
    as a launchable app icon right on the StrapOS desktop. On desktop
    platforms it launches as its own real process/window; on Android/Pydroid
    (where only one display surface can exist per process) it runs
    in-process, reusing StrapOS's own surface instead of ever letting the
    app trigger a second real display init — which is what silently kills
    the app at the native level with no Python exception, if it happens.
    QUIT events are also filtered out for the app's whole run, and a small
    floating back button stays on top the whole time so you can always
    return to StrapOS, even if the app has no quit control of its own.
    A "closed after only Xs" warning appears (on both platforms) if an app
    exits suspiciously fast, so a missing event loop is obvious rather than
    a silent mystery. Settings → App Launch Mode lets you override the
    platform auto-detection.
  - Browser: launches your device's real, actual default browser (Chrome,
    Firefox, whatever you have) — not an embedded one — via a real Android
    Intent on Android/Pydroid, or Python's webbrowser module on desktop.
  - Power system: a power button in the taskbar opens Lock Screen / Restart /
    Shut Down. Lock Screen dims everything and shows a clock until you tap
    to unlock. Restart closes any running apps and replays the boot screen
    without touching your settings or files. Shut Down fades to black (the
    mirror image of the boot screen) and then actually exits the process.
  - Security (Settings → Security): set a 4-digit PIN, and StrapOS will
    require it to start — the desktop is inaccessible until it's entered
    correctly. The Lock Screen and Restart both honor it too, just like a
    real device. The PIN itself is never stored — only a salted SHA-256
    hash of it. A factory reset (Software Reset) clears it along with
    everything else, so you're never permanently locked out.
  - Scrolling works on both platforms: mouse wheel on desktop, and drag-to-
    scroll (finger follows content) on touchscreens, in every scrollable
    list — File Manager, Strap App Loader, Settings, the wallpaper/upload
    pickers. A short tap still opens/selects normally; only a real drag
    scrolls, so nothing gets misfired on touch devices.
  - Weather: a relaxing, pastel-themed weather app powered by Open-Meteo
    (free, no API key). Search any city; shows current conditions as both
    a big emoji AND a text description, temperature, feels-like, humidity,
    wind, and today's high/low. The background gently shifts color to match
    the weather/time of day, with soft drifting clouds for ambiance. The
    network request runs on a background thread so a slow connection never
    freezes the rest of the OS. Your last city is remembered and re-fetched
    automatically next time you open the app.
  - Autoscale: the whole UI is drawn to a fixed virtual canvas, then scaled to
    fit your window/screen size on the fly. Resize the window freely, or
    press F11 to toggle true fullscreen at your device's native resolution.

Run with:
    pip install pygame
    python strapos.py

Controls:
    Esc   - back to desktop / quit
    F11   - toggle fullscreen
    (tap or press any key during the boot screen to skip it)
"""

import pygame
import sys
import os
import json
import math
import time
import subprocess
import shutil
import runpy
import traceback
import contextlib
import hashlib
import threading
import urllib.request
import urllib.parse
import urllib.error

# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

pygame.init()

# ---------------------------------------------------------------------------
# Autoscale system
# ---------------------------------------------------------------------------
# Everything in StrapOS is drawn onto a fixed "virtual" canvas (BASE_WIDTH x
# BASE_HEIGHT). That canvas is then scaled up/down every frame to fit
# whatever window/screen size the device actually has, letterboxing if the
# aspect ratio doesn't match. This means all the app code below can keep
# using fixed coordinates while StrapOS still fills any screen cleanly.

BASE_WIDTH, BASE_HEIGHT = 1080, 680
BASE_ASPECT = BASE_WIDTH / BASE_HEIGHT
FPS = 60
TASKBAR_H = 48

CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".strapos_config.json")
HOME_DIR = os.path.expanduser("~")
STRAPOS_APPS_DIR = os.path.join(HOME_DIR, ".strapos_apps")
STRAPOS_LOGS_DIR = os.path.join(STRAPOS_APPS_DIR, "_logs")
STRAPOS_WALLPAPERS_DIR = os.path.join(HOME_DIR, ".strapos_wallpapers")
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".gif")
MAX_CUSTOM_WALLPAPERS = 5
try:
    os.makedirs(STRAPOS_LOGS_DIR, exist_ok=True)
    os.makedirs(STRAPOS_WALLPAPERS_DIR, exist_ok=True)
except Exception:
    pass


def _safe_filename(name):
    """Turn an app name into a filesystem-safe log filename."""
    kept = [c if (c.isalnum() or c in ("-", "_")) else "_" for c in name]
    result = "".join(kept)[:60]
    return result or "app"


def _hash_pin(pin, salt=None):
    """Salted SHA-256 hash of a PIN — the raw PIN itself is never stored."""
    if salt is None:
        salt = os.urandom(16).hex()
    digest = hashlib.sha256((salt + pin).encode("utf-8")).hexdigest()
    return digest, salt


def _verify_pin(pin, digest, salt):
    if not digest or not salt:
        return False
    check, _ = _hash_pin(pin, salt)
    return check == digest


DEMO_APP_SOURCE = '''"""
StrapOS Demo App — a tiny, deliberately simple pygame app used to verify the
Strap App Loader / Terminal "test" command are working correctly. It has an
actual event loop and keeps running until you close it (via the floating
back button on Android, or the ✕ Close App button / window close on
desktop), so it is a good way to check that app hosting works, independent
of any other script.
"""

import pygame
import sys
import math
import time

pygame.init()

existing = pygame.display.get_surface()
if existing is not None:
    screen = existing
else:
    screen = pygame.display.set_mode((900, 600), pygame.RESIZABLE)

pygame.display.set_caption("StrapOS Demo App")
clock = pygame.time.Clock()

FONT = pygame.font.SysFont("segoeui", 22)
start_time = time.time()

x, y = 100.0, 100.0
vx, vy = 220.0, 170.0
radius = 28

running = True
while running:
    dt = clock.tick(60) / 1000.0

    for event in pygame.event.get():
        if event.type == pygame.QUIT:
            running = False
        elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
            running = False

    w, h = screen.get_size()

    x += vx * dt
    y += vy * dt
    if x - radius < 0 or x + radius > w:
        vx *= -1
        x = max(radius, min(w - radius, x))
    if y - radius < 0 or y + radius > h:
        vy *= -1
        y = max(radius, min(h - radius, y))

    t = time.time() - start_time
    bg = (
        int(60 + 60 * math.sin(t * 0.6)),
        int(60 + 60 * math.sin(t * 0.6 + 2)),
        int(70 + 60 * math.sin(t * 0.6 + 4)),
    )
    screen.fill(bg)

    pygame.draw.circle(screen, (255, 255, 255), (int(x), int(y)), radius)
    pygame.draw.circle(screen, (30, 30, 35), (int(x), int(y)), radius, 3)

    label = FONT.render(f"StrapOS Demo App — running for {t:.1f}s", True, (255, 255, 255))
    screen.blit(label, (16, h - 40))

    pygame.display.flip()

pygame.quit()
'''

try:
    os.makedirs(STRAPOS_APPS_DIR, exist_ok=True)
except Exception:
    pass


def get_android_storage_root():
    """
    Resolve the folder the File Manager (and App Loader) should browse.

    On a packaged Android build (Buildozer / python-for-android) this
    resolves to the app's real external-storage files directory, i.e.
    /storage/emulated/0/Android/data/<your.package.name>/files — the same
    folder Android's Context.getExternalFilesDir(None) returns, and the one
    other apps / a USB cable / a file browser can actually see.

    On anything else (desktop testing, Termux, etc.) it falls back through a
    list of sensible candidates, and finally to the home folder.
    """
    # 1) The correct way when actually running as a packaged Android app.
    try:
        from jnius import autoclass  # provided by python-for-android/Buildozer
        PythonActivity = autoclass("org.kivy.android.PythonActivity")
        context = PythonActivity.mActivity
        files_dir = context.getExternalFilesDir(None)
        if files_dir:
            path = files_dir.getAbsolutePath()
            if os.path.isdir(path):
                return path
    except Exception:
        pass

    # 2) Guess the path from the package name if it's exposed as an env var.
    try:
        package_name = os.environ.get("ANDROID_ARGUMENT") or os.environ.get("P4A_PACKAGE_NAME")
        if package_name:
            guess = f"/storage/emulated/0/Android/data/{package_name}/files"
            if os.path.isdir(guess):
                return guess
    except Exception:
        pass

    # 3) Common Android storage roots (also covers Termux and similar).
    for candidate in (
        "/storage/emulated/0/Android/data",
        "/sdcard/Android/data",
        "/storage/emulated/0",
        "/sdcard",
    ):
        if os.path.isdir(candidate):
            return candidate

    # 4) Not on Android at all — fall back to the home folder.
    return HOME_DIR


def _detect_android():
    """
    Best-effort detection of "we are running on Android" — covers Pydroid3,
    Termux, and packaged Buildozer/python-for-android apps. Used to decide
    HOW imported apps get launched (see launch_app / resolve_launch_mode).
    """
    if "ANDROID_ARGUMENT" in os.environ or "ANDROID_PRIVATE" in os.environ:
        return True
    if "ANDROID_ROOT" in os.environ or "ANDROID_DATA" in os.environ:
        return True
    if hasattr(sys, "getandroidapilevel"):
        return True
    try:
        exe_lower = (sys.executable or "").lower()
        if "pydroid" in exe_lower or "com.termux" in exe_lower:
            return True
    except Exception:
        pass
    if os.path.isdir("/system/app") or os.path.isdir("/system/priv-app"):
        return True
    return False


IS_ANDROID = _detect_android()
STORAGE_ROOT = get_android_storage_root()

_display_info = pygame.display.Info()
DEVICE_W, DEVICE_H = _display_info.current_w, _display_info.current_h

# Pick a sensible starting window size: as large as possible while staying
# within ~85% of the device's screen and preserving the base aspect ratio.
_max_w, _max_h = int(DEVICE_W * 0.85), int(DEVICE_H * 0.85)
if _max_w / BASE_ASPECT <= _max_h:
    WIN_W, WIN_H = _max_w, int(_max_w / BASE_ASPECT)
else:
    WIN_H, WIN_W = _max_h, int(_max_h * BASE_ASPECT)
WIN_W, WIN_H = max(WIN_W, 640), max(WIN_H, 400)

is_fullscreen = False
window = pygame.display.set_mode((WIN_W, WIN_H), pygame.RESIZABLE)
pygame.display.set_caption("StrapOS")

# `screen` is the fixed-size virtual canvas every app draws to.
screen = pygame.Surface((BASE_WIDTH, BASE_HEIGHT))
scale = 1.0
offset_x, offset_y = 0, 0
clock = pygame.time.Clock()
pygame.key.set_repeat(300, 30)  # smooth key-repeat for typing in the Terminal app


def recompute_scale():
    """Recalculate the scale factor + letterbox offsets for the current window size."""
    global scale, offset_x, offset_y
    scale = min(WIN_W / BASE_WIDTH, WIN_H / BASE_HEIGHT)
    scaled_w = BASE_WIDTH * scale
    scaled_h = BASE_HEIGHT * scale
    offset_x = (WIN_W - scaled_w) / 2
    offset_y = (WIN_H - scaled_h) / 2


recompute_scale()


def to_virtual(pos):
    """Convert real window/screen mouse coordinates into virtual canvas coordinates."""
    x, y = pos
    return ((x - offset_x) / scale, (y - offset_y) / scale)


def to_real_rect(vrect):
    """Convert a rect in virtual canvas coordinates into real window coordinates —
    needed for pygame.key.set_text_input_rect(), which expects real screen space."""
    return pygame.Rect(
        int(offset_x + vrect.x * scale),
        int(offset_y + vrect.y * scale),
        max(1, int(vrect.w * scale)),
        max(1, int(vrect.h * scale)),
    )


def present():
    """Scale the virtual canvas onto the real window (with letterboxing) and flip."""
    window.fill((0, 0, 0))
    scaled_w = max(1, round(BASE_WIDTH * scale))
    scaled_h = max(1, round(BASE_HEIGHT * scale))
    scaled_surface = pygame.transform.smoothscale(screen, (scaled_w, scaled_h))
    window.blit(scaled_surface, (offset_x, offset_y))
    pygame.display.flip()


def toggle_fullscreen():
    global is_fullscreen, window, WIN_W, WIN_H
    is_fullscreen = not is_fullscreen
    if is_fullscreen:
        window = pygame.display.set_mode((DEVICE_W, DEVICE_H), pygame.FULLSCREEN)
        WIN_W, WIN_H = DEVICE_W, DEVICE_H
    else:
        window = pygame.display.set_mode((WIN_W, WIN_H), pygame.RESIZABLE)
    recompute_scale()


def reinit_strapos_display():
    """
    Rebuild StrapOS's own display window/surface. Needed after an in-process
    app (see _run_app_inprocess) has been running and may have called
    pygame.display.set_mode()/quit() itself, since only one display surface
    can exist at a time in a single process (this is exactly the constraint
    that makes true multi-window apps impossible on Android/Pydroid).
    """
    global window
    try:
        if is_fullscreen:
            window = pygame.display.set_mode((DEVICE_W, DEVICE_H), pygame.FULLSCREEN)
        else:
            window = pygame.display.set_mode((WIN_W, WIN_H), pygame.RESIZABLE)
    except Exception:
        window = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption("StrapOS")
    recompute_scale()
    try:
        pygame.key.set_repeat(300, 30)
    except Exception:
        pass
    pygame.event.clear()


# ---------------------------------------------------------------------------
# In-process app hosting: floating "back to StrapOS" button
# ---------------------------------------------------------------------------
# While a launched app runs in-process (Android/Pydroid), it owns the display
# and its own event loop, so StrapOS has no natural way to interrupt it. To
# fix that, pygame.display.flip()/update() are temporarily patched (only for
# the duration of the child app) to draw a small floating back-arrow button
# on top of whatever the app renders, and to watch for a tap on it. A tap
# raises _ReturnToStrapOS, which unwinds out of the app's loop and back into
# StrapOS — this works regardless of how the app reads input (events or
# polling), since it's driven purely by mouse/touch position each frame.

class _ReturnToStrapOS(Exception):
    """Raised to escape an in-process app's loop when the back button is tapped."""
    pass


_original_display_flip = pygame.display.flip
_original_display_update = pygame.display.update
_original_display_set_mode = pygame.display.set_mode
_original_event_get = pygame.event.get
_original_event_poll = pygame.event.poll
_original_event_wait = pygame.event.wait
_back_btn_state = {"rect": None, "was_pressed": False, "armed_at": 0}


def _draw_back_button_and_check():
    surf = pygame.display.get_surface()
    if surf is None:
        return
    w, h = surf.get_size()
    size = max(46, min(64, int(min(w, h) * 0.13)))
    margin = 12
    rect = pygame.Rect(margin, margin, size, size)
    _back_btn_state["rect"] = rect

    btn = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(btn, (15, 16, 20, 185), (size // 2, size // 2), size // 2)
    pygame.draw.circle(btn, (255, 255, 255, 230), (size // 2, size // 2), size // 2, 2)
    cx, cy = size / 2, size / 2
    pygame.draw.polygon(btn, (255, 255, 255, 235), [
        (cx + size * 0.15, cy - size * 0.22),
        (cx - size * 0.20, cy),
        (cx + size * 0.15, cy + size * 0.22),
    ])
    surf.blit(btn, rect.topleft)

    try:
        pressed = pygame.mouse.get_pressed()[0]
        pos = pygame.mouse.get_pos()
    except Exception:
        pressed, pos = False, (-1, -1)

    # Ignore taps for a brief grace period after launch, and never treat an
    # already-held press (e.g. the finger still down from tapping "Launch")
    # as a NEW tap — only a fresh press-down inside the button counts.
    ready = pygame.time.get_ticks() >= _back_btn_state["armed_at"]
    tapped = ready and pressed and not _back_btn_state["was_pressed"] and rect.collidepoint(pos)
    _back_btn_state["was_pressed"] = pressed
    if tapped:
        raise _ReturnToStrapOS()


def _patched_display_flip():
    _draw_back_button_and_check()
    return _original_display_flip()


def _patched_display_update(*args, **kwargs):
    _draw_back_button_and_check()
    return _original_display_update(*args, **kwargs)


def _patched_display_set_mode(size=(0, 0), flags=0, depth=0, display=0, vsync=0):
    """
    This is the key fix. Android (and Pydroid specifically) really only
    expects ONE display surface to ever exist per process. A hosted app
    calling pygame.display.set_mode() a second time in our shared process —
    which every hosted app does, since it thinks it's starting fresh — can
    silently tear the whole native surface down at the OS level. That's not
    a Python exception, so nothing shows up in the log; StrapOS just quietly
    regains control, which looks exactly like "it opened then instantly
    closed." So instead of ever calling the real set_mode a second time, we
    simply hand the app the surface StrapOS already owns and is already
    displaying — no re-initialization happens at all, at any layer.

    Most simple pygame scripts query surf.get_width()/get_height() rather
    than hardcoding an exact resolution, so this is transparent for them.
    Scripts that hardcode a specific resolution (e.g. 800x600) may appear
    scaled to your actual screen size instead — a fair trade for reliably
    staying open.
    """
    existing = pygame.display.get_surface()
    if existing is not None:
        pygame.event.pump()
        try:
            _original_event_get()  # drain with the REAL get, bypassing our filter below
        except Exception:
            pass
        _back_btn_state["rect"] = None
        _back_btn_state["was_pressed"] = False
        return existing

    # No surface exists yet at all — this is a genuine first-time init, safe
    # to do for real.
    result = _original_display_set_mode(size, flags, depth, display, vsync)
    try:
        pygame.event.pump()
        _original_event_get()
    except Exception:
        pass
    _back_btn_state["rect"] = None
    _back_btn_state["was_pressed"] = False
    return result


def _patched_event_get(*args, **kwargs):
    """Same as pygame.event.get(), but QUIT events never reach the hosted app —
    the only sanctioned way out while in-process is the floating back button."""
    events = _original_event_get(*args, **kwargs)
    if isinstance(events, list):
        return [e for e in events if e.type != pygame.QUIT]
    return events


def _patched_event_poll(*args, **kwargs):
    ev = _original_event_poll(*args, **kwargs)
    if ev is not None and getattr(ev, "type", None) == pygame.QUIT:
        return pygame.event.Event(pygame.NOEVENT)
    return ev


def _patched_event_wait(*args, **kwargs):
    ev = _original_event_wait(*args, **kwargs)
    if ev is not None and getattr(ev, "type", None) == pygame.QUIT:
        return pygame.event.Event(pygame.NOEVENT)
    return ev


FONT_TITLE = pygame.font.SysFont("segoeui", 30, bold=True)
FONT_WELCOME = pygame.font.SysFont("segoeui", 56, bold=True)
FONT_WEATHER_EMOJI = pygame.font.SysFont(None, 108)  # default font resolution — best shot at color emoji glyphs
FONT_WEATHER_TEMP = pygame.font.SysFont("segoeui", 60, bold=True)
FONT_ICON = pygame.font.SysFont("segoeui", 15)
FONT_UI = pygame.font.SysFont("segoeui", 18)
FONT_SMALL = pygame.font.SysFont("segoeui", 14)
FONT_MONO = pygame.font.SysFont("consolas", 15)

WHITE = (255, 255, 255)
BLACK = (20, 20, 20)
GRAY = (90, 90, 90)
LIGHT_GRAY = (230, 230, 230)
SKY_BLUE = (135, 206, 250)

BOOT_HOLD_MS = 1400   # how long "Welcome" holds at full opacity
BOOT_FADE_MS = 900    # how long the fade-to-desktop takes

# Open-Meteo uses WMO weather interpretation codes — map each to an emoji + a
# short description, since the Weather app shows both.
WEATHER_CODES = {
    0: ("☀️", "Clear Sky"),
    1: ("🌤️", "Mainly Clear"),
    2: ("⛅", "Partly Cloudy"),
    3: ("☁️", "Overcast"),
    45: ("🌫️", "Fog"),
    48: ("🌫️", "Rime Fog"),
    51: ("🌦️", "Light Drizzle"),
    53: ("🌦️", "Drizzle"),
    55: ("🌧️", "Dense Drizzle"),
    56: ("🌧️", "Freezing Drizzle"),
    57: ("🌧️", "Freezing Drizzle"),
    61: ("🌧️", "Light Rain"),
    63: ("🌧️", "Rain"),
    65: ("🌧️", "Heavy Rain"),
    66: ("🌧️", "Freezing Rain"),
    67: ("🌧️", "Freezing Rain"),
    71: ("🌨️", "Light Snow"),
    73: ("🌨️", "Snow"),
    75: ("❄️", "Heavy Snow"),
    77: ("🌨️", "Snow Grains"),
    80: ("🌦️", "Rain Showers"),
    81: ("🌧️", "Rain Showers"),
    82: ("⛈️", "Violent Showers"),
    85: ("🌨️", "Snow Showers"),
    86: ("❄️", "Heavy Snow Showers"),
    95: ("⛈️", "Thunderstorm"),
    96: ("⛈️", "Thunderstorm + Hail"),
    99: ("⛈️", "Severe Thunderstorm"),
}

# ---------------------------------------------------------------------------
# Wallpapers - mint green (default) + other colors, same arch design
# ---------------------------------------------------------------------------

WALLPAPERS = [
    {"name": "Mint Green",    "base": (163, 217, 195), "accent": (108, 168, 143)},
    {"name": "Blush Pink",    "base": (241, 200, 210), "accent": (205, 143, 158)},
    {"name": "Sky Blue",      "base": (180, 214, 240), "accent": (117, 160, 201)},
    {"name": "Lavender",      "base": (213, 201, 240), "accent": (159, 137, 202)},
    {"name": "Sunset Orange", "base": (246, 191, 150), "accent": (211, 130, 79)},
    {"name": "Charcoal",      "base": (78, 82, 92),    "accent": (48, 51, 60)},
]


def clamp(v, lo=0, hi=255):
    return max(lo, min(hi, v))


def shade(color, amt):
    return (clamp(color[0] + amt), clamp(color[1] + amt), clamp(color[2] + amt))


def draw_arch(surface, x, y, w, h, color, line_w=3):
    """Draws a single moroccan-style arch outline (used to build the wallpaper)."""
    top_h = h * 0.55
    rect = pygame.Rect(int(x), int(y), int(w), int(top_h * 2))
    try:
        pygame.draw.arc(surface, color, rect, math.pi, 2 * math.pi, line_w)
    except Exception:
        pass
    pygame.draw.line(surface, color, (x, y + top_h), (x, y + h), line_w)
    pygame.draw.line(surface, color, (x + w, y + top_h), (x + w, y + h), line_w)
    pygame.draw.line(surface, color, (x, y + h), (x + w, y + h), line_w)
    # small inner arch for detail
    inset = w * 0.22
    inner_top = y + top_h * 0.35
    inner_rect = pygame.Rect(int(x + inset), int(inner_top), int(w - inset * 2), int((top_h - inner_top + y) * 2))


def draw_arch_wallpaper(surface, base_color, accent_color):
    w, h = surface.get_size()
    surface.fill(base_color)

    arch_w, arch_h = 120, 170
    gap_x, gap_y = 26, 18
    step_x = arch_w + gap_x
    step_y = arch_h + gap_y

    line_color = shade(accent_color, 25)
    faint_color = shade(base_color, -14)

    row = 0
    y = -arch_h
    while y < h + arch_h:
        offset = step_x // 2 if row % 2 else 0
        x = -arch_w - offset
        while x < w + arch_w:
            draw_arch(surface, x, y, arch_w, arch_h, faint_color, 2)
            x += step_x
        y += step_y
        row += 1

    # subtle diagonal accent stripes for extra "arch design" texture
    for i in range(-h, w, 90):
        pygame.draw.line(surface, shade(accent_color, 10), (i, 0), (i + h, h), 1)


# ---------------------------------------------------------------------------
# Config persistence (this is StrapOS's little "sync" of your preferences)
# ---------------------------------------------------------------------------

def load_config():
    default = {
        "wallpaper_index": 0, "clock_format": "24", "installed_apps": [],
        "launch_mode": "auto", "boot_hold_ms": BOOT_HOLD_MS,
        "wallpaper_kind": "builtin", "custom_wallpaper_path": None, "custom_wallpapers": [],
        "pin_hash": None, "pin_salt": None,
        "weather_last_city": "",
    }
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r") as f:
                data = json.load(f)
                merged = dict(default)
                merged.update(data)
                if not isinstance(merged.get("installed_apps"), list):
                    merged["installed_apps"] = []
                if not isinstance(merged.get("custom_wallpapers"), list):
                    merged["custom_wallpapers"] = []
                return merged
        except Exception:
            pass
    return default


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w") as f:
            json.dump(cfg, f)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Small drawing helpers for icons (no external image assets needed)
# ---------------------------------------------------------------------------

def draw_folder_icon(surf, rect, color=(255, 200, 90)):
    x, y, w, h = rect
    tab_w = w * 0.45
    pygame.draw.rect(surf, shade(color, -25), (x, y + h * 0.12, tab_w, h * 0.18), border_radius=4)
    pygame.draw.rect(surf, color, (x, y + h * 0.28, w, h * 0.62), border_radius=6)
    pygame.draw.rect(surf, shade(color, -35), (x, y + h * 0.28, w, h * 0.62), 2, border_radius=6)


def draw_file_icon(surf, rect, color=(235, 235, 245)):
    x, y, w, h = rect
    pygame.draw.rect(surf, color, (x, y, w, h), border_radius=4)
    pygame.draw.rect(surf, shade(color, -60), (x, y, w, h), 2, border_radius=4)
    for i in range(3):
        ly = y + h * 0.3 + i * h * 0.18
        pygame.draw.line(surf, shade(color, -70), (x + w * 0.15, ly), (x + w * 0.85, ly), 2)


def draw_gear_icon(surf, rect, color=(210, 210, 215)):
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    r_outer = min(w, h) / 2
    r_inner = r_outer * 0.55
    teeth = 8
    pts = []
    for i in range(teeth * 2):
        ang = (2 * math.pi / (teeth * 2)) * i
        r = r_outer if i % 2 == 0 else r_outer * 0.8
        pts.append((cx + r * math.cos(ang), cy + r * math.sin(ang)))
    pygame.draw.polygon(surf, color, pts)
    pygame.draw.circle(surf, shade(color, -70), (int(cx), int(cy)), int(r_inner))
    pygame.draw.circle(surf, color, (int(cx), int(cy)), int(r_inner * 0.45))


def draw_calculator_icon(surf, rect, color=(232, 232, 236)):
    x, y, w, h = rect
    pygame.draw.rect(surf, color, (x, y, w, h), border_radius=8)
    pygame.draw.rect(surf, shade(color, -60), (x, y, w, h), 2, border_radius=8)
    screen_rect = pygame.Rect(x + w * 0.12, y + h * 0.1, w * 0.76, h * 0.22)
    pygame.draw.rect(surf, shade(color, -50), screen_rect, border_radius=3)
    btn = w * 0.16
    gap_x = (w * 0.76 - btn * 3) / 2
    gap_y = h * 0.09
    start_x = x + w * 0.12
    start_y = screen_rect.bottom + h * 0.08
    for r in range(3):
        for c in range(3):
            bx = start_x + c * (btn + gap_x)
            by = start_y + r * (btn + gap_y)
            pygame.draw.rect(surf, shade(color, -25), (bx, by, btn, btn), border_radius=2)


def draw_terminal_icon(surf, rect, color=(38, 40, 46)):
    x, y, w, h = rect
    pygame.draw.rect(surf, color, (x, y, w, h), border_radius=8)
    pygame.draw.rect(surf, shade(color, 60), (x, y, w, h), 2, border_radius=8)
    p1 = (x + w * 0.18, y + h * 0.34)
    p2 = (x + w * 0.4, y + h * 0.5)
    p3 = (x + w * 0.18, y + h * 0.66)
    pygame.draw.lines(surf, (120, 230, 140), False, [p1, p2, p3], 3)
    pygame.draw.line(surf, (120, 230, 140), (x + w * 0.46, y + h * 0.66), (x + w * 0.78, y + h * 0.66), 3)


def draw_upload_icon(surf, rect, color=(52, 110, 235)):
    """Strap App Loader icon: blue background + an upload arrow into a tray."""
    x, y, w, h = rect
    pygame.draw.rect(surf, color, (x, y, w, h), border_radius=18)
    pygame.draw.rect(surf, shade(color, -40), (x, y, w, h), 2, border_radius=18)

    cx = x + w / 2
    shaft_top = y + h * 0.20
    shaft_bottom = y + h * 0.56
    pygame.draw.line(surf, WHITE, (cx, shaft_bottom), (cx, shaft_top), 5)

    head_w = w * 0.20
    head_h = h * 0.14
    pts = [(cx - head_w, shaft_top + head_h), (cx, shaft_top), (cx + head_w, shaft_top + head_h)]
    pygame.draw.polygon(surf, WHITE, pts)

    tray_y = y + h * 0.72
    pygame.draw.line(surf, WHITE, (x + w * 0.22, tray_y), (x + w * 0.78, tray_y), 5)
    pygame.draw.line(surf, WHITE, (x + w * 0.22, tray_y), (x + w * 0.22, tray_y + h * 0.12), 5)
    pygame.draw.line(surf, WHITE, (x + w * 0.78, tray_y), (x + w * 0.78, tray_y + h * 0.12), 5)


def draw_pyapp_icon(surf, rect, color=(120, 120, 200)):
    """Generic icon used for user-imported .py apps."""
    x, y, w, h = rect
    pygame.draw.rect(surf, color, (x, y, w, h), border_radius=16)
    pygame.draw.rect(surf, shade(color, -45), (x, y, w, h), 2, border_radius=16)
    txt = FONT_UI.render("PY", True, WHITE)
    surf.blit(txt, txt.get_rect(center=(x + w / 2, y + h / 2)))


def draw_browser_icon(surf, rect, color=(41, 121, 226)):
    """Browser icon: blue background + a white globe with blue grid lines."""
    x, y, w, h = rect
    pygame.draw.rect(surf, color, (x, y, w, h), border_radius=18)
    pygame.draw.rect(surf, shade(color, -40), (x, y, w, h), 2, border_radius=18)

    cx, cy = x + w / 2, y + h / 2
    r = min(w, h) * 0.34

    pygame.draw.circle(surf, WHITE, (int(cx), int(cy)), int(r))
    pygame.draw.circle(surf, shade(color, -15), (int(cx), int(cy)), int(r), 2)

    pygame.draw.line(surf, color, (cx - r, cy), (cx + r, cy), 2)
    pygame.draw.line(surf, color, (cx, cy - r), (cx, cy + r), 2)

    lat_rect = pygame.Rect(cx - r, cy - r * 0.5, r * 2, r)
    pygame.draw.ellipse(surf, color, lat_rect, 2)
    long_rect = pygame.Rect(cx - r * 0.5, cy - r, r, r * 2)
    pygame.draw.ellipse(surf, color, long_rect, 2)


def draw_power_icon(surf, rect, color=(255, 255, 255)):
    """Standard power-button glyph: a circle with a gap at the top + a line
    poking through the gap."""
    x, y, w, h = rect
    cx, cy = x + w / 2, y + h / 2
    r = min(w, h) / 2 - 2
    bounding = pygame.Rect(cx - r, cy - r, r * 2, r * 2)
    pygame.draw.arc(surf, color, bounding, math.radians(55), math.radians(305), 3)
    pygame.draw.line(surf, color, (cx, cy - r - 1), (cx, cy - r * 0.1), 3)


def draw_weather_icon(surf, rect):
    """Relaxing-themed weather icon: soft pastel sky, a warm sun peeking
    out from behind a fluffy white cloud."""
    x, y, w, h = rect
    bg = (176, 216, 235)
    pygame.draw.rect(surf, bg, (x, y, w, h), border_radius=18)
    pygame.draw.rect(surf, shade(bg, -30), (x, y, w, h), 2, border_radius=18)

    sun_color = (255, 205, 90)
    sun_cx, sun_cy = x + w * 0.62, y + h * 0.4
    sun_r = w * 0.20
    for ang in range(0, 360, 45):
        rad = math.radians(ang)
        x1 = sun_cx + math.cos(rad) * sun_r * 1.15
        y1 = sun_cy + math.sin(rad) * sun_r * 1.15
        x2 = sun_cx + math.cos(rad) * sun_r * 1.45
        y2 = sun_cy + math.sin(rad) * sun_r * 1.45
        pygame.draw.line(surf, sun_color, (x1, y1), (x2, y2), 3)
    pygame.draw.circle(surf, sun_color, (int(sun_cx), int(sun_cy)), int(sun_r))

    cx, cy = x + w * 0.42, y + h * 0.62
    cloud_color = (255, 255, 255)
    puffs = [
        (cx - w * 0.30, cy - h * 0.14, w * 0.42, h * 0.28),
        (cx - w * 0.12, cy - h * 0.26, w * 0.34, h * 0.32),
        (cx + w * 0.05, cy - h * 0.14, w * 0.34, h * 0.26),
    ]
    for px, py, pw, ph in puffs:
        pygame.draw.ellipse(surf, cloud_color, (px, py, pw, ph))
    pygame.draw.ellipse(surf, shade(cloud_color, -25), puffs[0], 2)


# ---------------------------------------------------------------------------
# Safe arithmetic evaluator for the Calculator app (no eval(), no code exec)
# ---------------------------------------------------------------------------

import ast as _ast
import operator as _operator

_SAFE_OPERATORS = {
    _ast.Add: _operator.add,
    _ast.Sub: _operator.sub,
    _ast.Mult: _operator.mul,
    _ast.Div: _operator.truediv,
    _ast.Mod: _operator.mod,
    _ast.Pow: _operator.pow,
    _ast.USub: _operator.neg,
    _ast.UAdd: _operator.pos,
}


def safe_eval(expression):
    """Evaluate a simple arithmetic expression (+ - * / % ** parentheses) safely."""

    def _eval(node):
        if isinstance(node, _ast.Expression):
            return _eval(node.body)
        if isinstance(node, _ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError("invalid constant")
        if isinstance(node, _ast.BinOp) and type(node.op) in _SAFE_OPERATORS:
            return _SAFE_OPERATORS[type(node.op)](_eval(node.left), _eval(node.right))
        if isinstance(node, _ast.UnaryOp) and type(node.op) in _SAFE_OPERATORS:
            return _SAFE_OPERATORS[type(node.op)](_eval(node.operand))
        raise ValueError("unsupported expression")

    parsed = _ast.parse(expression, mode="eval")
    return _eval(parsed)


# ---------------------------------------------------------------------------
# UI helper widgets
# ---------------------------------------------------------------------------

class Button:
    def __init__(self, rect, text, on_click, bg=(255, 255, 255), fg=BLACK, font=None):
        self.rect = pygame.Rect(rect)
        self.text = text
        self.on_click = on_click
        self.bg = bg
        self.fg = fg
        self.font = font or FONT_UI

    def draw(self, surf, hovered):
        color = shade(self.bg, -15) if hovered else self.bg
        pygame.draw.rect(surf, color, self.rect, border_radius=8)
        pygame.draw.rect(surf, shade(color, -40), self.rect, 1, border_radius=8)
        label = self.font.render(self.text, True, self.fg)
        surf.blit(label, label.get_rect(center=self.rect.center))

    def hit(self, pos):
        return self.rect.collidepoint(pos)


# ---------------------------------------------------------------------------
# StrapOS application
# ---------------------------------------------------------------------------

class StrapOS:
    STATE_DESKTOP = "desktop"
    STATE_FILES = "files"
    STATE_SETTINGS = "settings"
    STATE_CALC = "calculator"
    STATE_TERMINAL = "terminal"
    STATE_APPLOADER = "apploader"
    STATE_WEATHER = "weather"

    ICON_LEFT = 40
    ICON_TOP = 50
    ICON_ROW_STEP = 130
    ICON_COL_STEP = 140
    ICONS_PER_COL = 4
    APP_ICON_PALETTE = [
        (235, 120, 110), (110, 170, 235), (160, 120, 225),
        (110, 200, 160), (230, 175, 90), (90, 190, 210),
    ]
    TASKBAR_PINNED_APPS = [
        ("File Manager", draw_folder_icon, STATE_FILES),
        ("Settings", draw_gear_icon, STATE_SETTINGS),
        ("Calculator", draw_calculator_icon, STATE_CALC),
        ("Terminal", draw_terminal_icon, STATE_TERMINAL),
    ]
    BOOT_SLIDER_MIN_MS = 0
    BOOT_SLIDER_MAX_MS = 5000

    def __init__(self):
        self.cfg = load_config()
        self.wallpaper_index = self.cfg.get("wallpaper_index", 0) % len(WALLPAPERS)
        self.wallpaper_kind = self.cfg.get("wallpaper_kind", "builtin")
        self.wallpaper_custom_path = self.cfg.get("custom_wallpaper_path")
        self.custom_wallpapers = self.cfg.get("custom_wallpapers", [])
        if not isinstance(self.custom_wallpapers, list):
            self.custom_wallpapers = []
        self.clock_format = self.cfg.get("clock_format", "24")
        self.state = self.STATE_DESKTOP

        self.wallpaper_surface = pygame.Surface((BASE_WIDTH, BASE_HEIGHT))
        self._rebuild_wallpaper()

        # Wallpaper picker (Settings → Wallpaper → Upload) state
        self.wallpaper_picker_open = False
        self.wallpaper_picker_path = STORAGE_ROOT
        self.wallpaper_picker_entries = []
        self.wallpaper_picker_scroll = 0
        self.settings_scroll = 0  # Settings screen is scrollable
        self._wallpaper_thumb_cache = {}
        self.reset_confirm_open = False  # Settings → Software Reset confirmation dialog

        # Power system: menu, lock screen, shutdown fade
        self.power_menu_open = False
        self.lock_screen_active = False
        self.lock_screen_purpose = None  # "boot" | "manual"
        self.shutting_down = False
        self.shutdown_start = 0
        self._taskbar_power_rect = None
        self._power_menu_rects = []
        self._power_menu_box = None

        # Security: optional PIN. If set, StrapOS won't proceed past the
        # lock screen until it's entered correctly — including at startup.
        self.pin_hash = self.cfg.get("pin_hash")
        self.pin_salt = self.cfg.get("pin_salt")
        self.pin_entry_input = ""
        self.pin_entry_error = ""
        self._pin_pad_rects = {}
        self.pin_setup_open = False       # Settings → Security modal
        self.pin_setup_stage = None       # "new" | "confirm" | "remove"
        self.pin_setup_input = ""
        self.pin_setup_first_value = None
        self.pin_setup_error = ""
        self._pin_setup_pad_rects = {}
        self._pin_setup_cancel_rect = None
        self._st_security_rects = []

        # Weather app: Open-Meteo, fetched on a background thread so a slow
        # network call never freezes the rest of the OS.
        self.weather_last_city = self.cfg.get("weather_last_city", "")
        self.weather_city_query = self.weather_last_city
        self.weather_data = None
        self.weather_error = None
        self.weather_loading = False
        self.weather_thread = None
        self._weather_auto_fetched = False
        self._weather_bg_cache = None
        self._weather_bg_cache_key = None
        self._weather_close_rect = None
        self._weather_go_rect = None
        self._weather_refresh_rect = None

        # File manager state — synced to Android's app-specific external
        # storage (Android/data/<package>/files) instead of the process home
        self.current_path = STORAGE_ROOT
        self.entries = []
        self.scroll = 0
        self.selected_file_content = None
        self.selected_file_name = None
        self.status_message = ""
        self._refresh_entries()

        # File manager: "Upload" picker (copies a file into the current folder)
        self.fm_upload_open = False
        self.fm_upload_path = STORAGE_ROOT
        self.fm_upload_entries = []
        self.fm_upload_scroll = 0

        # Calculator state
        self.calc_expression = ""
        self.calc_error = False

        # Terminal state
        self.term_prompt = "strapos@device:~$ "
        self.term_input = ""
        self.term_history = [
            "StrapOS Terminal",
            'Type "test" to make sure everything is working fine.',
            "",
        ]
        self.term_running_proc = None   # subprocess.Popen of a command launched from here, if any
        self.term_running_name = None
        self._term_close_running_rect = None

        # Strap App Loader state
        self.installed_apps = self.cfg.get("installed_apps", [])
        if not isinstance(self.installed_apps, list):
            self.installed_apps = []
        self.loader_path = STORAGE_ROOT
        self.loader_entries = []
        self.loader_scroll = 0
        self.loader_apps_scroll = 0
        self._loader_refresh_entries()
        self.launch_watch = []       # tracks recently-launched subprocesses to catch instant crashes
        self.loader_log_view = None  # {"name": ..., "content": ...} when the log overlay is open
        self.launch_mode = self.cfg.get("launch_mode", "auto")   # "auto" | "inprocess" | "subprocess"
        self.pending_inprocess_launch = None  # set right before we hand the window to a child app

        # toast notifications (used across every screen)
        self.toast_message = ""
        self.toast_ok = True
        self.toast_until = 0

        self._build_desktop_icons()
        self._taskbar_pinned_rects = []
        self.buttons = []  # rebuilt each frame per screen

        # boot splash: sky blue "Welcome" screen that holds, then fades out.
        # If a PIN is set, the lock screen gates this — booting only actually
        # starts once the PIN is entered correctly (see _unlock_lock_screen).
        self.boot_hold_ms = self.cfg.get("boot_hold_ms", BOOT_HOLD_MS)
        self.boot_hold_ms = max(self.BOOT_SLIDER_MIN_MS, min(self.BOOT_SLIDER_MAX_MS, self.boot_hold_ms))
        if self.pin_hash:
            self.lock_screen_active = True
            self.lock_screen_purpose = "boot"
            self.booting = False
            self.boot_start = 0
        else:
            self.booting = True
            self.boot_start = pygame.time.get_ticks()
        self.slider_dragging = False  # Settings → Boot Screen slider drag state

        # Touch/drag scrolling for mobile — mouse wheel doesn't exist on
        # touchscreens, so scrollable lists also support drag-to-scroll.
        self._drag_scroll = {"active": False, "area_key": None, "start_pos": (0, 0),
                              "last_pos": (0, 0), "moved": False}
        self.DRAG_SCROLL_THRESHOLD = 6  # px of movement before a press counts as a drag, not a tap

    # ---------------- desktop icon layout ----------------
    def _build_desktop_icons(self):
        base_apps = [
            {"label": "File Manager", "draw": draw_folder_icon, "action": lambda: self.open_app(self.STATE_FILES)},
            {"label": "Settings", "draw": draw_gear_icon, "action": lambda: self.open_app(self.STATE_SETTINGS)},
            {"label": "Calculator", "draw": draw_calculator_icon, "action": lambda: self.open_app(self.STATE_CALC)},
            {"label": "Terminal", "draw": draw_terminal_icon, "action": lambda: self.open_app(self.STATE_TERMINAL)},
            {"label": "Strap App Loader", "draw": draw_upload_icon,
             "action": lambda: self.open_app(self.STATE_APPLOADER)},
            {"label": "Browser", "draw": draw_browser_icon, "action": lambda: self.open_real_browser()},
            {"label": "Weather", "draw": draw_weather_icon, "action": lambda: self.open_app(self.STATE_WEATHER)},
        ]

        dynamic_apps = []
        for i, app in enumerate(self.installed_apps):
            color = self.APP_ICON_PALETTE[i % len(self.APP_ICON_PALETTE)]
            dynamic_apps.append({
                "label": app.get("name", "App"),
                "draw": (lambda surf, rect, c=color: draw_pyapp_icon(surf, rect, c)),
                "action": (lambda p=app.get("path"), n=app.get("name", "App"): self.launch_app(p, n)),
            })

        all_apps = base_apps + dynamic_apps
        icon_w, icon_h = 96, 96
        icons = []
        for i, app in enumerate(all_apps):
            col = i // self.ICONS_PER_COL
            row = i % self.ICONS_PER_COL
            rect = pygame.Rect(
                self.ICON_LEFT + col * self.ICON_COL_STEP,
                self.ICON_TOP + row * self.ICON_ROW_STEP,
                icon_w, icon_h,
            )
            icons.append({"label": app["label"], "rect": rect, "draw": app["draw"], "action": app["action"]})
        self.desktop_icons = icons

    # ---------------- wallpaper ----------------
    def _rebuild_wallpaper(self):
        if self.wallpaper_kind == "custom" and self.wallpaper_custom_path:
            try:
                self.wallpaper_surface = self._load_custom_wallpaper_surface(self.wallpaper_custom_path)
                return
            except Exception:
                # the file may have been moved/deleted since — fall back gracefully
                self.wallpaper_kind = "builtin"
                self.wallpaper_custom_path = None
        wp = WALLPAPERS[self.wallpaper_index]
        surf = pygame.Surface((BASE_WIDTH, BASE_HEIGHT))
        draw_arch_wallpaper(surf, wp["base"], wp["accent"])
        self.wallpaper_surface = surf

    def _load_custom_wallpaper_surface(self, path):
        """Load an image file and scale/crop it to fill the whole canvas
        (cover-fit: scale to cover, then center-crop any excess)."""
        return self._load_and_cover_fit(path, BASE_WIDTH, BASE_HEIGHT)

    def set_wallpaper(self, idx):
        self.wallpaper_index = idx % len(WALLPAPERS)
        self.wallpaper_kind = "builtin"
        self.wallpaper_custom_path = None
        self._rebuild_wallpaper()
        self.cfg["wallpaper_index"] = self.wallpaper_index
        self.cfg["wallpaper_kind"] = "builtin"
        self.cfg["custom_wallpaper_path"] = None
        save_config(self.cfg)

    def set_custom_wallpaper(self, path):
        self.wallpaper_kind = "custom"
        self.wallpaper_custom_path = path
        self._rebuild_wallpaper()
        self.cfg["wallpaper_kind"] = "custom"
        self.cfg["custom_wallpaper_path"] = path
        save_config(self.cfg)

    def set_clock_format(self, fmt):
        self.clock_format = fmt
        self.cfg["clock_format"] = fmt
        save_config(self.cfg)

    def set_launch_mode(self, mode):
        self.launch_mode = mode
        self.cfg["launch_mode"] = mode
        save_config(self.cfg)

    def resolve_launch_mode(self):
        if self.launch_mode in ("inprocess", "subprocess"):
            return self.launch_mode
        return "inprocess" if IS_ANDROID else "subprocess"

    def set_boot_hold_ms(self, ms):
        self.boot_hold_ms = max(self.BOOT_SLIDER_MIN_MS, min(self.BOOT_SLIDER_MAX_MS, ms))
        self.cfg["boot_hold_ms"] = self.boot_hold_ms
        save_config(self.cfg)

    def perform_factory_reset(self):
        """Wipes every persisted trace of StrapOS (config, imported apps, logs,
        custom wallpapers) and rebuilds the whole instance from scratch — same
        state as a first-ever launch, boot screen and all."""
        self._set_text_input_active(False)

        if self.term_running_proc is not None:
            try:
                self.term_running_proc.terminate()
            except Exception:
                pass
        for entry in self.launch_watch:
            try:
                entry["proc"].terminate()
            except Exception:
                pass
        self.launch_watch = []

        for path, is_dir in (
            (CONFIG_PATH, False),
            (STRAPOS_APPS_DIR, True),
            (STRAPOS_WALLPAPERS_DIR, True),
        ):
            try:
                if is_dir and os.path.isdir(path):
                    shutil.rmtree(path)
                elif not is_dir and os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
        try:
            os.makedirs(STRAPOS_LOGS_DIR, exist_ok=True)
            os.makedirs(STRAPOS_WALLPAPERS_DIR, exist_ok=True)
        except Exception:
            pass

        self.__init__()
        self.show_toast("StrapOS has been reset.", ok=True)

    # ---------------- wallpaper picker (Settings → Wallpaper → Upload) ----------------
    def open_wallpaper_picker(self):
        self.wallpaper_picker_open = True
        self.wallpaper_picker_path = STORAGE_ROOT
        self._wp_picker_refresh_entries()

    def close_wallpaper_picker(self):
        self.wallpaper_picker_open = False

    def _wp_picker_refresh_entries(self):
        self.wallpaper_picker_scroll = 0
        try:
            names = os.listdir(self.wallpaper_picker_path)
        except Exception:
            self.wallpaper_picker_entries = []
            return
        dirs, files = [], []
        for n in names:
            full = os.path.join(self.wallpaper_picker_path, n)
            try:
                if os.path.isdir(full):
                    dirs.append(n)
                elif n.lower().endswith(IMAGE_EXTENSIONS):
                    files.append(n)
            except Exception:
                continue
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)
        self.wallpaper_picker_entries = [("dir", d) for d in dirs] + [("image", f) for f in files]

    def wp_picker_go_up(self):
        parent = os.path.dirname(self.wallpaper_picker_path.rstrip(os.sep))
        if parent and os.path.isdir(parent):
            self.wallpaper_picker_path = parent
            self._wp_picker_refresh_entries()

    def wp_picker_go_into(self, name):
        full = os.path.join(self.wallpaper_picker_path, name)
        if os.path.isdir(full):
            self.wallpaper_picker_path = full
            self._wp_picker_refresh_entries()

    def import_custom_wallpaper(self, name):
        src = os.path.abspath(os.path.join(self.wallpaper_picker_path, name))
        if not os.path.isfile(src):
            self.show_toast(f"'{name}' not found.", ok=False)
            return

        # Fail fast if it's not actually a loadable image, before touching disk.
        try:
            pygame.image.load(src)
        except Exception as e:
            self.show_toast(f"Couldn't load image: {e}", ok=False)
            return

        try:
            os.makedirs(STRAPOS_WALLPAPERS_DIR, exist_ok=True)

            existing = next((w for w in self.custom_wallpapers if w.get("source") == src), None)
            if existing is not None:
                self.set_custom_wallpaper(existing["path"])
                self.close_wallpaper_picker()
                self.show_toast(f"Wallpaper set to '{existing['name']}'.", ok=True)
                return

            stem, ext = os.path.splitext(os.path.basename(src))
            dest = os.path.join(STRAPOS_WALLPAPERS_DIR, os.path.basename(src))
            counter = 1
            while os.path.exists(dest):
                dest = os.path.join(STRAPOS_WALLPAPERS_DIR, f"{stem}_{counter}{ext}")
                counter += 1
            shutil.copy2(src, dest)

            self.custom_wallpapers.append({"name": stem, "path": dest, "source": src})
            while len(self.custom_wallpapers) > MAX_CUSTOM_WALLPAPERS:
                evicted = self.custom_wallpapers.pop(0)
                try:
                    if os.path.exists(evicted["path"]):
                        os.remove(evicted["path"])
                except Exception:
                    pass
            self.cfg["custom_wallpapers"] = self.custom_wallpapers
            save_config(self.cfg)

            self.set_custom_wallpaper(dest)
            self.close_wallpaper_picker()
            self.show_toast("Wallpaper updated!", ok=True)
        except Exception as e:
            self.show_toast(f"Couldn't set wallpaper: {e}", ok=False)

    def remove_custom_wallpaper(self, entry):
        self.custom_wallpapers = [w for w in self.custom_wallpapers if w.get("path") != entry.get("path")]
        self.cfg["custom_wallpapers"] = self.custom_wallpapers
        save_config(self.cfg)
        try:
            if entry.get("path") and os.path.exists(entry["path"]):
                os.remove(entry["path"])
        except Exception:
            pass
        self._wallpaper_thumb_cache = {
            k: v for k, v in self._wallpaper_thumb_cache.items() if k[0] != entry.get("path")
        }
        if self.wallpaper_kind == "custom" and self.wallpaper_custom_path == entry.get("path"):
            self.set_wallpaper(0)  # fall back to the default builtin wallpaper
        self.show_toast(f"Removed '{entry.get('name', 'wallpaper')}'.", ok=True)

    # ---------------- navigation ----------------
    def open_app(self, state):
        self._set_text_input_active(state in (self.STATE_TERMINAL, self.STATE_WEATHER))
        self._drag_scroll["active"] = False
        self.state = state
        if state == self.STATE_FILES:
            self._refresh_entries()
        if (state == self.STATE_WEATHER and not self._weather_auto_fetched
                and self.weather_last_city and self.weather_data is None):
            self._weather_auto_fetched = True
            self.open_weather_search(self.weather_last_city)

    def go_home(self):
        self._set_text_input_active(False)
        self._drag_scroll["active"] = False
        self.state = self.STATE_DESKTOP

    def _set_text_input_active(self, active):
        """Tell SDL whether text entry is happening right now — this is what
        actually makes Android's on-screen keyboard show/hide itself."""
        try:
            if active:
                pygame.key.start_text_input()
            else:
                pygame.key.stop_text_input()
        except Exception:
            pass

    # ---------------- file manager logic ----------------
    def _refresh_entries(self):
        self.selected_file_content = None
        self.selected_file_name = None
        self.status_message = ""
        try:
            names = os.listdir(self.current_path)
        except Exception as e:
            self.entries = []
            self.status_message = f"Cannot open this folder ({e})"
            return
        dirs, files = [], []
        for n in names:
            full = os.path.join(self.current_path, n)
            try:
                if os.path.isdir(full):
                    dirs.append(n)
                else:
                    files.append(n)
            except Exception:
                continue
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)
        self.entries = [("dir", d) for d in dirs] + [("file", f) for f in files]
        self.scroll = 0

    def go_up(self):
        parent = os.path.dirname(self.current_path.rstrip(os.sep))
        if parent and os.path.isdir(parent):
            self.current_path = parent
            self._refresh_entries()

    def go_into(self, name):
        full = os.path.join(self.current_path, name)
        if os.path.isdir(full):
            self.current_path = full
            self._refresh_entries()

    def open_file_preview(self, name):
        full = os.path.join(self.current_path, name)
        self.selected_file_name = name
        text_exts = (".txt", ".py", ".md", ".json", ".csv", ".log", ".ini", ".cfg", ".yml", ".yaml", ".html", ".css", ".js")
        if name.lower().endswith(text_exts):
            try:
                with open(full, "r", errors="replace") as f:
                    content = f.read(4000)
                self.selected_file_content = content if content else "(empty file)"
            except Exception as e:
                self.selected_file_content = f"Could not read file: {e}"
        else:
            try:
                size = os.path.getsize(full)
                self.selected_file_content = f"(no text preview available)\n\nFile size: {size:,} bytes"
            except Exception:
                self.selected_file_content = "(no preview available)"

    # ---------------- drawing: desktop ----------------
    def draw_desktop(self, mouse_pos):
        screen.blit(self.wallpaper_surface, (0, 0))

        for icon in self.desktop_icons:
            r = icon["rect"]
            hovered = r.collidepoint(mouse_pos)
            panel = pygame.Rect(r.x - 8, r.y - 8, r.w + 16, r.h + 40)
            if hovered:
                s = pygame.Surface((panel.w, panel.h), pygame.SRCALPHA)
                pygame.draw.rect(s, (255, 255, 255, 60), s.get_rect(), border_radius=14)
                screen.blit(s, panel.topleft)
            icon["draw"](screen, (r.x, r.y, r.w, r.h * 0.72))
            label = FONT_ICON.render(icon["label"], True, WHITE)
            shadow = FONT_ICON.render(icon["label"], True, (0, 0, 0))
            label_rect = label.get_rect(center=(r.centerx, r.y + r.h * 0.72 + 16))
            screen.blit(shadow, label_rect.move(1, 1))
            screen.blit(label, label_rect)

        self.draw_taskbar("StrapOS Desktop")

    def handle_desktop_click(self, pos):
        for icon in self.desktop_icons:
            if icon["rect"].inflate(16, 40).collidepoint(pos):
                icon["action"]()
                return

    # ---------------- drawing: taskbar ----------------
    def draw_taskbar(self, title):
        bar = pygame.Rect(0, BASE_HEIGHT - TASKBAR_H, BASE_WIDTH, TASKBAR_H)
        s = pygame.Surface((BASE_WIDTH, TASKBAR_H), pygame.SRCALPHA)
        pygame.draw.rect(s, (20, 20, 25, 210), s.get_rect())
        screen.blit(s, bar.topleft)

        logo = FONT_UI.render("🟢 StrapOS", True, WHITE)
        screen.blit(logo, (16, BASE_HEIGHT - TASKBAR_H + 12))

        self._draw_taskbar_pinned(16 + logo.get_width() + 22)

        title_render = FONT_SMALL.render(title, True, (200, 200, 200))
        screen.blit(title_render, (BASE_WIDTH // 2 - title_render.get_width() // 2, BASE_HEIGHT - TASKBAR_H + 15))

        clock_str = self.format_clock()
        clock_text = FONT_SMALL.render(clock_str, True, WHITE)
        clock_x = BASE_WIDTH - clock_text.get_width() - 16
        screen.blit(clock_text, (clock_x, BASE_HEIGHT - TASKBAR_H + 15))

        self._draw_taskbar_power(clock_x)

        self._draw_toast()

    def _draw_taskbar_power(self, clock_x):
        mouse_pos = to_virtual(pygame.mouse.get_pos())
        size = 24
        rect = pygame.Rect(clock_x - size - 14, BASE_HEIGHT - TASKBAR_H + (TASKBAR_H - size) // 2, size, size)
        hit_rect = rect.inflate(10, 10)
        hovered = hit_rect.collidepoint(mouse_pos)
        if hovered or self.power_menu_open:
            hs = pygame.Surface((hit_rect.w, hit_rect.h), pygame.SRCALPHA)
            pygame.draw.rect(hs, (255, 255, 255, 65 if self.power_menu_open else 40), hs.get_rect(), border_radius=8)
            screen.blit(hs, hit_rect.topleft)
        draw_power_icon(screen, rect, (235, 110, 110) if hovered else WHITE)
        self._taskbar_power_rect = hit_rect

    def _draw_taskbar_pinned(self, start_x):
        """Windows-style pinned quick-launch icons: the 4 core apps, always
        one tap away from any screen. Browser is intentionally excluded."""
        mouse_pos = to_virtual(pygame.mouse.get_pos())
        icon_size = 30
        gap = 10
        y = BASE_HEIGHT - TASKBAR_H + (TASKBAR_H - icon_size) // 2
        self._taskbar_pinned_rects = []
        x = start_x
        for label, draw_fn, target_state in self.TASKBAR_PINNED_APPS:
            btn_rect = pygame.Rect(x, y, icon_size, icon_size)
            hit_rect = btn_rect.inflate(8, 8)
            hovered = hit_rect.collidepoint(mouse_pos)
            active = (self.state == target_state)
            if hovered or active:
                hs = pygame.Surface((hit_rect.w, hit_rect.h), pygame.SRCALPHA)
                pygame.draw.rect(hs, (255, 255, 255, 65 if active else 40), hs.get_rect(), border_radius=8)
                screen.blit(hs, hit_rect.topleft)
            draw_fn(screen, (btn_rect.x + 3, btn_rect.y + 3, icon_size - 6, icon_size - 6))
            self._taskbar_pinned_rects.append((hit_rect, target_state))
            x += icon_size + gap

    def handle_taskbar_click(self, pos):
        if self._taskbar_power_rect and self._taskbar_power_rect.collidepoint(pos):
            self.power_menu_open = not self.power_menu_open
            return True
        for rect, target_state in self._taskbar_pinned_rects:
            if rect.collidepoint(pos):
                self.open_app(target_state)
                return True
        return False

    # ---------------- power system: menu, lock screen, shutdown, restart ----------------
    def draw_power_menu(self):
        overlay = pygame.Rect(0, 0, BASE_WIDTH, BASE_HEIGHT - TASKBAR_H)
        s = pygame.Surface((overlay.w, overlay.h), pygame.SRCALPHA)
        pygame.draw.rect(s, (10, 10, 14, 130), s.get_rect())
        screen.blit(s, overlay.topleft)

        box_w, box_h = 250, 216
        box = pygame.Rect(0, 0, box_w, box_h)
        box.right = BASE_WIDTH - 16
        box.bottom = BASE_HEIGHT - TASKBAR_H - 12
        pygame.draw.rect(screen, (250, 250, 252), box, border_radius=14)
        pygame.draw.rect(screen, (210, 210, 215), box, 1, border_radius=14)

        mouse_pos = to_virtual(pygame.mouse.get_pos())
        options = [
            ("lock", "🔒  Lock Screen", (95, 155, 220), WHITE),
            ("restart", "⟳  Restart", (150, 150, 158), WHITE),
            ("shutdown", "⏻  Shut Down", (205, 90, 90), WHITE),
            ("cancel", "Cancel", LIGHT_GRAY, BLACK),
        ]
        self._power_menu_rects = []
        y = box.y + 14
        for key, label, base_color, fg in options:
            rect = pygame.Rect(box.x + 14, y, box.w - 28, 40)
            hovered = rect.collidepoint(mouse_pos)
            pygame.draw.rect(screen, shade(base_color, -15) if hovered else base_color, rect, border_radius=10)
            lbl = FONT_UI.render(label, True, fg)
            screen.blit(lbl, lbl.get_rect(center=rect.center))
            self._power_menu_rects.append((rect, key))
            y += 40 + 8
        self._power_menu_box = box

    def handle_power_menu_click(self, pos):
        for rect, key in self._power_menu_rects:
            if rect.collidepoint(pos):
                self.power_menu_open = False
                if key == "lock":
                    self._set_text_input_active(False)
                    self.lock_screen_active = True
                    self.lock_screen_purpose = "manual"
                    self.pin_entry_input = ""
                    self.pin_entry_error = ""
                elif key == "restart":
                    self.trigger_restart()
                elif key == "shutdown":
                    self.shutting_down = True
                    self.shutdown_start = pygame.time.get_ticks()
                return
        if self._power_menu_box and not self._power_menu_box.collidepoint(pos):
            self.power_menu_open = False  # tapped outside the menu — just dismiss it

    # ---------------- PIN pad (shared by lock screen + Settings → Security) ----------------
    def _draw_pin_pad(self, target_surf, pad_rect, mouse_pos):
        keys = [["1", "2", "3"], ["4", "5", "6"], ["7", "8", "9"], ["", "0", "⌫"]]
        rows, cols = 4, 3
        gap = 10
        btn_w = (pad_rect.w - gap * (cols - 1)) / cols
        btn_h = (pad_rect.h - gap * (rows - 1)) / rows
        rects = {}
        for r, row in enumerate(keys):
            for c, label in enumerate(row):
                if not label:
                    continue
                bx = pad_rect.x + c * (btn_w + gap)
                by = pad_rect.y + r * (btn_h + gap)
                rect = pygame.Rect(bx, by, btn_w, btn_h)
                hovered = rect.collidepoint(mouse_pos)
                base = (232, 205, 205) if label == "⌫" else (240, 240, 243)
                color = shade(base, -18) if hovered else base
                pygame.draw.rect(target_surf, color, rect, border_radius=12)
                lbl = FONT_TITLE.render(label, True, BLACK)
                target_surf.blit(lbl, lbl.get_rect(center=rect.center))
                rects[label] = rect
        return rects

    def _draw_pin_dots(self, target_surf, color_filled, color_empty, center_x, y, filled_count, total=4):
        spacing = 26
        start_x = center_x - (total - 1) * spacing / 2
        for i in range(total):
            cx, cy = int(start_x + i * spacing), int(y)
            if i < filled_count:
                pygame.draw.circle(target_surf, color_filled, (cx, cy), 7)
            else:
                pygame.draw.circle(target_surf, color_empty, (cx, cy), 7, 2)

    def draw_lock_screen(self):
        screen.blit(self.wallpaper_surface, (0, 0))
        dim = pygame.Surface((BASE_WIDTH, BASE_HEIGHT))
        dim.fill((0, 0, 0))
        dim.set_alpha(150 if self.pin_hash else 130)
        screen.blit(dim, (0, 0))

        if not self.pin_hash:
            self._pin_pad_rects = {}
            clock_render = FONT_WELCOME.render(self.format_clock(), True, WHITE)
            screen.blit(clock_render, clock_render.get_rect(center=(BASE_WIDTH // 2, BASE_HEIGHT // 2 - 30)))
            date_render = FONT_UI.render(time.strftime("%A, %B %d"), True, (225, 225, 228))
            screen.blit(date_render, date_render.get_rect(center=(BASE_WIDTH // 2, BASE_HEIGHT // 2 + 26)))
            hint = FONT_SMALL.render("Tap anywhere to unlock", True, (200, 200, 205))
            screen.blit(hint, hint.get_rect(center=(BASE_WIDTH // 2, BASE_HEIGHT - 70)))
            return

        title_text = "Enter PIN to Start StrapOS" if self.lock_screen_purpose == "boot" else "StrapOS is Locked"
        title = FONT_UI.render(title_text, True, WHITE)
        screen.blit(title, title.get_rect(center=(BASE_WIDTH // 2, int(BASE_HEIGHT * 0.22))))

        dots_y = int(BASE_HEIGHT * 0.22) + 46
        self._draw_pin_dots(screen, WHITE, (140, 140, 148), BASE_WIDTH // 2, dots_y, len(self.pin_entry_input))

        if self.pin_entry_error:
            err = FONT_SMALL.render(self.pin_entry_error, True, (255, 150, 150))
            screen.blit(err, err.get_rect(center=(BASE_WIDTH // 2, dots_y + 28)))

        pad_w, pad_h = 260, 300
        pad_rect = pygame.Rect(0, 0, pad_w, pad_h)
        pad_rect.center = (BASE_WIDTH // 2, int(BASE_HEIGHT * 0.62))
        mouse_pos = to_virtual(pygame.mouse.get_pos())
        self._pin_pad_rects = self._draw_pin_pad(screen, pad_rect, mouse_pos)

    def handle_pin_pad_click(self, pos):
        for label, rect in self._pin_pad_rects.items():
            if rect.collidepoint(pos):
                self._pin_pad_press(label)
                return

    def handle_pin_pad_key(self, event):
        if event.key == pygame.K_BACKSPACE:
            self._pin_pad_press("⌫")
        elif getattr(event, "unicode", "") and event.unicode.isdigit():
            self._pin_pad_press(event.unicode)

    def _pin_pad_press(self, label):
        if label == "⌫":
            self.pin_entry_input = self.pin_entry_input[:-1]
            self.pin_entry_error = ""
            return
        if label.isdigit() and len(self.pin_entry_input) < 4:
            self.pin_entry_input += label
            self.pin_entry_error = ""
            if len(self.pin_entry_input) == 4:
                self._check_pin_entry()

    def _check_pin_entry(self):
        if _verify_pin(self.pin_entry_input, self.pin_hash, self.pin_salt):
            self._unlock_lock_screen()
        else:
            self.pin_entry_error = "Incorrect PIN — try again."
            self.pin_entry_input = ""

    def _unlock_lock_screen(self):
        purpose = self.lock_screen_purpose
        self.lock_screen_active = False
        self.lock_screen_purpose = None
        self.pin_entry_input = ""
        self.pin_entry_error = ""
        if purpose == "boot":
            self.booting = True
            self.boot_start = pygame.time.get_ticks()

    def draw_shutdown_overlay(self):
        """Fades the screen to black with 'Shutting Down' text — the mirror
        image of the boot screen. Returns True once the fade is complete."""
        screen.blit(self.wallpaper_surface, (0, 0))
        elapsed = pygame.time.get_ticks() - self.shutdown_start
        duration = 1300
        progress = min(1.0, elapsed / duration)

        overlay = pygame.Surface((BASE_WIDTH, BASE_HEIGHT))
        overlay.fill((0, 0, 0))
        overlay.set_alpha(int(255 * progress))
        screen.blit(overlay, (0, 0))

        if progress > 0.3:
            text_alpha = int(255 * min(1.0, (progress - 0.3) / 0.7))
            text_render = FONT_WELCOME.render("Shutting Down", True, WHITE)
            text_render.set_alpha(text_alpha)
            screen.blit(text_render, text_render.get_rect(center=(BASE_WIDTH // 2, BASE_HEIGHT // 2)))

        return progress >= 1.0

    def trigger_restart(self):
        """A soft restart: closes everything running and plays the boot screen
        again, but — unlike Software Reset — keeps all settings, wallpapers,
        and imported apps exactly as they were."""
        self._set_text_input_active(False)

        if self.term_running_proc is not None:
            try:
                self.term_running_proc.terminate()
            except Exception:
                pass
            self.term_running_proc = None
            self.term_running_name = None
        for entry in self.launch_watch:
            try:
                entry["proc"].terminate()
            except Exception:
                pass
        self.launch_watch = []

        self.state = self.STATE_DESKTOP
        self.current_path = STORAGE_ROOT
        self._refresh_entries()
        self.calc_expression = ""
        self.calc_error = False
        self.term_input = ""
        self.term_history = [
            "StrapOS Terminal",
            'Type "test" to make sure everything is working fine.',
            "",
        ]
        self.loader_path = STORAGE_ROOT
        self._loader_refresh_entries()
        self.loader_log_view = None
        self.wallpaper_picker_open = False
        self.reset_confirm_open = False
        self.fm_upload_open = False
        self.power_menu_open = False
        self.pin_setup_open = False
        self.settings_scroll = 0

        # Just like a real device, a restart brings you back to the lock
        # screen if a PIN is set — otherwise it goes straight to boot.
        if self.pin_hash:
            self.lock_screen_active = True
            self.lock_screen_purpose = "boot"
            self.pin_entry_input = ""
            self.pin_entry_error = ""
            self.booting = False
        else:
            self.lock_screen_active = False
            self.lock_screen_purpose = None
            self.booting = True
            self.boot_start = pygame.time.get_ticks()

    def _draw_toast(self):
        if not self.toast_message or pygame.time.get_ticks() >= self.toast_until:
            return
        msg_render = FONT_SMALL.render(self.toast_message, True, WHITE)
        pad_x, pad_y = 16, 8
        box_w = min(BASE_WIDTH - 40, msg_render.get_width() + pad_x * 2)
        box_h = msg_render.get_height() + pad_y * 2
        box = pygame.Rect(0, 0, box_w, box_h)
        box.centerx = BASE_WIDTH // 2
        box.bottom = BASE_HEIGHT - TASKBAR_H - 14
        color = (40, 150, 105) if self.toast_ok else (195, 75, 75)
        s = pygame.Surface((box_w, box_h), pygame.SRCALPHA)
        pygame.draw.rect(s, (*color, 235), s.get_rect(), border_radius=10)
        screen.blit(s, box.topleft)
        screen.blit(msg_render, (box.x + pad_x, box.y + pad_y))

    def draw_boot_overlay(self):
        """Sky blue 'Welcome' splash, drawn on top of the desktop each frame
        until it fully fades out. Tapping anywhere skips it early."""
        elapsed = pygame.time.get_ticks() - self.boot_start
        hold = self.boot_hold_ms
        total = hold + BOOT_FADE_MS
        if elapsed >= total:
            self.booting = False
            return

        if elapsed < hold:
            alpha = 255
        else:
            progress = (elapsed - hold) / BOOT_FADE_MS
            alpha = max(0, min(255, int(255 * (1 - progress))))

        bg = pygame.Surface((BASE_WIDTH, BASE_HEIGHT))
        bg.fill(SKY_BLUE)
        bg.set_alpha(alpha)
        screen.blit(bg, (0, 0))

        title_render = FONT_WELCOME.render("Welcome", True, WHITE)
        title_render.set_alpha(alpha)
        screen.blit(title_render, title_render.get_rect(center=(BASE_WIDTH // 2, BASE_HEIGHT // 2 - 12)))

        sub_render = FONT_UI.render("StrapOS is starting…", True, WHITE)
        sub_render.set_alpha(alpha)
        screen.blit(sub_render, sub_render.get_rect(center=(BASE_WIDTH // 2, BASE_HEIGHT // 2 + 48)))

    def format_clock(self):
        if self.clock_format == "12":
            text = time.strftime("%I:%M:%S %p")
            if text.startswith("0"):
                text = text[1:]
            return text
        return time.strftime("%H:%M:%S")

    # ---------------- drawing: window chrome ----------------
    def draw_window(self, title):
        margin = 28
        win_rect = pygame.Rect(margin, margin, BASE_WIDTH - margin * 2, BASE_HEIGHT - margin * 2 - TASKBAR_H)
        shadow = pygame.Rect(win_rect.x + 6, win_rect.y + 6, win_rect.w, win_rect.h)
        s = pygame.Surface((shadow.w, shadow.h), pygame.SRCALPHA)
        pygame.draw.rect(s, (0, 0, 0, 60), s.get_rect(), border_radius=14)
        screen.blit(s, shadow.topleft)

        pygame.draw.rect(screen, (250, 250, 252), win_rect, border_radius=14)
        pygame.draw.rect(screen, (210, 210, 215), win_rect, 1, border_radius=14)

        title_bar = pygame.Rect(win_rect.x, win_rect.y, win_rect.w, 46)
        pygame.draw.rect(screen, (235, 235, 240), title_bar, border_top_left_radius=14, border_top_right_radius=14)
        pygame.draw.line(screen, (215, 215, 220), (win_rect.x, win_rect.y + 46), (win_rect.right, win_rect.y + 46), 1)

        title_render = FONT_UI.render(title, True, BLACK)
        screen.blit(title_render, (win_rect.x + 18, win_rect.y + 12))

        close_rect = pygame.Rect(win_rect.right - 40, win_rect.y + 10, 28, 28)
        hovered = close_rect.collidepoint(to_virtual(pygame.mouse.get_pos()))
        pygame.draw.circle(screen, (235, 90, 90) if hovered else (220, 100, 100), close_rect.center, 14)
        x_font = FONT_UI.render("x", True, WHITE)
        screen.blit(x_font, x_font.get_rect(center=close_rect.center))

        return win_rect, close_rect

    # ---------------- drawing: file manager ----------------
    def draw_file_manager(self, mouse_pos):
        screen.blit(self.wallpaper_surface, (0, 0))
        win_rect, close_rect = self.draw_window("File Manager")
        self._fm_close_rect = close_rect

        content = pygame.Rect(win_rect.x + 16, win_rect.y + 58, win_rect.w - 32, win_rect.h - 74)

        # toolbar
        up_btn = pygame.Rect(content.x, content.y, 70, 30)
        home_btn = pygame.Rect(content.x + 78, content.y, 80, 30)
        upload_btn = pygame.Rect(content.x + 166, content.y, 96, 30)
        pygame.draw.rect(screen, LIGHT_GRAY, up_btn, border_radius=6)
        pygame.draw.rect(screen, LIGHT_GRAY, home_btn, border_radius=6)
        upload_hovered = upload_btn.collidepoint(mouse_pos)
        pygame.draw.rect(screen, shade((90, 150, 220), -15) if upload_hovered else (90, 150, 220),
                          upload_btn, border_radius=6)
        screen.blit(FONT_SMALL.render("⬆ Up", True, BLACK), FONT_SMALL.render("⬆ Up", True, BLACK).get_rect(center=up_btn.center))
        screen.blit(FONT_SMALL.render("🏠 Home", True, BLACK), FONT_SMALL.render("🏠 Home", True, BLACK).get_rect(center=home_btn.center))
        upload_lbl = FONT_SMALL.render("⬆ Upload", True, WHITE)
        screen.blit(upload_lbl, upload_lbl.get_rect(center=upload_btn.center))
        self._fm_up_btn, self._fm_home_btn, self._fm_upload_btn = up_btn, home_btn, upload_btn

        path_rect = pygame.Rect(content.x + 270, content.y, content.w - 270, 30)
        pygame.draw.rect(screen, WHITE, path_rect, border_radius=6)
        pygame.draw.rect(screen, (210, 210, 210), path_rect, 1, border_radius=6)
        path_text = self.current_path
        max_chars = 60
        if len(path_text) > max_chars:
            path_text = "..." + path_text[-max_chars:]
        screen.blit(FONT_SMALL.render(path_text, True, GRAY), (path_rect.x + 10, path_rect.y + 7))

        list_area = pygame.Rect(content.x, content.y + 42, content.w * 0.55, content.h - 42)
        preview_area = pygame.Rect(list_area.right + 14, content.y + 42, content.w - list_area.w - 14, content.h - 42)

        pygame.draw.rect(screen, WHITE, list_area, border_radius=8)
        pygame.draw.rect(screen, (220, 220, 220), list_area, 1, border_radius=8)
        pygame.draw.rect(screen, (250, 250, 252), preview_area, border_radius=8)
        pygame.draw.rect(screen, (220, 220, 220), preview_area, 1, border_radius=8)

        row_h = 30
        self._fm_row_rects = []
        clip = screen.get_clip()
        screen.set_clip(list_area)
        if not self.entries and self.status_message:
            screen.blit(FONT_SMALL.render(self.status_message, True, (180, 60, 60)), (list_area.x + 12, list_area.y + 12))
        for i, (kind, name) in enumerate(self.entries):
            ry = list_area.y + i * row_h - self.scroll
            if ry + row_h < list_area.y or ry > list_area.bottom:
                self._fm_row_rects.append(None)
                continue
            row_rect = pygame.Rect(list_area.x + 4, ry, list_area.w - 8, row_h - 4)
            self._fm_row_rects.append(row_rect)
            hovered = row_rect.collidepoint(mouse_pos)
            if hovered:
                pygame.draw.rect(screen, (225, 240, 235), row_rect, border_radius=6)
            icon_rect = (row_rect.x + 4, row_rect.y + 3, 18, 18)
            if kind == "dir":
                draw_folder_icon(screen, icon_rect)
            else:
                draw_file_icon(screen, icon_rect)
            label = FONT_SMALL.render(name, True, BLACK)
            screen.blit(label, (row_rect.x + 30, row_rect.y + 5))
        screen.set_clip(clip)

        # preview panel
        if self.selected_file_name:
            header = FONT_UI.render(self.selected_file_name, True, BLACK)
            screen.blit(header, (preview_area.x + 12, preview_area.y + 10))
            body_area = pygame.Rect(preview_area.x + 12, preview_area.y + 42, preview_area.w - 24, preview_area.h - 54)
            clip2 = screen.get_clip()
            screen.set_clip(body_area)
            if self.selected_file_content:
                y = body_area.y
                for line in self.selected_file_content.split("\n"):
                    line_render = FONT_MONO.render(line[:90], True, (50, 50, 50))
                    screen.blit(line_render, (body_area.x, y))
                    y += 18
                    if y > body_area.bottom:
                        break
            screen.set_clip(clip2)
        else:
            hint = FONT_SMALL.render("Click a file to preview it here.", True, GRAY)
            screen.blit(hint, (preview_area.x + 12, preview_area.y + 12))

        self._fm_list_area = list_area
        self.draw_taskbar(f"File Manager — {os.path.basename(self.current_path) or self.current_path}")

        if self.fm_upload_open:
            self.draw_fm_upload_overlay(win_rect, mouse_pos)

    def handle_file_manager_click(self, pos):
        if self.fm_upload_open:
            self.handle_fm_upload_click(pos)
            return
        if self._fm_close_rect.collidepoint(pos):
            self.go_home()
            return
        if self._fm_up_btn.collidepoint(pos):
            self.go_up()
            return
        if self._fm_home_btn.collidepoint(pos):
            self.current_path = STORAGE_ROOT
            self._refresh_entries()
            return
        if self._fm_upload_btn.collidepoint(pos):
            self.open_fm_upload()
            return
        for row_rect, (kind, name) in zip(self._fm_row_rects, self.entries):
            if row_rect and row_rect.collidepoint(pos):
                if kind == "dir":
                    self.go_into(name)
                else:
                    self.open_file_preview(name)
                return

    def handle_file_manager_scroll(self, direction):
        if self.fm_upload_open:
            max_scroll = max(0, len(self.fm_upload_entries) * 30 - self._fm_upload_list_area.h)
            self.fm_upload_scroll = max(0, min(max_scroll, self.fm_upload_scroll - direction * 30))
            return
        max_scroll = max(0, len(self.entries) * 30 - self._fm_list_area.h)
        self.scroll = max(0, min(max_scroll, self.scroll - direction * 30))

    # ---------------- file manager: upload picker ----------------
    def open_fm_upload(self):
        self.fm_upload_open = True
        self.fm_upload_path = STORAGE_ROOT
        self._fm_upload_refresh_entries()

    def close_fm_upload(self):
        self.fm_upload_open = False

    def _fm_upload_refresh_entries(self):
        self.fm_upload_scroll = 0
        try:
            names = os.listdir(self.fm_upload_path)
        except Exception:
            self.fm_upload_entries = []
            return
        dirs, files = [], []
        for n in names:
            full = os.path.join(self.fm_upload_path, n)
            try:
                if os.path.isdir(full):
                    dirs.append(n)
                elif os.path.isfile(full):
                    files.append(n)
            except Exception:
                continue
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)
        self.fm_upload_entries = [("dir", d) for d in dirs] + [("file", f) for f in files]

    def fm_upload_go_up(self):
        parent = os.path.dirname(self.fm_upload_path.rstrip(os.sep))
        if parent and os.path.isdir(parent):
            self.fm_upload_path = parent
            self._fm_upload_refresh_entries()

    def fm_upload_go_into(self, name):
        full = os.path.join(self.fm_upload_path, name)
        if os.path.isdir(full):
            self.fm_upload_path = full
            self._fm_upload_refresh_entries()

    def upload_file_to_current_folder(self, name):
        src = os.path.abspath(os.path.join(self.fm_upload_path, name))
        if not os.path.isfile(src):
            self.show_toast(f"'{name}' not found.", ok=False)
            return
        if os.path.abspath(os.path.dirname(src)) == os.path.abspath(self.current_path):
            self.show_toast(f"'{name}' is already in this folder.", ok=False)
            return
        try:
            stem, ext = os.path.splitext(os.path.basename(src))
            dest = os.path.join(self.current_path, os.path.basename(src))
            counter = 1
            while os.path.exists(dest):
                dest = os.path.join(self.current_path, f"{stem}_{counter}{ext}")
                counter += 1
            shutil.copy2(src, dest)
            self.close_fm_upload()
            self._refresh_entries()
            self.show_toast(f"Uploaded '{os.path.basename(dest)}'.", ok=True)
        except Exception as e:
            self.show_toast(f"Upload failed: {e}", ok=False)

    def draw_fm_upload_overlay(self, win_rect, mouse_pos):
        overlay = pygame.Rect(win_rect.x + 16, win_rect.y + 56, win_rect.w - 32, win_rect.h - 76)
        pygame.draw.rect(screen, (246, 247, 249), overlay, border_radius=10)
        pygame.draw.rect(screen, (200, 200, 205), overlay, 2, border_radius=10)

        title = FONT_UI.render("Upload a File", True, BLACK)
        screen.blit(title, (overlay.x + 16, overlay.y + 10))
        subtitle = FONT_SMALL.render(f"Copies into: {os.path.basename(self.current_path) or self.current_path}",
                                      True, GRAY)
        screen.blit(subtitle, (overlay.x + 16, overlay.y + 32))

        close_rect = pygame.Rect(overlay.right - 40, overlay.y + 10, 28, 28)
        hovered = close_rect.collidepoint(mouse_pos)
        pygame.draw.circle(screen, (210, 95, 95) if hovered else (225, 110, 110), close_rect.center, 14)
        xlbl = FONT_UI.render("x", True, WHITE)
        screen.blit(xlbl, xlbl.get_rect(center=close_rect.center))
        self._fm_upload_close_rect = close_rect

        toolbar_y = overlay.y + 56
        up_btn = pygame.Rect(overlay.x + 16, toolbar_y, 70, 28)
        home_btn = pygame.Rect(overlay.x + 94, toolbar_y, 80, 28)
        pygame.draw.rect(screen, LIGHT_GRAY, up_btn, border_radius=6)
        pygame.draw.rect(screen, LIGHT_GRAY, home_btn, border_radius=6)
        up_lbl = FONT_SMALL.render("⬆ Up", True, BLACK)
        home_lbl = FONT_SMALL.render("🏠 Home", True, BLACK)
        screen.blit(up_lbl, up_lbl.get_rect(center=up_btn.center))
        screen.blit(home_lbl, home_lbl.get_rect(center=home_btn.center))
        self._fm_upload_up_btn, self._fm_upload_home_btn = up_btn, home_btn

        path_rect = pygame.Rect(overlay.x + 182, toolbar_y, overlay.w - 198, 28)
        pygame.draw.rect(screen, WHITE, path_rect, border_radius=6)
        pygame.draw.rect(screen, (210, 210, 210), path_rect, 1, border_radius=6)
        path_text = self.fm_upload_path
        if len(path_text) > 60:
            path_text = "..." + path_text[-60:]
        screen.blit(FONT_SMALL.render(path_text, True, GRAY), (path_rect.x + 8, path_rect.y + 6))

        list_area = pygame.Rect(overlay.x + 16, toolbar_y + 38, overlay.w - 32,
                                 overlay.bottom - (toolbar_y + 38) - 16)
        pygame.draw.rect(screen, WHITE, list_area, border_radius=8)
        pygame.draw.rect(screen, (220, 220, 220), list_area, 1, border_radius=8)
        self._fm_upload_list_area = list_area

        row_h = 30
        self._fm_upload_row_rects = []
        clip = screen.get_clip()
        screen.set_clip(list_area)
        if not self.fm_upload_entries:
            screen.blit(FONT_SMALL.render("No folders or files here.", True, GRAY),
                        (list_area.x + 12, list_area.y + 12))
        for i, (kind, name) in enumerate(self.fm_upload_entries):
            ry = list_area.y + i * row_h - self.fm_upload_scroll
            if ry + row_h < list_area.y or ry > list_area.bottom:
                self._fm_upload_row_rects.append(None)
                continue
            row_rect = pygame.Rect(list_area.x + 4, ry, list_area.w - 8, row_h - 4)
            self._fm_upload_row_rects.append(row_rect)
            hovered_row = row_rect.collidepoint(mouse_pos)
            if hovered_row:
                pygame.draw.rect(screen, (222, 232, 250) if kind == "file" else (225, 240, 235),
                                  row_rect, border_radius=6)
            icon_rect = (row_rect.x + 4, row_rect.y + 3, 18, 18)
            if kind == "dir":
                draw_folder_icon(screen, icon_rect)
            else:
                draw_file_icon(screen, icon_rect)
            screen.blit(FONT_SMALL.render(name, True, BLACK), (row_rect.x + 30, row_rect.y + 5))
        screen.set_clip(clip)

    def handle_fm_upload_click(self, pos):
        if self._fm_upload_close_rect.collidepoint(pos):
            self.close_fm_upload()
            return
        if self._fm_upload_up_btn.collidepoint(pos):
            self.fm_upload_go_up()
            return
        if self._fm_upload_home_btn.collidepoint(pos):
            self.fm_upload_path = STORAGE_ROOT
            self._fm_upload_refresh_entries()
            return
        for row_rect, (kind, name) in zip(self._fm_upload_row_rects, self.fm_upload_entries):
            if row_rect and row_rect.collidepoint(pos):
                if kind == "dir":
                    self.fm_upload_go_into(name)
                else:
                    self.upload_file_to_current_folder(name)
                return

    # ---------------- drawing: settings ----------------
    @staticmethod
    def _load_and_cover_fit(path, target_w, target_h):
        img = pygame.image.load(path)
        try:
            img = img.convert()
        except Exception:
            pass
        iw, ih = img.get_size()
        if iw <= 0 or ih <= 0:
            raise ValueError("invalid image dimensions")
        fit_scale = max(target_w / iw, target_h / ih)
        new_w, new_h = max(1, int(iw * fit_scale) + 1), max(1, int(ih * fit_scale) + 1)
        scaled = pygame.transform.smoothscale(img, (new_w, new_h))
        surf = pygame.Surface((target_w, target_h))
        off_x = (new_w - target_w) // 2
        off_y = (new_h - target_h) // 2
        surf.blit(scaled, (-off_x, -off_y))
        return surf

    def _get_wallpaper_thumbnail(self, path, size):
        key = (path, size)
        if key in self._wallpaper_thumb_cache:
            return self._wallpaper_thumb_cache[key]
        try:
            thumb = self._load_and_cover_fit(path, size, size)
        except Exception:
            thumb = None
        self._wallpaper_thumb_cache[key] = thumb
        return thumb

    def _settings_to_canvas_pos(self, pos):
        rect = getattr(self, "_st_content_rect", None)
        if not rect:
            return pos
        return (pos[0] - rect.x, pos[1] - rect.y + self.settings_scroll)

    def draw_settings(self, mouse_pos):
        screen.blit(self.wallpaper_surface, (0, 0))
        win_rect, close_rect = self.draw_window("Settings")
        self._st_close_rect = close_rect

        content = pygame.Rect(win_rect.x + 24, win_rect.y + 58, win_rect.w - 48, win_rect.h - 78)
        self._st_content_rect = content
        canvas_mouse = self._settings_to_canvas_pos(mouse_pos)

        # --- lay out the wallpaper grid (builtins + custom uploads + an "Upload" tile) ---
        grid_items = [{"kind": "builtin", "index": i, "wp": wp} for i, wp in enumerate(WALLPAPERS)]
        grid_items += [{"kind": "custom", "entry": w} for w in self.custom_wallpapers]
        grid_items.append({"kind": "upload"})

        swatch_size = 120
        gap = 20
        cols = 6
        rows_used = (len(grid_items) + cols - 1) // cols
        grid_start_y = 56
        section_bottom = grid_start_y + rows_used * (swatch_size + 38)

        clock_y = section_bottom + 20
        by = clock_y + 56
        btn_h = 46
        launch_y = by + btn_h + 22
        by2 = launch_y + 50
        btn_h2 = 38
        boot_y = by2 + btn_h2 + 22
        slider_y = boot_y + 50
        slider_h = 26
        security_y = slider_y + slider_h + 34
        sec_btn_y = security_y + 50
        btn_h3 = 40
        reset_y = sec_btn_y + btn_h3 + 34
        reset_btn_h = 42
        total_content_h = reset_y + 50 + reset_btn_h + 30
        self._st_total_content_h = total_content_h

        max_scroll = max(0, total_content_h - content.h)
        self.settings_scroll = max(0, min(max_scroll, self.settings_scroll))

        canvas = pygame.Surface((content.w, total_content_h))
        canvas.fill((250, 250, 252))

        # --- Wallpaper section ---
        canvas.blit(FONT_UI.render("Wallpaper", True, BLACK), (0, 0))
        canvas.blit(FONT_SMALL.render(
            "Pick a built-in color, or upload your own image from your device.", True, GRAY), (0, 24))

        self._st_wallpaper_item_rects = []
        self._st_wallpaper_remove_rects = []
        for i, item in enumerate(grid_items):
            col = i % cols
            row = i // cols
            x = col * (swatch_size + gap)
            y = grid_start_y + row * (swatch_size + 38)
            rect = pygame.Rect(x, y, swatch_size, swatch_size)
            self._st_wallpaper_item_rects.append((rect, item))
            hovered = rect.collidepoint(canvas_mouse)

            if item["kind"] == "builtin":
                preview = pygame.Surface((swatch_size, swatch_size))
                draw_arch_wallpaper(preview, item["wp"]["base"], item["wp"]["accent"])
                canvas.blit(preview, rect.topleft)
                selected = (self.wallpaper_kind == "builtin" and self.wallpaper_index == item["index"])
                label_text = item["wp"]["name"]
            elif item["kind"] == "custom":
                entry = item["entry"]
                thumb = self._get_wallpaper_thumbnail(entry["path"], swatch_size)
                if thumb is not None:
                    canvas.blit(thumb, rect.topleft)
                else:
                    pygame.draw.rect(canvas, (60, 60, 65), rect, border_radius=10)
                    err = FONT_SMALL.render("missing", True, WHITE)
                    canvas.blit(err, err.get_rect(center=rect.center))
                selected = (self.wallpaper_kind == "custom" and self.wallpaper_custom_path == entry["path"])
                label_text = entry["name"]

                remove_rect = pygame.Rect(rect.right - 20, rect.y + 4, 18, 18)
                self._st_wallpaper_remove_rects.append((remove_rect, entry))
                remove_hovered = remove_rect.collidepoint(canvas_mouse)
                s = pygame.Surface((18, 18), pygame.SRCALPHA)
                pygame.draw.circle(s, (215, 90, 90, 235 if remove_hovered else 200), (9, 9), 9)
                canvas.blit(s, remove_rect.topleft)
                xr = FONT_SMALL.render("x", True, WHITE)
                canvas.blit(xr, xr.get_rect(center=remove_rect.center))
            else:  # upload tile
                pygame.draw.rect(canvas, (244, 245, 248), rect, border_radius=10)
                pygame.draw.rect(canvas, (200, 200, 206), rect, 2, border_radius=10)
                pad = 22
                draw_upload_icon(canvas, (rect.x + pad, rect.y + pad, rect.w - pad * 2, rect.h - pad * 2))
                selected = False
                label_text = "Upload"

            if item["kind"] != "upload":
                border_color = (40, 180, 120) if selected else (210, 210, 210)
                pygame.draw.rect(canvas, border_color, rect, 4 if selected else 2, border_radius=10)
                if hovered and not selected:
                    s = pygame.Surface((swatch_size, swatch_size), pygame.SRCALPHA)
                    pygame.draw.rect(s, (255, 255, 255, 40), s.get_rect(), border_radius=10)
                    canvas.blit(s, rect.topleft)
            elif hovered:
                s = pygame.Surface((swatch_size, swatch_size), pygame.SRCALPHA)
                pygame.draw.rect(s, (255, 255, 255, 60), s.get_rect(), border_radius=10)
                canvas.blit(s, rect.topleft)

            label = FONT_SMALL.render(label_text + (" ✓" if selected else ""), True, BLACK)
            canvas.blit(label, (rect.x, rect.bottom + 4))

        # --- Clock format section ---
        canvas.blit(FONT_UI.render("Clock Format", True, BLACK), (0, clock_y))
        canvas.blit(FONT_SMALL.render("Pick how the taskbar clock displays the time.", True, GRAY),
                    (0, clock_y + 24))

        btn_w = 150
        gap_btn = 14
        opts = [("12", "12-hour  (2:45:09 PM)"), ("24", "24-hour  (14:45:09)")]
        self._st_clock_rects = []
        for i, (val, label_text) in enumerate(opts):
            bx = i * (btn_w + gap_btn + 90)
            rect = pygame.Rect(bx, by, btn_w + 90, btn_h)
            self._st_clock_rects.append((rect, val))
            selected = (self.clock_format == val)
            hovered = rect.collidepoint(canvas_mouse)
            base_color = (90, 180, 145) if selected else (LIGHT_GRAY if not hovered else shade(LIGHT_GRAY, -10))
            pygame.draw.rect(canvas, base_color, rect, border_radius=10)
            pygame.draw.rect(canvas, shade(base_color, -40), rect, 1, border_radius=10)
            fg = WHITE if selected else BLACK
            lbl = FONT_SMALL.render(("● " if selected else "○ ") + label_text, True, fg)
            canvas.blit(lbl, lbl.get_rect(center=rect.center))

        # --- App Launch Mode section ---
        canvas.blit(FONT_UI.render("App Launch Mode", True, BLACK), (0, launch_y))
        auto_target = "same window (Android)" if IS_ANDROID else "separate process (Desktop)"
        canvas.blit(FONT_SMALL.render(
            f"How imported apps open. Auto currently picks: {auto_target}.", True, GRAY), (0, launch_y + 24))

        mode_opts = [
            ("auto", "Auto"),
            ("inprocess", "Same window (Android)"),
            ("subprocess", "Separate process (Desktop)"),
        ]
        gap_lm = 12
        col_w = (content.w - gap_lm * (len(mode_opts) - 1)) / len(mode_opts)
        self._st_launchmode_rects = []
        for i, (val, label_text) in enumerate(mode_opts):
            bx = i * (col_w + gap_lm)
            rect = pygame.Rect(bx, by2, col_w, btn_h2)
            self._st_launchmode_rects.append((rect, val))
            selected = (self.launch_mode == val)
            hovered = rect.collidepoint(canvas_mouse)
            base_color = (90, 180, 145) if selected else (LIGHT_GRAY if not hovered else shade(LIGHT_GRAY, -10))
            pygame.draw.rect(canvas, base_color, rect, border_radius=8)
            pygame.draw.rect(canvas, shade(base_color, -40), rect, 1, border_radius=8)
            fg = WHITE if selected else BLACK
            lbl = FONT_SMALL.render(("● " if selected else "○ ") + label_text, True, fg)
            canvas.blit(lbl, lbl.get_rect(center=rect.center))

        # --- Boot Screen section (slider) ---
        canvas.blit(FONT_UI.render("Boot Screen", True, BLACK), (0, boot_y))
        seconds = self.boot_hold_ms / 1000.0
        canvas.blit(FONT_SMALL.render(
            f"How long \"Welcome\" holds before it fades — {seconds:.1f}s", True, GRAY), (0, boot_y + 24))

        slider_rect = pygame.Rect(0, slider_y, content.w, slider_h)
        self._st_boot_slider_rect = slider_rect

        track = pygame.Rect(slider_rect.x, slider_rect.centery - 3, slider_rect.w, 6)
        pygame.draw.rect(canvas, LIGHT_GRAY, track, border_radius=3)

        ratio = (self.boot_hold_ms - self.BOOT_SLIDER_MIN_MS) / (self.BOOT_SLIDER_MAX_MS - self.BOOT_SLIDER_MIN_MS)
        ratio = max(0.0, min(1.0, ratio))
        filled = pygame.Rect(track.x, track.y, int(track.w * ratio), track.h)
        pygame.draw.rect(canvas, (90, 180, 145), filled, border_radius=3)

        handle_x = track.x + int(track.w * ratio)
        handle_r = 11
        handle_hover = self.slider_dragging or (pygame.Vector2(canvas_mouse).distance_to(
            (handle_x, slider_rect.centery)) <= handle_r + 4)
        pygame.draw.circle(canvas, (255, 255, 255), (handle_x, slider_rect.centery), handle_r)
        pygame.draw.circle(canvas, (90, 180, 145) if handle_hover else (150, 150, 150),
                            (handle_x, slider_rect.centery), handle_r, 3)

        min_lbl = FONT_SMALL.render("0s", True, GRAY)
        max_lbl = FONT_SMALL.render("5s", True, GRAY)
        canvas.blit(min_lbl, (slider_rect.x, slider_rect.bottom + 2))
        canvas.blit(max_lbl, (slider_rect.right - max_lbl.get_width(), slider_rect.bottom + 2))

        # --- Security section (PIN) ---
        canvas.blit(FONT_UI.render("Security", True, BLACK), (0, security_y))
        if self.pin_hash:
            sec_sub = "A PIN is set — StrapOS requires it before starting or unlocking."
        else:
            sec_sub = "Set a PIN so StrapOS requires it before starting."
        canvas.blit(FONT_SMALL.render(sec_sub, True, GRAY), (0, security_y + 24))

        self._st_security_rects = []
        if self.pin_hash:
            change_rect = pygame.Rect(0, sec_btn_y, 150, btn_h3)
            remove_rect = pygame.Rect(162, sec_btn_y, 150, btn_h3)
            for rect, key, label, base_color, fg in [
                (change_rect, "change", "Change PIN", (90, 150, 220), WHITE),
                (remove_rect, "remove", "Remove PIN", (205, 90, 90), WHITE),
            ]:
                hovered = rect.collidepoint(canvas_mouse)
                pygame.draw.rect(canvas, shade(base_color, -15) if hovered else base_color,
                                  rect, border_radius=10)
                lbl = FONT_SMALL.render(label, True, fg)
                canvas.blit(lbl, lbl.get_rect(center=rect.center))
                self._st_security_rects.append((rect, key))
        else:
            set_rect = pygame.Rect(0, sec_btn_y, 170, btn_h3)
            hovered = set_rect.collidepoint(canvas_mouse)
            base_color = (90, 150, 220)
            pygame.draw.rect(canvas, shade(base_color, -15) if hovered else base_color, set_rect, border_radius=10)
            lbl = FONT_SMALL.render("Set PIN", True, WHITE)
            canvas.blit(lbl, lbl.get_rect(center=set_rect.center))
            self._st_security_rects.append((set_rect, "set"))

        # --- Software Reset section (danger zone) ---
        pygame.draw.line(canvas, (225, 225, 228), (0, reset_y - 14), (content.w, reset_y - 14), 1)
        canvas.blit(FONT_UI.render("Software Reset", True, (180, 55, 55)), (0, reset_y))
        canvas.blit(FONT_SMALL.render(
            "Erases all settings, wallpapers, and imported apps. This cannot be undone.", True, GRAY),
            (0, reset_y + 24))

        reset_btn_rect = pygame.Rect(0, reset_y + 50, 230, reset_btn_h)
        self._st_reset_btn_rect = reset_btn_rect
        danger_color = (205, 75, 75)
        reset_hovered = reset_btn_rect.collidepoint(canvas_mouse)
        pygame.draw.rect(canvas, shade(danger_color, -18) if reset_hovered else danger_color,
                          reset_btn_rect, border_radius=10)
        reset_lbl = FONT_UI.render("Reset StrapOS", True, WHITE)
        canvas.blit(reset_lbl, reset_lbl.get_rect(center=reset_btn_rect.center))

        # blit only the visible scrolled slice of the canvas into the window
        visible = pygame.Rect(0, self.settings_scroll, content.w, content.h)
        screen.blit(canvas, content.topleft, area=visible)

        if total_content_h > content.h:
            track_x = content.right - 6
            bar_h = max(30, content.h * content.h / total_content_h)
            bar_y = content.y + (self.settings_scroll / max(1, total_content_h - content.h)) * (content.h - bar_h)
            pygame.draw.rect(screen, (210, 210, 215), (track_x, content.y, 4, content.h), border_radius=2)
            pygame.draw.rect(screen, (150, 150, 155), (track_x, bar_y, 4, bar_h), border_radius=2)

        self.draw_taskbar("Settings")

        if self.pin_setup_open:
            self.draw_pin_setup_overlay(win_rect, mouse_pos)
        elif self.wallpaper_picker_open:
            self.draw_wallpaper_picker_overlay(win_rect, mouse_pos)
        elif self.reset_confirm_open:
            self.draw_reset_confirm_overlay(win_rect, mouse_pos)

    def _boot_ms_from_pos(self, pos):
        rect = self._st_boot_slider_rect
        ratio = (pos[0] - rect.x) / max(1, rect.w)
        ratio = max(0.0, min(1.0, ratio))
        ms = self.BOOT_SLIDER_MIN_MS + ratio * (self.BOOT_SLIDER_MAX_MS - self.BOOT_SLIDER_MIN_MS)
        return int(round(ms / 100.0) * 100)  # snap to nearest 0.1s

    def draw_wallpaper_picker_overlay(self, win_rect, mouse_pos):
        overlay = pygame.Rect(win_rect.x + 16, win_rect.y + 56, win_rect.w - 32, win_rect.h - 76)
        pygame.draw.rect(screen, (246, 247, 249), overlay, border_radius=10)
        pygame.draw.rect(screen, (200, 200, 205), overlay, 2, border_radius=10)

        title = FONT_UI.render("Choose an Image", True, BLACK)
        screen.blit(title, (overlay.x + 16, overlay.y + 12))
        close_rect = pygame.Rect(overlay.right - 40, overlay.y + 10, 28, 28)
        hovered = close_rect.collidepoint(mouse_pos)
        pygame.draw.circle(screen, (210, 95, 95) if hovered else (225, 110, 110), close_rect.center, 14)
        xlbl = FONT_UI.render("x", True, WHITE)
        screen.blit(xlbl, xlbl.get_rect(center=close_rect.center))
        self._wp_picker_close_rect = close_rect

        toolbar_y = overlay.y + 48
        up_btn = pygame.Rect(overlay.x + 16, toolbar_y, 70, 28)
        home_btn = pygame.Rect(overlay.x + 94, toolbar_y, 80, 28)
        pygame.draw.rect(screen, LIGHT_GRAY, up_btn, border_radius=6)
        pygame.draw.rect(screen, LIGHT_GRAY, home_btn, border_radius=6)
        up_lbl = FONT_SMALL.render("⬆ Up", True, BLACK)
        home_lbl = FONT_SMALL.render("🏠 Home", True, BLACK)
        screen.blit(up_lbl, up_lbl.get_rect(center=up_btn.center))
        screen.blit(home_lbl, home_lbl.get_rect(center=home_btn.center))
        self._wp_picker_up_btn, self._wp_picker_home_btn = up_btn, home_btn

        path_rect = pygame.Rect(overlay.x + 182, toolbar_y, overlay.w - 198, 28)
        pygame.draw.rect(screen, WHITE, path_rect, border_radius=6)
        pygame.draw.rect(screen, (210, 210, 210), path_rect, 1, border_radius=6)
        path_text = self.wallpaper_picker_path
        if len(path_text) > 60:
            path_text = "..." + path_text[-60:]
        screen.blit(FONT_SMALL.render(path_text, True, GRAY), (path_rect.x + 8, path_rect.y + 6))

        list_area = pygame.Rect(overlay.x + 16, toolbar_y + 38, overlay.w - 32,
                                 overlay.bottom - (toolbar_y + 38) - 16)
        pygame.draw.rect(screen, WHITE, list_area, border_radius=8)
        pygame.draw.rect(screen, (220, 220, 220), list_area, 1, border_radius=8)
        self._wp_picker_list_area = list_area

        row_h = 30
        self._wp_picker_row_rects = []
        clip = screen.get_clip()
        screen.set_clip(list_area)
        if not self.wallpaper_picker_entries:
            screen.blit(FONT_SMALL.render("No folders or images here.", True, GRAY),
                        (list_area.x + 12, list_area.y + 12))
        for i, (kind, name) in enumerate(self.wallpaper_picker_entries):
            ry = list_area.y + i * row_h - self.wallpaper_picker_scroll
            if ry + row_h < list_area.y or ry > list_area.bottom:
                self._wp_picker_row_rects.append(None)
                continue
            row_rect = pygame.Rect(list_area.x + 4, ry, list_area.w - 8, row_h - 4)
            self._wp_picker_row_rects.append(row_rect)
            hovered_row = row_rect.collidepoint(mouse_pos)
            if hovered_row:
                pygame.draw.rect(screen, (222, 232, 250) if kind == "image" else (225, 240, 235),
                                  row_rect, border_radius=6)
            icon_rect = (row_rect.x + 4, row_rect.y + 3, 18, 18)
            if kind == "dir":
                draw_folder_icon(screen, icon_rect)
            else:
                draw_file_icon(screen, icon_rect)
            screen.blit(FONT_SMALL.render(name, True, BLACK), (row_rect.x + 30, row_rect.y + 5))
        screen.set_clip(clip)

    def handle_wallpaper_picker_click(self, pos):
        if self._wp_picker_close_rect.collidepoint(pos):
            self.close_wallpaper_picker()
            return
        if self._wp_picker_up_btn.collidepoint(pos):
            self.wp_picker_go_up()
            return
        if self._wp_picker_home_btn.collidepoint(pos):
            self.wallpaper_picker_path = STORAGE_ROOT
            self._wp_picker_refresh_entries()
            return
        for row_rect, (kind, name) in zip(self._wp_picker_row_rects, self.wallpaper_picker_entries):
            if row_rect and row_rect.collidepoint(pos):
                if kind == "dir":
                    self.wp_picker_go_into(name)
                else:
                    self.import_custom_wallpaper(name)
                return

    def draw_reset_confirm_overlay(self, win_rect, mouse_pos):
        overlay = pygame.Rect(win_rect.x + 16, win_rect.y + 56, win_rect.w - 32, win_rect.h - 76)
        s = pygame.Surface((overlay.w, overlay.h), pygame.SRCALPHA)
        pygame.draw.rect(s, (10, 10, 14, 190), s.get_rect(), border_radius=10)
        screen.blit(s, overlay.topleft)

        box_w, box_h = min(460, overlay.w - 40), 236
        box = pygame.Rect(0, 0, box_w, box_h)
        box.center = overlay.center
        pygame.draw.rect(screen, (250, 250, 252), box, border_radius=14)
        pygame.draw.rect(screen, (210, 210, 215), box, 1, border_radius=14)

        title = FONT_UI.render("Reset StrapOS?", True, BLACK)
        screen.blit(title, (box.x + 20, box.y + 16))

        lines = [
            ("This will permanently erase:", (70, 70, 75)),
            ("• All settings and your selected wallpaper", (70, 70, 75)),
            ("• Every custom wallpaper you've uploaded", (70, 70, 75)),
            ("• Every app you've imported (and their logs)", (70, 70, 75)),
            ("This cannot be undone.", (190, 70, 70)),
        ]
        y = box.y + 48
        for text, color in lines:
            screen.blit(FONT_SMALL.render(text, True, color), (box.x + 20, y))
            y += 21

        btn_h = 40
        gap = 12
        btn_w = (box.w - 40 - gap) / 2
        cancel_rect = pygame.Rect(box.x + 20, box.bottom - 56, btn_w, btn_h)
        confirm_rect = pygame.Rect(cancel_rect.right + gap, box.bottom - 56, btn_w, btn_h)
        self._reset_cancel_rect = cancel_rect
        self._reset_confirm_rect = confirm_rect

        c_hover = cancel_rect.collidepoint(mouse_pos)
        pygame.draw.rect(screen, shade(LIGHT_GRAY, -10) if c_hover else LIGHT_GRAY, cancel_rect, border_radius=8)
        c_lbl = FONT_SMALL.render("Cancel", True, BLACK)
        screen.blit(c_lbl, c_lbl.get_rect(center=cancel_rect.center))

        danger = (200, 70, 70)
        r_hover = confirm_rect.collidepoint(mouse_pos)
        pygame.draw.rect(screen, shade(danger, -18) if r_hover else danger, confirm_rect, border_radius=8)
        r_lbl = FONT_SMALL.render("Reset Everything", True, WHITE)
        screen.blit(r_lbl, r_lbl.get_rect(center=confirm_rect.center))

    def handle_reset_confirm_click(self, pos):
        if self._reset_cancel_rect.collidepoint(pos):
            self.reset_confirm_open = False
            return
        if self._reset_confirm_rect.collidepoint(pos):
            self.reset_confirm_open = False
            self.perform_factory_reset()
            return

    def draw_pin_setup_overlay(self, win_rect, mouse_pos):
        overlay = pygame.Rect(win_rect.x + 16, win_rect.y + 56, win_rect.w - 32, win_rect.h - 76)
        s = pygame.Surface((overlay.w, overlay.h), pygame.SRCALPHA)
        pygame.draw.rect(s, (10, 10, 14, 190), s.get_rect(), border_radius=10)
        screen.blit(s, overlay.topleft)

        box_w, box_h = min(300, overlay.w - 40), 400
        box = pygame.Rect(0, 0, box_w, box_h)
        box.center = overlay.center
        pygame.draw.rect(screen, (250, 250, 252), box, border_radius=14)
        pygame.draw.rect(screen, (210, 210, 215), box, 1, border_radius=14)

        titles = {"new": "Set a New PIN", "confirm": "Confirm PIN", "remove": "Enter Current PIN"}
        title = FONT_UI.render(titles.get(self.pin_setup_stage, "PIN"), True, BLACK)
        screen.blit(title, title.get_rect(center=(box.centerx, box.y + 26)))

        dots_y = box.y + 66
        self._draw_pin_dots(screen, (60, 150, 110), (200, 200, 205), box.centerx, dots_y,
                             len(self.pin_setup_input))

        if self.pin_setup_error:
            err = FONT_SMALL.render(self.pin_setup_error, True, (190, 70, 70))
            screen.blit(err, err.get_rect(center=(box.centerx, dots_y + 26)))

        pad_rect = pygame.Rect(box.x + 20, dots_y + 46, box.w - 40, 230)
        self._pin_setup_pad_rects = self._draw_pin_pad(screen, pad_rect, mouse_pos)

        cancel_rect = pygame.Rect(box.x + 20, box.bottom - 46, box.w - 40, 32)
        hovered = cancel_rect.collidepoint(mouse_pos)
        pygame.draw.rect(screen, shade(LIGHT_GRAY, -10) if hovered else LIGHT_GRAY, cancel_rect, border_radius=8)
        lbl = FONT_SMALL.render("Cancel", True, BLACK)
        screen.blit(lbl, lbl.get_rect(center=cancel_rect.center))
        self._pin_setup_cancel_rect = cancel_rect

    def handle_pin_setup_click(self, pos):
        if self._pin_setup_cancel_rect.collidepoint(pos):
            self._close_pin_setup()
            return
        for label, rect in self._pin_setup_pad_rects.items():
            if rect.collidepoint(pos):
                self._pin_setup_pad_press(label)
                return

    def _close_pin_setup(self):
        self.pin_setup_open = False
        self.pin_setup_stage = None
        self.pin_setup_input = ""
        self.pin_setup_first_value = None
        self.pin_setup_error = ""

    def _pin_setup_pad_press(self, label):
        if label == "⌫":
            self.pin_setup_input = self.pin_setup_input[:-1]
            self.pin_setup_error = ""
            return
        if label.isdigit() and len(self.pin_setup_input) < 4:
            self.pin_setup_input += label
            self.pin_setup_error = ""
            if len(self.pin_setup_input) == 4:
                self._process_pin_setup_stage()

    def _process_pin_setup_stage(self):
        if self.pin_setup_stage == "new":
            self.pin_setup_first_value = self.pin_setup_input
            self.pin_setup_input = ""
            self.pin_setup_stage = "confirm"

        elif self.pin_setup_stage == "confirm":
            if self.pin_setup_input == self.pin_setup_first_value:
                digest, salt = _hash_pin(self.pin_setup_input)
                self.pin_hash, self.pin_salt = digest, salt
                self.cfg["pin_hash"], self.cfg["pin_salt"] = digest, salt
                save_config(self.cfg)
                self._close_pin_setup()
                self.show_toast("PIN set — StrapOS will require it to start.", ok=True)
            else:
                self.pin_setup_error = "PINs didn't match — try again."
                self.pin_setup_input = ""
                self.pin_setup_stage = "new"
                self.pin_setup_first_value = None

        elif self.pin_setup_stage == "remove":
            if _verify_pin(self.pin_setup_input, self.pin_hash, self.pin_salt):
                self.pin_hash, self.pin_salt = None, None
                self.cfg["pin_hash"], self.cfg["pin_salt"] = None, None
                save_config(self.cfg)
                self._close_pin_setup()
                self.show_toast("PIN removed.", ok=True)
            else:
                self.pin_setup_error = "Incorrect PIN — try again."
                self.pin_setup_input = ""

    def handle_settings_click(self, pos):
        if self.pin_setup_open:
            self.handle_pin_setup_click(pos)
            return
        if self.reset_confirm_open:
            self.handle_reset_confirm_click(pos)
            return
        if self.wallpaper_picker_open:
            self.handle_wallpaper_picker_click(pos)
            return
        if self._st_close_rect.collidepoint(pos):
            self.go_home()
            return
        if not self._st_content_rect.collidepoint(pos):
            return  # click landed on window chrome/padding, not the scrollable content
        canvas_pos = self._settings_to_canvas_pos(pos)
        # (starting a drag on the boot-hold slider is handled earlier, in
        # run()'s MOUSEBUTTONDOWN, before a tap vs. scroll-drag is decided)
        if self._st_reset_btn_rect.collidepoint(canvas_pos):
            self.reset_confirm_open = True
            return
        for rect, key in self._st_security_rects:
            if rect.collidepoint(canvas_pos):
                if key in ("set", "change"):
                    self.pin_setup_open = True
                    self.pin_setup_stage = "new"
                    self.pin_setup_input = ""
                    self.pin_setup_first_value = None
                    self.pin_setup_error = ""
                elif key == "remove":
                    self.pin_setup_open = True
                    self.pin_setup_stage = "remove"
                    self.pin_setup_input = ""
                    self.pin_setup_error = ""
                return
        for remove_rect, entry in self._st_wallpaper_remove_rects:
            if remove_rect.collidepoint(canvas_pos):
                self.remove_custom_wallpaper(entry)
                return
        for rect, item in self._st_wallpaper_item_rects:
            if rect.collidepoint(canvas_pos):
                if item["kind"] == "builtin":
                    self.set_wallpaper(item["index"])
                elif item["kind"] == "custom":
                    self.set_custom_wallpaper(item["entry"]["path"])
                else:
                    self.open_wallpaper_picker()
                return
        for rect, val in self._st_clock_rects:
            if rect.collidepoint(canvas_pos):
                self.set_clock_format(val)
                return
        for rect, val in self._st_launchmode_rects:
            if rect.collidepoint(canvas_pos):
                self.set_launch_mode(val)
                return

    def handle_settings_scroll(self, direction):
        if self.pin_setup_open or self.reset_confirm_open:
            return
        if self.wallpaper_picker_open:
            max_scroll = max(0, len(self.wallpaper_picker_entries) * 30 - self._wp_picker_list_area.h)
            self.wallpaper_picker_scroll = max(0, min(max_scroll, self.wallpaper_picker_scroll - direction * 30))
            return
        max_scroll = max(0, self._st_total_content_h - self._st_content_rect.h)
        self.settings_scroll = max(0, min(max_scroll, self.settings_scroll - direction * 30))

    # ---------------- drawing: calculator ----------------
    def draw_calculator(self, mouse_pos):
        screen.blit(self.wallpaper_surface, (0, 0))
        win_rect, close_rect = self.draw_window("Calculator")
        self._calc_close_rect = close_rect

        content = pygame.Rect(win_rect.x + 24, win_rect.y + 58, win_rect.w - 48, win_rect.h - 78)

        # display
        display_h = 84
        display_rect = pygame.Rect(content.x, content.y, content.w, display_h)
        pygame.draw.rect(screen, (24, 26, 30), display_rect, border_radius=10)
        shown = self.calc_expression if self.calc_expression else "0"
        expr_render = FONT_TITLE.render(shown[-22:], True, (205, 255, 215))
        screen.blit(expr_render, (display_rect.right - expr_render.get_width() - 18,
                                   display_rect.bottom - expr_render.get_height() - 12))
        if self.calc_error:
            err_render = FONT_SMALL.render("Error — check your expression", True, (255, 130, 130))
            screen.blit(err_render, (display_rect.x + 16, display_rect.y + 12))

        # button grid
        rows = [
            ["C", "(", ")", "⌫"],
            ["7", "8", "9", "/"],
            ["4", "5", "6", "*"],
            ["1", "2", "3", "-"],
            ["0", ".", "=", "+"],
        ]
        grid_top = display_rect.bottom + 16
        btn_gap = 10
        btn_w = (content.w - btn_gap * 3) / 4
        btn_h = (content.bottom - grid_top - btn_gap * 4) / 5
        self._calc_buttons = []
        for r, row in enumerate(rows):
            for c, label in enumerate(row):
                bx = content.x + c * (btn_w + btn_gap)
                by = grid_top + r * (btn_h + btn_gap)
                rect = pygame.Rect(bx, by, btn_w, btn_h)
                self._calc_buttons.append((rect, label))
                hovered = rect.collidepoint(mouse_pos)
                if label == "=":
                    base_color = (70, 165, 130)
                elif label in ("/", "*", "-", "+"):
                    base_color = (214, 228, 224)
                elif label in ("C", "⌫"):
                    base_color = (235, 205, 205)
                else:
                    base_color = (235, 235, 238)
                color = shade(base_color, -18) if hovered else base_color
                pygame.draw.rect(screen, color, rect, border_radius=10)
                pygame.draw.rect(screen, shade(color, -40), rect, 1, border_radius=10)
                fg = WHITE if label == "=" else BLACK
                lbl = FONT_UI.render(label, True, fg)
                screen.blit(lbl, lbl.get_rect(center=rect.center))

        self.draw_taskbar("Calculator")

    def handle_calc_click(self, pos):
        if self._calc_close_rect.collidepoint(pos):
            self.go_home()
            return
        for rect, label in self._calc_buttons:
            if rect.collidepoint(pos):
                self._calc_press(label)
                return

    def _calc_press(self, label):
        if label == "C":
            self.calc_expression = ""
            self.calc_error = False
        elif label == "⌫":
            self.calc_expression = self.calc_expression[:-1]
            self.calc_error = False
        elif label == "=":
            try:
                result = safe_eval(self.calc_expression or "0")
                if isinstance(result, float) and result.is_integer():
                    result = int(result)
                self.calc_expression = str(result)
                self.calc_error = False
            except Exception:
                self.calc_error = True
        else:
            if self.calc_error:
                self.calc_expression = ""
                self.calc_error = False
            if len(self.calc_expression) < 40:
                self.calc_expression += label

    # ---------------- drawing: terminal ----------------
    def draw_terminal(self, mouse_pos):
        screen.blit(self.wallpaper_surface, (0, 0))
        win_rect, close_rect = self.draw_window("Terminal")
        self._term_close_rect = close_rect

        content = pygame.Rect(win_rect.x + 20, win_rect.y + 56, win_rect.w - 40, win_rect.h - 76)
        pygame.draw.rect(screen, (14, 16, 19), content, border_radius=8)
        pygame.draw.rect(screen, (60, 60, 65), content, 1, border_radius=8)

        # If a command launched something as its own window (desktop/subprocess
        # mode), show a small close button for it here, and notice if it has
        # already closed on its own.
        self._term_close_running_rect = None
        if self.term_running_proc is not None:
            if self.term_running_proc.poll() is not None:
                self.term_history.append(f"[StrapOS] '{self.term_running_name}' closed.")
                self.term_running_proc = None
                self.term_running_name = None
            else:
                btn_w, btn_h = 130, 26
                btn_rect = pygame.Rect(content.right - btn_w - 8, content.y + 8, btn_w, btn_h)
                hovered = btn_rect.collidepoint(mouse_pos)
                pygame.draw.rect(screen, shade((205, 90, 90), -15) if hovered else (205, 90, 90),
                                  btn_rect, border_radius=6)
                lbl = FONT_SMALL.render("✕ Close App", True, WHITE)
                screen.blit(lbl, lbl.get_rect(center=btn_rect.center))
                self._term_close_running_rect = btn_rect

        line_h = 20
        clip = screen.get_clip()
        screen.set_clip(content.inflate(-8, -8))

        max_lines = max(1, content.h // line_h - 1)
        visible = self.term_history[-max_lines:]
        y = content.y + 10
        for line in visible:
            if line.startswith("strapos:"):
                color = (150, 210, 255)
            elif line.startswith("[StrapOS]"):
                color = (230, 200, 120)
            else:
                color = (150, 230, 160)
            render = FONT_MONO.render(line[:110], True, color)
            screen.blit(render, (content.x + 12, y))
            y += line_h

        cursor = "█" if (pygame.time.get_ticks() // 500) % 2 == 0 else " "
        input_line = self.term_prompt + self.term_input + cursor
        render = FONT_MONO.render(input_line[:120], True, (235, 235, 235))
        screen.blit(render, (content.x + 12, y))

        try:
            input_line_rect = pygame.Rect(content.x, y, content.w, line_h)
            pygame.key.set_text_input_rect(to_real_rect(input_line_rect))
        except Exception:
            pass

        screen.set_clip(clip)
        self.draw_taskbar("Terminal")

    def handle_terminal_click(self, pos):
        if self._term_close_running_rect and self._term_close_running_rect.collidepoint(pos):
            self._close_running_terminal_command()
            return
        if self._term_close_rect.collidepoint(pos):
            self.go_home()

    def handle_terminal_key(self, event):
        """Handles RETURN/BACKSPACE only — printable characters come through
        TEXTINPUT events instead (see handle_terminal_text), which is the
        correct SDL-level source for typed text and the only one mobile
        virtual keyboards reliably send."""
        if event.key == pygame.K_RETURN:
            typed = self.term_input
            self.term_history.append(self.term_prompt + typed)
            cmd = typed.strip().lower()
            if cmd == "test":
                self._run_terminal_test_command()
            elif typed.strip():
                first_word = typed.strip().split()[0]
                self.term_history.append(f"strapos: command not found: {first_word}")
            self.term_history.append("")
            self.term_input = ""
            if len(self.term_history) > 400:
                self.term_history = self.term_history[-400:]
        elif event.key == pygame.K_BACKSPACE:
            self.term_input = self.term_input[:-1]

    def handle_terminal_text(self, text):
        if text and len(self.term_input) < 200:
            self.term_input += text

    # ---------------- Weather app (Open-Meteo) ----------------
    def open_weather_search(self, city_name):
        city_name = (city_name or "").strip()
        if not city_name:
            return
        self.weather_loading = True
        self.weather_error = None
        self.weather_last_city = city_name
        self.cfg["weather_last_city"] = city_name
        save_config(self.cfg)
        t = threading.Thread(target=self._fetch_weather_worker, args=(city_name,), daemon=True)
        self.weather_thread = t
        t.start()

    def _fetch_weather_worker(self, city_name):
        """Runs on a background thread — never blocks the OS while the
        network request is in flight."""
        try:
            geo_url = ("https://geocoding-api.open-meteo.com/v1/search?name="
                       + urllib.parse.quote(city_name) + "&count=1&language=en&format=json")
            with urllib.request.urlopen(geo_url, timeout=10) as resp:
                geo_data = json.loads(resp.read().decode("utf-8"))

            results = geo_data.get("results")
            if not results:
                self.weather_error = f"Couldn't find '{city_name}'."
                self.weather_data = None
                return

            place = results[0]
            lat, lon = place["latitude"], place["longitude"]
            display_name = place.get("name", city_name)
            country = place.get("country", "")

            forecast_url = (
                "https://api.open-meteo.com/v1/forecast"
                f"?latitude={lat}&longitude={lon}"
                "&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
                "weather_code,wind_speed_10m,is_day"
                "&daily=weather_code,temperature_2m_max,temperature_2m_min"
                "&timezone=auto"
            )
            with urllib.request.urlopen(forecast_url, timeout=10) as resp2:
                wdata = json.loads(resp2.read().decode("utf-8"))

            current = wdata.get("current", {})
            daily = wdata.get("daily", {})
            code = current.get("weather_code", 0)
            is_day = bool(current.get("is_day", 1))
            emoji, desc = WEATHER_CODES.get(code, ("🌡️", "Unknown"))
            if not is_day:
                if code == 0:
                    emoji, desc = "🌙", "Clear Night"
                elif code == 1:
                    emoji, desc = "✨", "Mostly Clear Night"
                elif code == 2:
                    emoji, desc = "🌙", "Partly Cloudy Night"

            daily_high = (daily.get("temperature_2m_max") or [None])[0]
            daily_low = (daily.get("temperature_2m_min") or [None])[0]

            self.weather_data = {
                "city": display_name,
                "country": country,
                "temp": current.get("temperature_2m"),
                "feels_like": current.get("apparent_temperature"),
                "humidity": current.get("relative_humidity_2m"),
                "wind": current.get("wind_speed_10m"),
                "emoji": emoji,
                "desc": desc,
                "high": daily_high,
                "low": daily_low,
                "is_day": is_day,
            }
            self.weather_error = None
        except urllib.error.URLError as e:
            self.weather_error = "Couldn't reach the internet — check your connection."
            self.weather_data = None
        except Exception as e:
            self.weather_error = f"Couldn't fetch weather: {e}"
            self.weather_data = None
        finally:
            self.weather_loading = False

    def _weather_mood_colors(self):
        """Soft pastel background colors (top, bottom) matching the current
        weather — the whole point of the 'relaxing' theme."""
        if not self.weather_data:
            return (168, 202, 220), (210, 230, 240)
        desc = self.weather_data.get("desc", "")
        is_day = self.weather_data.get("is_day", True)
        if not is_day:
            return (40, 50, 80), (72, 82, 112)
        if "Thunder" in desc:
            return (95, 95, 115), (135, 135, 155)
        if "Snow" in desc:
            return (200, 210, 225), (236, 240, 248)
        if "Rain" in desc or "Drizzle" in desc or "Showers" in desc:
            return (120, 140, 162), (172, 187, 197)
        if "Fog" in desc:
            return (172, 176, 181), (206, 209, 213)
        if "Overcast" in desc:
            return (165, 172, 180), (200, 205, 212)
        if "Cloud" in desc:
            return (150, 190, 215), (200, 220, 232)
        return (135, 195, 235), (190, 226, 246)

    def _get_weather_background(self, top_color, bottom_color, w, h):
        key = (top_color, bottom_color, w, h)
        if self._weather_bg_cache_key == key and self._weather_bg_cache is not None:
            return self._weather_bg_cache
        surf = pygame.Surface((max(1, w), max(1, h)))
        for y in range(max(1, h)):
            t = y / max(1, h - 1)
            r = int(top_color[0] + (bottom_color[0] - top_color[0]) * t)
            g = int(top_color[1] + (bottom_color[1] - top_color[1]) * t)
            b = int(top_color[2] + (bottom_color[2] - top_color[2]) * t)
            pygame.draw.line(surf, (r, g, b), (0, y), (w, y))
        self._weather_bg_cache = surf
        self._weather_bg_cache_key = key
        return surf

    def _draw_soft_cloud(self, surf, cx, cy, scale, alpha):
        w, h = int(140 * scale), int(70 * scale)
        s = pygame.Surface((max(1, w), max(1, h)), pygame.SRCALPHA)
        color = (255, 255, 255, alpha)
        pygame.draw.ellipse(s, color, (0, int(20 * scale), int(90 * scale), int(40 * scale)))
        pygame.draw.ellipse(s, color, (int(30 * scale), 0, int(70 * scale), int(50 * scale)))
        pygame.draw.ellipse(s, color, (int(60 * scale), int(15 * scale), int(80 * scale), int(45 * scale)))
        surf.blit(s, (cx - s.get_width() // 2, cy - s.get_height() // 2))

    def draw_weather(self, mouse_pos):
        screen.blit(self.wallpaper_surface, (0, 0))
        win_rect, close_rect = self.draw_window("Weather")
        self._weather_close_rect = close_rect

        content = pygame.Rect(win_rect.x + 16, win_rect.y + 56, win_rect.w - 32, win_rect.h - 76)

        top_color, bottom_color = self._weather_mood_colors()
        bg = self._get_weather_background(top_color, bottom_color, content.w, content.h)
        screen.blit(bg, content.topleft)

        # gentle drifting clouds for ambiance
        ticks = pygame.time.get_ticks()
        for i, (offset, speed, y_off, scale, alpha) in enumerate([
            (0, 0.012, 55, 1.0, 60), (260, 0.008, 130, 0.7, 45), (480, 0.010, 85, 0.85, 50),
        ]):
            span = content.w + 200
            drift_x = content.x + ((offset + ticks * speed) % span) - 100
            self._draw_soft_cloud(screen, drift_x, content.y + y_off, scale, alpha)

        cy = content.y + 26
        self._weather_refresh_rect = None

        if self.weather_loading:
            pulse = 140 + int(80 * math.sin(ticks / 260))
            loading_render = FONT_UI.render("Loading weather…", True, (pulse, pulse, pulse))
            screen.blit(loading_render, loading_render.get_rect(center=(content.centerx, content.centery - 10)))
        elif self.weather_error:
            err_render = FONT_UI.render(self.weather_error, True, (120, 45, 45))
            screen.blit(err_render, err_render.get_rect(center=(content.centerx, content.centery - 20)))
            hint = FONT_SMALL.render("Try searching a different city below.", True, (80, 80, 85))
            screen.blit(hint, hint.get_rect(center=(content.centerx, content.centery + 12)))
        elif self.weather_data:
            d = self.weather_data
            emoji_render = FONT_WEATHER_EMOJI.render(d["emoji"], True, BLACK)
            screen.blit(emoji_render, emoji_render.get_rect(center=(content.centerx, cy + 55)))

            temp_text = f"{round(d['temp'])}°C" if d.get("temp") is not None else "--"
            temp_render = FONT_WEATHER_TEMP.render(temp_text, True, (35, 35, 40))
            screen.blit(temp_render, temp_render.get_rect(center=(content.centerx, cy + 148)))

            desc_render = FONT_UI.render(d["desc"], True, (45, 45, 50))
            screen.blit(desc_render, desc_render.get_rect(center=(content.centerx, cy + 186)))

            loc_text = d["city"] + (f", {d['country']}" if d.get("country") else "")
            loc_render = FONT_SMALL.render(loc_text, True, (65, 65, 70))
            screen.blit(loc_render, loc_render.get_rect(center=(content.centerx, cy + 210)))

            stats_bits = []
            if d.get("feels_like") is not None:
                stats_bits.append(f"Feels like {round(d['feels_like'])}°")
            if d.get("humidity") is not None:
                stats_bits.append(f"💧 {d['humidity']}%")
            if d.get("wind") is not None:
                stats_bits.append(f"💨 {round(d['wind'])} km/h")
            if stats_bits:
                stats_render = FONT_SMALL.render("   ·   ".join(stats_bits), True, (55, 55, 60))
                screen.blit(stats_render, stats_render.get_rect(center=(content.centerx, cy + 248)))

            hl_bits = []
            if d.get("high") is not None:
                hl_bits.append(f"H: {round(d['high'])}°")
            if d.get("low") is not None:
                hl_bits.append(f"L: {round(d['low'])}°")
            if hl_bits:
                hl_render = FONT_SMALL.render("    ".join(hl_bits), True, (55, 55, 60))
                screen.blit(hl_render, hl_render.get_rect(center=(content.centerx, cy + 274)))

            refresh_rect = pygame.Rect(content.right - 46, content.y + 10, 32, 32)
            hovered = refresh_rect.collidepoint(mouse_pos)
            rs = pygame.Surface((32, 32), pygame.SRCALPHA)
            pygame.draw.circle(rs, (255, 255, 255, 170 if hovered else 125), (16, 16), 16)
            screen.blit(rs, refresh_rect.topleft)
            rlbl = FONT_UI.render("🔄", True, BLACK)
            screen.blit(rlbl, rlbl.get_rect(center=refresh_rect.center))
            self._weather_refresh_rect = refresh_rect
        else:
            hint = FONT_UI.render("Search a city to see its weather", True, (55, 55, 60))
            screen.blit(hint, hint.get_rect(center=(content.centerx, content.centery - 20)))

        # search bar
        search_h = 44
        search_rect = pygame.Rect(content.x + 16, content.bottom - search_h - 16,
                                   content.w - 32 - 90, search_h)
        go_rect = pygame.Rect(search_rect.right + 8, search_rect.y, 82, search_h)

        ss = pygame.Surface((search_rect.w, search_rect.h), pygame.SRCALPHA)
        pygame.draw.rect(ss, (255, 255, 255, 215), ss.get_rect(), border_radius=search_h // 2)
        screen.blit(ss, search_rect.topleft)

        cursor = "|" if (ticks // 500) % 2 == 0 else " "
        if self.weather_city_query:
            q_render = FONT_UI.render(self.weather_city_query + cursor, True, BLACK)
        else:
            q_render = FONT_UI.render("Search a city…", True, (150, 150, 155))
        screen.blit(q_render, (search_rect.x + 16, search_rect.y + (search_rect.h - q_render.get_height()) // 2))

        go_hovered = go_rect.collidepoint(mouse_pos)
        go_color = (90, 150, 220)
        pygame.draw.rect(screen, shade(go_color, -15) if go_hovered else go_color, go_rect,
                          border_radius=search_h // 2)
        go_lbl = FONT_SMALL.render("🔍 Go", True, WHITE)
        screen.blit(go_lbl, go_lbl.get_rect(center=go_rect.center))

        self._weather_search_rect = search_rect
        self._weather_go_rect = go_rect

        self.draw_taskbar("Weather")

    def handle_weather_click(self, pos):
        if self._weather_close_rect.collidepoint(pos):
            self.go_home()
            return
        if self._weather_go_rect and self._weather_go_rect.collidepoint(pos):
            self.open_weather_search(self.weather_city_query)
            return
        if self._weather_refresh_rect and self._weather_refresh_rect.collidepoint(pos):
            if self.weather_data:
                self.open_weather_search(self.weather_data["city"])
            return

    def handle_weather_key(self, event):
        if event.key == pygame.K_RETURN:
            self.open_weather_search(self.weather_city_query)
        elif event.key == pygame.K_BACKSPACE:
            self.weather_city_query = self.weather_city_query[:-1]

    def handle_weather_text(self, text):
        if text and len(self.weather_city_query) < 60:
            self.weather_city_query += text

    def _ensure_demo_app_file(self):
        try:
            os.makedirs(STRAPOS_APPS_DIR, exist_ok=True)
            path = os.path.join(STRAPOS_APPS_DIR, "strapos_demo_app.py")
            if not os.path.exists(path):
                with open(path, "w") as f:
                    f.write(DEMO_APP_SOURCE)
            return path
        except Exception:
            return None

    def _run_terminal_test_command(self):
        if self.term_running_proc is not None and self.term_running_proc.poll() is None:
            self.term_history.append(
                "[StrapOS] The demo app is already running — use ✕ Close App to close it first.")
            return

        demo_path = self._ensure_demo_app_file()
        if demo_path is None:
            self.term_history.append("strapos: could not create the demo app file.")
            return

        name = "StrapOS Demo App"
        mode = self.resolve_launch_mode()

        if mode == "inprocess":
            self.term_history.append("[StrapOS] Launching the demo app in this window…")
            self.pending_inprocess_launch = {"path": demo_path, "name": name}
            return

        try:
            os.makedirs(STRAPOS_LOGS_DIR, exist_ok=True)
            log_path = self.get_app_log_path(name)
            logf = open(log_path, "w")
            proc = subprocess.Popen(
                [sys.executable, "-u", demo_path],
                cwd=os.path.dirname(demo_path) or None,
                stdout=logf,
                stderr=subprocess.STDOUT,
            )
            logf.close()
            self.term_running_proc = proc
            self.term_running_name = name
            self.launch_watch.append({
                "proc": proc, "name": name, "log_path": log_path,
                "start": pygame.time.get_ticks(),
            })
            self.term_history.append("[StrapOS] Demo app launched in its own window — everything's working!")
            self.term_history.append("[StrapOS] Use the ✕ Close App button here to close it anytime.")
        except Exception as e:
            self.term_history.append(f"strapos: failed to launch demo app: {e}")

    def _close_running_terminal_command(self):
        if self.term_running_proc is None:
            return
        name = self.term_running_name or "app"
        try:
            self.term_running_proc.terminate()
        except Exception:
            pass
        self.term_history.append(f"[StrapOS] Closed '{name}'.")
        self.term_running_proc = None
        self.term_running_name = None

    # ---------------- Strap App Loader: logic ----------------
    def show_toast(self, message, ok=True):
        self.toast_message = message
        self.toast_ok = ok
        self.toast_until = pygame.time.get_ticks() + 3000

    def _loader_refresh_entries(self):
        self.loader_scroll = 0
        try:
            names = os.listdir(self.loader_path)
        except Exception:
            self.loader_entries = []
            return
        dirs, files = [], []
        for n in names:
            full = os.path.join(self.loader_path, n)
            try:
                if os.path.isdir(full):
                    dirs.append(n)
                elif n.lower().endswith(".py"):
                    files.append(n)
            except Exception:
                continue
        dirs.sort(key=str.lower)
        files.sort(key=str.lower)
        self.loader_entries = [("dir", d) for d in dirs] + [("pyfile", f) for f in files]

    def loader_go_up(self):
        parent = os.path.dirname(self.loader_path.rstrip(os.sep))
        if parent and os.path.isdir(parent):
            self.loader_path = parent
            self._loader_refresh_entries()

    def loader_go_into(self, name):
        full = os.path.join(self.loader_path, name)
        if os.path.isdir(full):
            self.loader_path = full
            self._loader_refresh_entries()

    def import_py_file(self, name):
        src = os.path.abspath(os.path.join(self.loader_path, name))
        if not os.path.isfile(src):
            self.show_toast(f"'{name}' not found.", ok=False)
            return
        try:
            os.makedirs(STRAPOS_APPS_DIR, exist_ok=True)

            # already imported from this exact source? just relaunch-ready, no duplicate.
            for a in self.installed_apps:
                if a.get("source") == src:
                    self.show_toast(f"'{a['name']}' is already imported.", ok=True)
                    return

            stem, ext = os.path.splitext(os.path.basename(src))
            dest = os.path.join(STRAPOS_APPS_DIR, stem + ext)
            counter = 1
            while os.path.exists(dest):
                dest = os.path.join(STRAPOS_APPS_DIR, f"{stem}_{counter}{ext}")
                counter += 1

            shutil.copy2(src, dest)
            app_name = os.path.splitext(os.path.basename(dest))[0]
            self.installed_apps.append({"name": app_name, "path": dest, "source": src})
            self.cfg["installed_apps"] = self.installed_apps
            save_config(self.cfg)
            self._build_desktop_icons()
            self.show_toast(f"Imported '{app_name}' — it's on your desktop now!", ok=True)
        except Exception as e:
            self.show_toast(f"Import failed: {e}", ok=False)

    def get_app_log_path(self, name):
        return os.path.join(STRAPOS_LOGS_DIR, f"{_safe_filename(name)}.log")

    def _read_log_tail(self, log_path, max_chars=4000):
        try:
            with open(log_path, "r", errors="replace") as f:
                content = f.read()
        except Exception:
            return ""
        content = content.strip()
        return content[-max_chars:] if content else ""

    def view_app_log(self, entry):
        name = entry.get("name", "App")
        content = self._read_log_tail(self.get_app_log_path(name))
        self.loader_log_view = {"name": name, "content": content}

    def _open_url_android(self, url):
        """
        Launch the device's default browser via a real Android Intent. Uses
        android.app.ActivityThread to grab the current process's Application
        Context generically — this works regardless of which Activity class
        the host app (Pydroid, a Buildozer build, etc.) actually uses, so it
        doesn't depend on knowing that class name in advance.
        """
        from jnius import autoclass
        Intent = autoclass("android.content.Intent")
        Uri = autoclass("android.net.Uri")
        ActivityThread = autoclass("android.app.ActivityThread")
        context = ActivityThread.currentActivityThread().getApplication().getApplicationContext()
        intent = Intent(Intent.ACTION_VIEW, Uri.parse(url))
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)

    def open_real_browser(self, url="https://www.google.com"):
        """Open the device's actual default browser (not an embedded one)."""
        if IS_ANDROID:
            try:
                self._open_url_android(url)
                self.show_toast("Opening your browser…", ok=True)
                return
            except Exception:
                pass  # fall through and try the desktop-style approach too

        try:
            import webbrowser
            opened = webbrowser.open(url)
            if opened:
                self.show_toast("Opening your browser…", ok=True)
            else:
                self.show_toast("Couldn't open a browser automatically on this device.", ok=False)
        except Exception as e:
            self.show_toast(f"Couldn't open browser: {e}", ok=False)

    def launch_app(self, path, name):
        if not path or not os.path.exists(path):
            self.show_toast(f"Cannot launch '{name}': file missing.", ok=False)
            return

        mode = self.resolve_launch_mode()

        if mode == "inprocess":
            # Android/Pydroid: only one display surface exists per process, so we
            # can't open a real second window. Instead we defer to the top of the
            # next frame (so this toast actually gets drawn first), then hand our
            # own pygame window over to the app's code directly.
            self.pending_inprocess_launch = {"path": path, "name": name}
            self.show_toast(f"Opening '{name}'…", ok=True)
            return

        # Desktop: launch as a real separate OS process/window.
        try:
            os.makedirs(STRAPOS_LOGS_DIR, exist_ok=True)
            log_path = self.get_app_log_path(name)
            logf = open(log_path, "w")
            proc = subprocess.Popen(
                [sys.executable, "-u", path],
                cwd=os.path.dirname(path) or None,
                stdout=logf,
                stderr=subprocess.STDOUT,
            )
            logf.close()  # the child keeps its own duplicated handle; safe to close ours
            self.launch_watch.append({
                "proc": proc, "name": name, "log_path": log_path,
                "start": pygame.time.get_ticks(),
            })
            self.show_toast(f"Launching '{name}'…", ok=True)
        except Exception as e:
            self.show_toast(f"Failed to launch '{name}': {e}", ok=False)

    def _run_app_inprocess(self, path, name):
        """
        Run an imported app's code directly inside StrapOS's own process/window
        (used on Android/Pydroid, where a subprocess can't get a display of its
        own). This blocks until the app's own loop exits, then StrapOS rebuilds
        its window and resumes. Output is captured to the app's log file.

        What makes this reliable:
          - pygame.display.set_mode() never actually re-initializes the native
            display a second time — Android only expects one per process, and
            a second real init can silently kill the whole app without ever
            raising a Python exception. The app is just handed the surface
            StrapOS already owns instead (see _patched_display_set_mode).
          - pygame.event.get()/poll()/wait() are patched for the app's entire
            run so QUIT events never reach it — the only sanctioned way back
            to StrapOS while hosted is the floating back button.
          - The back button ignores any already-held press and a brief grace
            period right after launch, so it can never fire on its own from
            residual touch state left over from tapping "Launch".

        Returns (ok, elapsed_seconds, reason) so the caller can tell a clean
        long-running session apart from a suspiciously fast exit.
        """
        log_path = self.get_app_log_path(name)
        prev_cwd = os.getcwd()
        app_dir = os.path.dirname(path) or prev_cwd
        ok = True
        reason = "normal"
        start = time.time()
        try:
            os.chdir(app_dir)
        except Exception:
            pass

        # Drop any stale/leftover events before the app gets its first look.
        pygame.event.pump()
        pygame.event.clear()
        _back_btn_state["rect"] = None
        _back_btn_state["was_pressed"] = False
        _back_btn_state["armed_at"] = pygame.time.get_ticks() + 600  # ignore taps for 0.6s
        pygame.display.flip = _patched_display_flip
        pygame.display.update = _patched_display_update
        pygame.display.set_mode = _patched_display_set_mode
        pygame.event.get = _patched_event_get
        pygame.event.poll = _patched_event_poll
        pygame.event.wait = _patched_event_wait

        try:
            os.makedirs(STRAPOS_LOGS_DIR, exist_ok=True)
            with open(log_path, "w") as logf:
                print(f"[StrapOS] Launching '{name}' at {time.strftime('%H:%M:%S')}", file=logf, flush=True)
                with contextlib.redirect_stdout(logf), contextlib.redirect_stderr(logf):
                    try:
                        runpy.run_path(path, run_name="__main__")
                    except _ReturnToStrapOS:
                        reason = "back_button"
                    except SystemExit as e:
                        code = e.code
                        if code in (None, 0):
                            reason = "sys_exit_clean"
                        else:
                            ok = False
                            reason = f"sys_exit_error({code!r})"
                    except Exception:
                        ok = False
                        reason = "exception"
                        traceback.print_exc(file=logf)
                elapsed = time.time() - start
                print(f"[StrapOS] '{name}' finished after {elapsed:.2f}s — reason: {reason}, ok: {ok}", file=logf)
        except Exception:
            ok = False
            reason = "host_error"
        finally:
            pygame.display.flip = _original_display_flip
            pygame.display.update = _original_display_update
            pygame.display.set_mode = _original_display_set_mode
            pygame.event.get = _original_event_get
            pygame.event.poll = _original_event_poll
            pygame.event.wait = _original_event_wait
            try:
                os.chdir(prev_cwd)
            except Exception:
                pass
            reinit_strapos_display()
        elapsed = time.time() - start
        return ok, elapsed, reason

    def _check_launch_watch(self):
        """Poll recently-launched apps briefly to catch instant crashes AND
        suspiciously-fast clean exits (e.g. a script missing its own event
        loop) — mirrors the diagnostics used for in-process/Android launches."""
        if not self.launch_watch:
            return
        now = pygame.time.get_ticks()
        still_watching = []
        for entry in self.launch_watch:
            elapsed = now - entry["start"]
            ret = entry["proc"].poll()
            if ret is None:
                # still running — keep watching briefly, then assume it's fine
                if elapsed < 1200:
                    still_watching.append(entry)
                continue
            if elapsed < 1200:
                tail = self._read_log_tail(entry["log_path"], max_chars=160).replace("\n", " | ")
                if ret != 0:
                    msg = f"'{entry['name']}' crashed on launch (exit code {ret})."
                else:
                    msg = (f"'{entry['name']}' closed after only {elapsed / 1000:.2f}s — it may be "
                           f"missing its own event loop, or something ended it early.")
                if tail:
                    msg += f" {tail}"
                self.show_toast(msg[:150], ok=False)
            # ran long enough to be a real session — no extra message needed
        self.launch_watch = still_watching

    def remove_app(self, entry):
        self.installed_apps = [a for a in self.installed_apps if a.get("path") != entry.get("path")]
        self.cfg["installed_apps"] = self.installed_apps
        save_config(self.cfg)
        try:
            if entry.get("path") and os.path.exists(entry["path"]):
                os.remove(entry["path"])
        except Exception:
            pass
        self._build_desktop_icons()
        self.show_toast(f"Removed '{entry.get('name', 'app')}'.", ok=True)

    # ---------------- Strap App Loader: drawing ----------------
    def draw_apploader(self, mouse_pos):
        screen.blit(self.wallpaper_surface, (0, 0))
        win_rect, close_rect = self.draw_window("Strap App Loader")
        self._loader_close_rect = close_rect

        content = pygame.Rect(win_rect.x + 16, win_rect.y + 58, win_rect.w - 32, win_rect.h - 74)

        # toolbar
        up_btn = pygame.Rect(content.x, content.y, 70, 30)
        home_btn = pygame.Rect(content.x + 78, content.y, 80, 30)
        pygame.draw.rect(screen, LIGHT_GRAY, up_btn, border_radius=6)
        pygame.draw.rect(screen, LIGHT_GRAY, home_btn, border_radius=6)
        up_lbl = FONT_SMALL.render("⬆ Up", True, BLACK)
        home_lbl = FONT_SMALL.render("🏠 Home", True, BLACK)
        screen.blit(up_lbl, up_lbl.get_rect(center=up_btn.center))
        screen.blit(home_lbl, home_lbl.get_rect(center=home_btn.center))
        self._loader_up_btn, self._loader_home_btn = up_btn, home_btn

        path_rect = pygame.Rect(content.x + 166, content.y, content.w - 166, 30)
        pygame.draw.rect(screen, WHITE, path_rect, border_radius=6)
        pygame.draw.rect(screen, (210, 210, 210), path_rect, 1, border_radius=6)
        path_text = self.loader_path
        if len(path_text) > 70:
            path_text = "..." + path_text[-70:]
        screen.blit(FONT_SMALL.render(path_text, True, GRAY), (path_rect.x + 10, path_rect.y + 7))

        header_y = content.y + 42
        list_area = pygame.Rect(content.x, header_y + 22, content.w * 0.52 - 8, content.h - 42 - 22)
        apps_area = pygame.Rect(list_area.right + 16, header_y + 22, content.right - (list_area.right + 16),
                                 content.h - 42 - 22)

        screen.blit(FONT_SMALL.render("Browse & click a .py file to import it", True, GRAY),
                    (list_area.x, header_y))
        screen.blit(FONT_UI.render("Installed Apps", True, BLACK), (apps_area.x, header_y - 3))

        pygame.draw.rect(screen, WHITE, list_area, border_radius=8)
        pygame.draw.rect(screen, (220, 220, 220), list_area, 1, border_radius=8)
        pygame.draw.rect(screen, (250, 250, 252), apps_area, border_radius=8)
        pygame.draw.rect(screen, (220, 220, 220), apps_area, 1, border_radius=8)

        # --- left: file browser (filtered to folders + .py files) ---
        row_h = 30
        self._loader_row_rects = []
        clip = screen.get_clip()
        screen.set_clip(list_area)
        if not self.loader_entries:
            screen.blit(FONT_SMALL.render("No folders or .py files here.", True, GRAY),
                        (list_area.x + 12, list_area.y + 12))
        for i, (kind, name) in enumerate(self.loader_entries):
            ry = list_area.y + i * row_h - self.loader_scroll
            if ry + row_h < list_area.y or ry > list_area.bottom:
                self._loader_row_rects.append(None)
                continue
            row_rect = pygame.Rect(list_area.x + 4, ry, list_area.w - 8, row_h - 4)
            self._loader_row_rects.append(row_rect)
            hovered = row_rect.collidepoint(mouse_pos)
            if hovered:
                pygame.draw.rect(screen, (222, 232, 250) if kind == "pyfile" else (225, 240, 235),
                                  row_rect, border_radius=6)
            icon_rect = (row_rect.x + 4, row_rect.y + 3, 18, 18)
            if kind == "dir":
                draw_folder_icon(screen, icon_rect)
            else:
                draw_file_icon(screen, icon_rect)
            label = FONT_SMALL.render(name, True, BLACK)
            screen.blit(label, (row_rect.x + 30, row_rect.y + 5))
        screen.set_clip(clip)
        self._loader_list_area = list_area

        # --- right: installed apps management ---
        clip2 = screen.get_clip()
        screen.set_clip(apps_area)
        self._loader_app_rows = []
        app_row_h = 56
        if not self.installed_apps:
            screen.blit(FONT_SMALL.render("No apps imported yet.", True, GRAY), (apps_area.x + 12, apps_area.y + 12))
            screen.blit(FONT_SMALL.render("Click a .py file on the left to add one.", True, GRAY),
                        (apps_area.x + 12, apps_area.y + 32))
        for i, entry in enumerate(self.installed_apps):
            ry = apps_area.y + i * app_row_h - self.loader_apps_scroll
            if ry + app_row_h < apps_area.y or ry > apps_area.bottom:
                self._loader_app_rows.append(None)
                continue
            row_rect = pygame.Rect(apps_area.x + 8, ry + 4, apps_area.w - 16, app_row_h - 10)
            pygame.draw.rect(screen, WHITE, row_rect, border_radius=8)
            pygame.draw.rect(screen, (225, 225, 228), row_rect, 1, border_radius=8)

            name_lbl = FONT_SMALL.render(entry.get("name", "App"), True, BLACK)
            screen.blit(name_lbl, (row_rect.x + 10, row_rect.y + 8))

            btn_h = 26
            btn_defs = [("Remove", 60, (215, 140, 140)), ("Launch", 60, (90, 175, 140)), ("Log", 46, (140, 160, 220))]
            btn_rects = {}
            x_cursor = row_rect.right - 8
            for label, bw, color in btn_defs:
                x_cursor -= bw
                rect = pygame.Rect(x_cursor, row_rect.y + row_rect.h - btn_h - 6, bw, btn_h)
                btn_rects[label] = rect
                x_cursor -= 6

            for label, rect in btn_rects.items():
                base_color = dict((l, c) for l, _, c in btn_defs)[label]
                hovered = rect.collidepoint(mouse_pos)
                pygame.draw.rect(screen, shade(base_color, -15) if hovered else base_color, rect, border_radius=6)
                lbl = FONT_SMALL.render(label, True, WHITE)
                screen.blit(lbl, lbl.get_rect(center=rect.center))

            self._loader_app_rows.append((row_rect, btn_rects, entry))
        screen.set_clip(clip2)
        self._loader_apps_area = apps_area

        if self.loader_log_view:
            self._draw_log_overlay(content)

        self.draw_taskbar("Strap App Loader")

    def _draw_log_overlay(self, content):
        overlay = pygame.Rect(content.x, content.y, content.w, content.h)
        s = pygame.Surface((overlay.w, overlay.h), pygame.SRCALPHA)
        pygame.draw.rect(s, (12, 13, 17, 238), s.get_rect(), border_radius=10)
        screen.blit(s, overlay.topleft)

        title = FONT_UI.render(f"Log — {self.loader_log_view['name']}", True, WHITE)
        screen.blit(title, (overlay.x + 16, overlay.y + 14))
        hint = FONT_SMALL.render("Captured stdout/stderr from the last launch", True, (170, 170, 175))
        screen.blit(hint, (overlay.x + 16, overlay.y + 38))

        close_rect = pygame.Rect(overlay.right - 40, overlay.y + 10, 28, 28)
        pygame.draw.circle(screen, (210, 95, 95), close_rect.center, 14)
        xlbl = FONT_UI.render("x", True, WHITE)
        screen.blit(xlbl, xlbl.get_rect(center=close_rect.center))
        self._loader_log_close_rect = close_rect

        body = pygame.Rect(overlay.x + 16, overlay.y + 64, overlay.w - 32, overlay.h - 80)
        clip = screen.get_clip()
        screen.set_clip(body)
        text = self.loader_log_view["content"] or "(no output captured — the app may not print anything, or hasn't run yet)"
        y = body.y
        for line in text.split("\n"):
            render = FONT_MONO.render(line[:115], True, (215, 215, 220))
            screen.blit(render, (body.x, y))
            y += 18
            if y > body.bottom:
                break
        screen.set_clip(clip)

    def handle_apploader_click(self, pos):
        if self.loader_log_view:
            if self._loader_log_close_rect.collidepoint(pos):
                self.loader_log_view = None
            return  # overlay is modal — swallow clicks while it's open

        if self._loader_close_rect.collidepoint(pos):
            self.go_home()
            return
        if self._loader_up_btn.collidepoint(pos):
            self.loader_go_up()
            return
        if self._loader_home_btn.collidepoint(pos):
            self.loader_path = STORAGE_ROOT
            self._loader_refresh_entries()
            return
        for row_rect, (kind, name) in zip(self._loader_row_rects, self.loader_entries):
            if row_rect and row_rect.collidepoint(pos):
                if kind == "dir":
                    self.loader_go_into(name)
                else:
                    self.import_py_file(name)
                return
        for row in self._loader_app_rows:
            if row is None:
                continue
            row_rect, btn_rects, entry = row
            if btn_rects["Launch"].collidepoint(pos):
                self.launch_app(entry.get("path"), entry.get("name", "App"))
                return
            if btn_rects["Remove"].collidepoint(pos):
                self.remove_app(entry)
                return
            if btn_rects["Log"].collidepoint(pos):
                self.view_app_log(entry)
                return

    def handle_apploader_scroll(self, direction, pos):
        if self._loader_list_area.collidepoint(pos):
            max_scroll = max(0, len(self.loader_entries) * 30 - self._loader_list_area.h)
            self.loader_scroll = max(0, min(max_scroll, self.loader_scroll - direction * 30))
        elif self._loader_apps_area.collidepoint(pos):
            max_scroll = max(0, len(self.installed_apps) * 56 - self._loader_apps_area.h)
            self.loader_apps_scroll = max(0, min(max_scroll, self.loader_apps_scroll - direction * 30))

    # ---------------- touch/drag scrolling (mobile) ----------------
    def _get_scrollable_area_for_state(self, pos):
        """Which scrollable list (if any) is under `pos` right now, given the
        current screen/modal. Returns an area_key string or None. A press
        that starts inside one of these becomes a drag-scroll candidate
        instead of firing its click immediately."""
        if self.state == self.STATE_FILES:
            if self.fm_upload_open:
                if hasattr(self, "_fm_upload_list_area") and self._fm_upload_list_area.collidepoint(pos):
                    return "fm_upload_list"
            else:
                if hasattr(self, "_fm_list_area") and self._fm_list_area.collidepoint(pos):
                    return "fm_list"
        elif self.state == self.STATE_APPLOADER and not self.loader_log_view:
            if hasattr(self, "_loader_list_area") and self._loader_list_area.collidepoint(pos):
                return "loader_list"
            if hasattr(self, "_loader_apps_area") and self._loader_apps_area.collidepoint(pos):
                return "loader_apps"
        elif self.state == self.STATE_SETTINGS:
            if self.wallpaper_picker_open:
                if hasattr(self, "_wp_picker_list_area") and self._wp_picker_list_area.collidepoint(pos):
                    return "wp_picker_list"
            elif not (self.reset_confirm_open or self.pin_setup_open):
                if hasattr(self, "_st_content_rect") and self._st_content_rect.collidepoint(pos):
                    return "settings"
        return None

    def _apply_drag_scroll(self, area_key, dy):
        """dy = how far the finger moved this step (content follows the finger,
        same convention as every touch UI: drag up reveals what's below)."""
        if area_key == "fm_list":
            max_scroll = max(0, len(self.entries) * 30 - self._fm_list_area.h)
            self.scroll = max(0, min(max_scroll, self.scroll - dy))
        elif area_key == "fm_upload_list":
            max_scroll = max(0, len(self.fm_upload_entries) * 30 - self._fm_upload_list_area.h)
            self.fm_upload_scroll = max(0, min(max_scroll, self.fm_upload_scroll - dy))
        elif area_key == "loader_list":
            max_scroll = max(0, len(self.loader_entries) * 30 - self._loader_list_area.h)
            self.loader_scroll = max(0, min(max_scroll, self.loader_scroll - dy))
        elif area_key == "loader_apps":
            max_scroll = max(0, len(self.installed_apps) * 56 - self._loader_apps_area.h)
            self.loader_apps_scroll = max(0, min(max_scroll, self.loader_apps_scroll - dy))
        elif area_key == "wp_picker_list":
            max_scroll = max(0, len(self.wallpaper_picker_entries) * 30 - self._wp_picker_list_area.h)
            self.wallpaper_picker_scroll = max(0, min(max_scroll, self.wallpaper_picker_scroll - dy))
        elif area_key == "settings":
            max_scroll = max(0, self._st_total_content_h - self._st_content_rect.h)
            self.settings_scroll = max(0, min(max_scroll, self.settings_scroll - dy))

    def _dispatch_click(self, click_pos):
        """The full click-routing chain, shared by both immediate taps
        (buttons, icons — outside any scrollable list) and deferred taps
        (a press inside a scrollable list that turned out not to be a drag)."""
        if self.power_menu_open:
            self.handle_power_menu_click(click_pos)
        elif self.handle_taskbar_click(click_pos):
            pass
        elif self.state == self.STATE_DESKTOP:
            self.handle_desktop_click(click_pos)
        elif self.state == self.STATE_FILES:
            self.handle_file_manager_click(click_pos)
        elif self.state == self.STATE_SETTINGS:
            self.handle_settings_click(click_pos)
        elif self.state == self.STATE_CALC:
            self.handle_calc_click(click_pos)
        elif self.state == self.STATE_TERMINAL:
            self.handle_terminal_click(click_pos)
        elif self.state == self.STATE_APPLOADER:
            self.handle_apploader_click(click_pos)
        elif self.state == self.STATE_WEATHER:
            self.handle_weather_click(click_pos)

    # ---------------- main loop ----------------
    def run(self):
        running = True
        while running:
            if self.pending_inprocess_launch:
                entry = self.pending_inprocess_launch
                self.pending_inprocess_launch = None
                was_terminal = (self.state == self.STATE_TERMINAL)
                if was_terminal:
                    self._set_text_input_active(False)  # hide the keyboard while the app has the screen
                ok, elapsed, reason = self._run_app_inprocess(entry["path"], entry["name"])
                if was_terminal:
                    self._set_text_input_active(True)  # show it again now that we're back
                name = entry["name"]
                if not ok:
                    self.show_toast(f"'{name}' crashed ({reason}). Check its Log for details.", ok=False)
                elif reason == "back_button":
                    self.show_toast(f"Back in StrapOS — closed '{name}'.", ok=True)
                elif elapsed < 1.0:
                    self.show_toast(
                        f"'{name}' closed after only {elapsed:.2f}s — it may be missing its own "
                        f"event loop, or something ended it early. Check its Log.", ok=False)
                else:
                    self.show_toast(f"Back in StrapOS — closed '{name}'.", ok=True)
                continue

            mouse_pos = to_virtual(pygame.mouse.get_pos())

            # Shutdown and the lock screen both take over completely — nothing
            # else on screen is interactive while either is active.
            if self.shutting_down:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                done = self.draw_shutdown_overlay()
                present()
                clock.tick(FPS)
                if done:
                    running = False
                continue

            if self.lock_screen_active:
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif not self.pin_hash and event.type in (pygame.MOUSEBUTTONDOWN, pygame.KEYDOWN):
                        self._unlock_lock_screen()  # no PIN set — any tap/key unlocks
                    elif self.pin_hash and event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        self.handle_pin_pad_click(to_virtual(event.pos))
                    elif self.pin_hash and event.type == pygame.KEYDOWN:
                        self.handle_pin_pad_key(event)
                self.draw_lock_screen()
                present()
                clock.tick(FPS)
                continue

            self._check_launch_watch()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.VIDEORESIZE:
                    if not is_fullscreen:
                        global WIN_W, WIN_H, window
                        WIN_W, WIN_H = max(event.w, 480), max(event.h, 320)
                        window = pygame.display.set_mode((WIN_W, WIN_H), pygame.RESIZABLE)
                        recompute_scale()
                elif event.type == pygame.KEYDOWN:
                    if self.booting:
                        self.booting = False  # any key skips the boot animation
                        continue
                    if event.key == pygame.K_ESCAPE:
                        if self.power_menu_open:
                            self.power_menu_open = False
                        elif self.state != self.STATE_DESKTOP:
                            self.go_home()
                        else:
                            running = False
                    elif event.key == pygame.K_F11:
                        toggle_fullscreen()
                    elif self.state == self.STATE_TERMINAL:
                        self.handle_terminal_key(event)
                    elif self.state == self.STATE_WEATHER:
                        self.handle_weather_key(event)
                elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                    if self.booting:
                        self.booting = False  # tap anywhere to skip the boot animation
                        continue
                    click_pos = to_virtual(event.pos)

                    # Starting a press on the boot-hold slider always moves the
                    # slider, never the settings page — check this first.
                    slider_hit = False
                    if (self.state == self.STATE_SETTINGS and not self.power_menu_open
                            and not (self.wallpaper_picker_open or self.reset_confirm_open
                                     or self.pin_setup_open)
                            and hasattr(self, "_st_boot_slider_rect")
                            and hasattr(self, "_st_content_rect")
                            and self._st_content_rect.collidepoint(click_pos)):
                        canvas_pos = self._settings_to_canvas_pos(click_pos)
                        if self._st_boot_slider_rect.inflate(0, 20).collidepoint(canvas_pos):
                            self.slider_dragging = True
                            self.boot_hold_ms = self._boot_ms_from_pos(canvas_pos)
                            slider_hit = True

                    if not slider_hit:
                        area_key = None if self.power_menu_open else self._get_scrollable_area_for_state(click_pos)
                        if area_key:
                            # Defer this tap — it might turn into a scroll drag.
                            self._drag_scroll = {"active": True, "area_key": area_key,
                                                  "start_pos": click_pos, "last_pos": click_pos, "moved": False}
                        else:
                            self._dispatch_click(click_pos)
                elif event.type == pygame.MOUSEWHEEL:
                    if self.state == self.STATE_FILES:
                        self.handle_file_manager_scroll(event.y)
                    elif self.state == self.STATE_APPLOADER:
                        self.handle_apploader_scroll(event.y, mouse_pos)
                    elif self.state == self.STATE_SETTINGS:
                        self.handle_settings_scroll(event.y)
                elif event.type == pygame.TEXTINPUT:
                    if self.state == self.STATE_TERMINAL:
                        self.handle_terminal_text(event.text)
                    elif self.state == self.STATE_WEATHER:
                        self.handle_weather_text(event.text)
                elif event.type == pygame.MOUSEMOTION:
                    if self.slider_dragging and self.state == self.STATE_SETTINGS:
                        canvas_pos = self._settings_to_canvas_pos(to_virtual(event.pos))
                        self.boot_hold_ms = self._boot_ms_from_pos(canvas_pos)
                    elif self._drag_scroll["active"]:
                        pos = to_virtual(event.pos)
                        last_y = self._drag_scroll["last_pos"][1]
                        self._apply_drag_scroll(self._drag_scroll["area_key"], pos[1] - last_y)
                        self._drag_scroll["last_pos"] = pos
                        start = self._drag_scroll["start_pos"]
                        if abs(pos[0] - start[0]) > self.DRAG_SCROLL_THRESHOLD \
                                or abs(pos[1] - start[1]) > self.DRAG_SCROLL_THRESHOLD:
                            self._drag_scroll["moved"] = True
                elif event.type == pygame.MOUSEBUTTONUP and event.button == 1:
                    if self.slider_dragging:
                        self.slider_dragging = False
                        self.set_boot_hold_ms(self.boot_hold_ms)  # persist once the drag ends
                    if self._drag_scroll["active"]:
                        was_tap = not self._drag_scroll["moved"]
                        tap_pos = self._drag_scroll["start_pos"]
                        self._drag_scroll["active"] = False
                        if was_tap:
                            # Never moved past the threshold — treat it as a
                            # normal tap on whatever's under it (a list row,
                            # a swatch, etc.) rather than a scroll.
                            self._dispatch_click(tap_pos)

            if self.state == self.STATE_DESKTOP:
                self.draw_desktop(mouse_pos)
            elif self.state == self.STATE_FILES:
                self.draw_file_manager(mouse_pos)
            elif self.state == self.STATE_SETTINGS:
                self.draw_settings(mouse_pos)
            elif self.state == self.STATE_CALC:
                self.draw_calculator(mouse_pos)
            elif self.state == self.STATE_TERMINAL:
                self.draw_terminal(mouse_pos)
            elif self.state == self.STATE_APPLOADER:
                self.draw_apploader(mouse_pos)
            elif self.state == self.STATE_WEATHER:
                self.draw_weather(mouse_pos)

            if self.power_menu_open:
                self.draw_power_menu()

            if self.booting:
                self.draw_boot_overlay()

            present()
            clock.tick(FPS)

        pygame.quit()
        sys.exit()


if __name__ == "__main__":
    StrapOS().run()
