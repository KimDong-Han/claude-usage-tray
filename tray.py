#!/usr/bin/env python3
"""Claude Code 사용량 표시기.

두 가지 UI를 한 프로세스에서 같이 띄운다. API 조회는 공유하므로 호출은 한 번뿐이다.
  - 떠 있는 위젯: `5H 94% 4h12m  ·  7D 90% 2d6h` — 항상 보이고 드래그로 옮긴다
  - 트레이 아이콘 2개: 원형=5시간, 사각형=주간. 숫자는 '남은 %'

우클릭 → 설정에서 색·글꼴·투명도·주기·임계값을 바꾼다. config.json에 저장된다.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
import threading
import time
import tkinter as tk
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from tkinter import colorchooser, ttk

import pystray
from PIL import Image, ImageDraw, ImageFont, ImageTk

IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WIN:
    import ctypes
    from ctypes import wintypes
if IS_MAC:
    import macos            # AppKit 글루. 윈도우의 ctypes 호출을 대신한다

# 설정창 외형용. 없으면 Tk 기본 외형으로 떨어지고 기능은 그대로 동작한다.
try:
    import sv_ttk           # Win11 Fluent(Sun Valley) ttk 테마
except ImportError:
    sv_ttk = None
try:
    import pywinstyles      # 다크 타이틀바 (윈도우 전용)
except ImportError:
    pywinstyles = None
if not IS_WIN:
    pywinstyles = None

BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS = Path.home() / ".claude" / ".credentials.json"
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CACHE_FILE = Path.home() / ".claude" / "swiftbar" / ".claude-usage-windows.json"
CONFIG_FILE = BASE_DIR / "config.json"
LEGACY_STATE = BASE_DIR / "widget-state.json"

AGENT_STATE_FILE = Path.home() / ".claude" / "usage-tray-agent-state.json"
AGENT_FLASH_S = 8       # 완료·에러를 보여줄 시간
AGENT_STALE_S = 900     # 작업중·입력대기가 이만큼 갱신 안 되면 흘려보낸다

TICK_SECONDS = 15  # 위젯 카운트다운만 다시 그리는 주기 (조회 없음)
TOPMOST_MS = 800 if IS_WIN else 2000   # 최상위 재확보 주기 — 윈도우는 트레이 플라이아웃에 밀려나므로 짧게
ICON_PX = 64

if IS_WIN:
    WINDIR = Path(os.environ.get("WINDIR", r"C:\Windows"))
    STARTUP_DIR = Path(os.environ["APPDATA"]) / "Microsoft/Windows/Start Menu/Programs/Startup"
    STARTUP_LINK = STARTUP_DIR / "claude-usage-tray.vbs"
AUTOSTART_LABEL = "로그인 시 자동 실행" if IS_MAC else "윈도우 시작 시 자동 실행"
UI_FONT = "Helvetica Neue" if IS_MAC else "Segoe UI"


# 상태 정의 — (키, 라벨, 기본 문구, 기본 동작, 기본 유지초)
#   동작 none 없음 / jump 한 번 점프 / jump_loop 계속 점프 / gear 안전모+망치 / dead 눈이 X
#   유지 0 이면 다음 상태가 올 때까지 계속
STATE_DEFS = (
    ("session_start",     "세션 시작",     "시작합니다",       "jump",      6),
    ("working",           "작업 중",       "",                "gear",      0),
    ("needs_input",       "입력 필요",     "검토부탁드립니다.",  "jump_loop", 0),
    ("permission_denied", "권한 거부",     "알겠습니다",       "none",      6),
    ("tool_failed",       "도구 실패",     "$#(@#)&",         "dead",      8),
    ("subagent",          "서브에이전트",   "도우미 투입",      "gear",      0),
    ("task_done",         "작업 하나 완료", "하나 끝",          "jump",      6),
    ("compacting",        "대화 압축",     "정리 중...",       "gear",      0),
    ("model_switch",      "모델 전환",     "갈아탔습니다",     "none",      6),
    ("done",              "전체 완료",     "모든 작업 완료",    "jump",      8),
    ("failed",            "중단 · 실패",   "$#(@#)&",         "dead",      8),
    ("idle",              "평상시",        "",                "none",      0),
)
ACTION_LABELS = {
    "none": "없음", "jump": "한 번 점프", "jump_loop": "계속 점프",
    "gear": "안전모+망치", "dead": "눈이 X",
}
DEFAULT_STATES = {
    key: {"msg": msg, "act": act, "hold": hold}
    for key, _label, msg, act, hold in STATE_DEFS
}
AGENT_STATES = tuple(DEFAULT_STATES)
GLITCH_ACTIONS = ("dead",)


# ---------------------------------------------------------------- 설정

DEFAULTS = {
    "bg": "#202024",
    "fg_label": "#8a8a93",
    "fg_time": "#8a8a93",
    "fg_ok": "#4ec97a",
    "fg_warn": "#f0a03a",
    "fg_crit": "#ff5f6b",
    "font_family": UI_FONT,
    "font_size": 10,
    "opacity": 1.0,
    "topmost": True,
    "tray_icons": True,         # 트레이(메뉴바) 아이콘 2개. 위젯만 쓰면 꺼도 된다
    "poll_minutes": 5,
    "warn_at": 20,
    "crit_at": 5,
    "pet_enabled": True,
    "pet_size": 26,
    "pet_speed": 2,
    "pet_max": 3,               # 서브에이전트가 늘어도 이 마릿수까지만 표현한다
    "pet_color_mode": "usage",   # usage = 잔량 색을 따라감, fixed = 아래 색으로 고정
    "pet_color": "#d97757",
    "pet_bubble_enabled": True,
    "pet_bubble_mode": "click",  # click = 클릭할 때만, always = 항상 표시
    "pet_bubble_messages": ["5H {5h}  ·  7D {7d}"],
    "states": DEFAULT_STATES,
    "x": None,
    "y": None,
}

CONFIG = dict(DEFAULTS)


def load_config() -> None:
    try:
        CONFIG.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        # 위젯만 있던 시절의 위치 파일에서 좌표만 넘겨받는다
        try:
            old = json.loads(LEGACY_STATE.read_text(encoding="utf-8"))
            CONFIG["x"], CONFIG["y"] = old.get("x"), old.get("y")
        except (OSError, ValueError):
            pass
    migrate_config()


def migrate_config() -> None:
    """상태가 4개 고정이던 시절의 msg_* 키를 새 states 구조로 옮긴다."""
    legacy = {"msg_needs_input": "needs_input", "msg_working": "working",
              "msg_done": "done", "msg_error": "tool_failed"}
    states = dict(CONFIG.get("states") or {})
    moved = False
    for old_key, state in legacy.items():
        if old_key in CONFIG:
            entry = dict(DEFAULT_STATES.get(state, {}))
            entry.update(states.get(state) or {})
            entry["msg"] = CONFIG.pop(old_key)
            states[state] = entry
            moved = True
    if moved:
        CONFIG["states"] = states


def save_config() -> None:
    try:
        CONFIG_FILE.write_text(json.dumps(CONFIG, indent=2), encoding="utf-8")
    except OSError:
        pass


def hex_to_rgb(value: str):
    value = value.lstrip("#")
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (0x6B, 0x6B, 0x72)


# ---------------------------------------------------------------- 데이터 수집

def _token() -> str:
    # Claude Code가 토큰을 주기적으로 갱신하므로 호출할 때마다 새로 읽는다
    try:
        with open(CREDENTIALS, encoding="utf-8") as fp:
            return json.load(fp)["claudeAiOauth"]["accessToken"]
    except FileNotFoundError:
        if not IS_MAC:
            raise
    # 맥의 Claude Code 는 파일 대신 키체인에 넣는다
    import subprocess
    out = subprocess.run(
        ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
        capture_output=True, text=True, check=True).stdout.strip()
    return json.loads(out)["claudeAiOauth"]["accessToken"]


def fetch_usage() -> dict:
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {_token()}",
            "anthropic-beta": "oauth-2025-04-20",
            "Content-Type": "application/json",
            "User-Agent": "claude-usage-tray/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(data), encoding="utf-8")
    except OSError:
        pass
    return data


def read_cache():
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- 값 다듬기

def remaining_pct(window):
    if not window:
        return None
    used = window.get("utilization")
    return None if used is None else max(0.0, 100.0 - float(used))


def _minutes_left(window):
    if not window or not window.get("resets_at"):
        return None
    when = datetime.fromisoformat(window["resets_at"])
    return int((when - datetime.now(timezone.utc)).total_seconds() // 60)


def resets_in(window) -> str:
    mins = _minutes_left(window)
    if mins is None:
        return "리셋 시각 미정"
    if mins <= 0:
        return "곧 리셋"
    days, rem = divmod(mins, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}일 {hours}시간 후 리셋"
    if hours:
        return f"{hours}시간 {mins}분 후 리셋"
    return f"{mins}분 후 리셋"


def resets_short(window) -> str:
    """위젯용 짧은 표기: 2d6h / 4h12m / 37m"""
    mins = _minutes_left(window)
    if mins is None:
        return "--"
    if mins <= 0:
        return "곧"
    days, rem = divmod(mins, 1440)
    hours, mins = divmod(rem, 60)
    if days:
        return f"{days}d{hours}h"
    if hours:
        return f"{hours}h{mins:02d}m"
    return f"{mins}m"


def scoped_lines(data) -> list:
    """모델별로 따로 걸린 주간 한도만 추려낸다."""
    out = []
    for item in (data or {}).get("limits") or []:
        model = ((item.get("scope") or {}).get("model") or {}).get("display_name")
        if model and item.get("percent") is not None:
            out.append(f"{model} {100 - int(item['percent'])}% 남음")
    return out


def _pct_text(window) -> str:
    remaining = remaining_pct(window)
    return "--" if remaining is None else f"{remaining:.0f}%"


def fill_placeholders(text: str, data) -> str:
    """말풍선 문구의 {5h} 같은 자리를 실제 값으로 바꾼다."""
    data = data or {}
    for token, value in (
        ("{5h}", _pct_text(data.get("five_hour"))),
        ("{7d}", _pct_text(data.get("seven_day"))),
        ("{5h_reset}", resets_short(data.get("five_hour"))),
        ("{7d_reset}", resets_short(data.get("seven_day"))),
    ):
        text = text.replace(token, value)
    return text


def pick_bubble_template() -> str:
    lines = [m for m in (CONFIG.get("pet_bubble_messages") or []) if m.strip()]
    return random.choice(lines or list(DEFAULTS["pet_bubble_messages"]))


def bubble_text(data) -> str:
    return fill_placeholders(pick_bubble_template(), data)


def severity_key(remaining) -> str:
    if remaining is None:
        return "fg_label"
    if remaining <= CONFIG["crit_at"]:
        return "fg_crit"
    if remaining <= CONFIG["warn_at"]:
        return "fg_warn"
    return "fg_ok"


# ---------------------------------------------------------------- 아이콘 그리기

def _font_paths():
    if IS_WIN:
        return [WINDIR / "Fonts" / n for n in ("segoeuib.ttf", "arialbd.ttf", "seguisb.ttf")]
    if IS_MAC:
        return [Path(p) for p in macos.FONT_CANDIDATES]
    return [Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]


def _font(size: int):
    for path in _font_paths():
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
    return ImageFont.load_default()


def render_icon(remaining, shape: str) -> Image.Image:
    img = Image.new("RGBA", (ICON_PX, ICON_PX), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fill = hex_to_rgb(CONFIG[severity_key(remaining)])
    box = (1, 1, ICON_PX - 2, ICON_PX - 2)
    if shape == "circle":
        draw.ellipse(box, fill=fill)
    else:
        draw.rounded_rectangle(box, radius=13, fill=fill)

    text = "?" if remaining is None else str(int(round(remaining)))
    size = 56
    font = _font(size)
    while size > 10:
        font = _font(size)
        bbox = draw.textbbox((0, 0), text, font=font)
        if bbox[2] - bbox[0] <= ICON_PX * 0.84 and bbox[3] - bbox[1] <= ICON_PX * 0.66:
            break
        size -= 2
    bbox = draw.textbbox((0, 0), text, font=font)
    draw.text(
        (
            (ICON_PX - (bbox[2] - bbox[0])) / 2 - bbox[0],
            (ICON_PX - (bbox[3] - bbox[1])) / 2 - bbox[1],
        ),
        text,
        font=font,
        fill=(255, 255, 255, 255),
    )
    return img


# ---------------------------------------------------------------- 자동 시작

def autostart_enabled() -> bool:
    if IS_MAC:
        return macos.autostart_enabled()
    if not IS_WIN:
        return False
    return STARTUP_LINK.exists()


def toggle_autostart(*_args) -> None:
    if IS_MAC:
        macos.autostart_set(not macos.autostart_enabled(), Path(__file__).resolve())
        return
    if not IS_WIN:
        return
    if autostart_enabled():
        STARTUP_LINK.unlink(missing_ok=True)
        return
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    runner = pythonw if pythonw.exists() else Path(sys.executable)
    script = Path(__file__).resolve()
    STARTUP_DIR.mkdir(parents=True, exist_ok=True)
    # wscript가 이 파일을 ANSI로 읽으므로 내용은 ASCII만 쓴다
    STARTUP_LINK.write_text(
        'Dim sh, q\n'
        'Set sh = CreateObject("WScript.Shell")\n'
        'q = Chr(34)\n'
        f'sh.Run q & "{runner}" & q & " " & q & "{script}" & q, 0, False\n',
        encoding="utf-8",
    )


# ---------------------------------------------------------------- 화면 좌표

def window_hwnd(window) -> int:
    """Tk 창의 실제 최상위 HWND. overrideredirect 창도 이걸로 잡힌다."""
    if not IS_WIN:
        return 0
    try:
        user32 = ctypes.windll.user32
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
        return user32.GetAncestor(wintypes.HWND(window.winfo_id()), 2)  # GA_ROOT
    except Exception:
        return 0


def hide_from_taskbar(window) -> None:
    """작업표시줄 버튼과 Alt+Tab 목록에서 창을 뺀다 (WS_EX_TOOLWINDOW).

    Tk의 -toolwindow가 이 스타일을 붙여준다. 단 Tk가 이때 OS 창을 새로
    만들어 HWND가 바뀌므로, HWND를 쓰는 작업(다크 타이틀바·z-order)은
    반드시 이 호출 '뒤에' 해야 한다.

    맥은 창이 아니라 앱 단위로 Dock 에 잡히므로 여기선 할 일이 없다.
    App 이 시작할 때 macos.hide_from_dock() 을 한 번 부른다.
    """
    if not IS_WIN:
        return
    try:
        window.attributes("-toolwindow", True)
        window.update_idletasks()
        return
    except tk.TclError:
        pass

    hwnd = window_hwnd(window)  # 폴백: 확장 스타일을 직접 설정
    if not hwnd:
        return
    user32 = ctypes.windll.user32
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
    get_long.restype = set_long.restype = ctypes.c_longlong
    get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_longlong]
    GWL_EXSTYLE, WS_EX_TOOLWINDOW, WS_EX_APPWINDOW = -20, 0x00000080, 0x00040000
    try:
        style = (get_long(hwnd, GWL_EXSTYLE) | WS_EX_TOOLWINDOW) & ~WS_EX_APPWINDOW
        user32.ShowWindow(hwnd, 0)  # SW_HIDE
        set_long(hwnd, GWL_EXSTYLE, style)
        user32.ShowWindow(hwnd, 5)  # SW_SHOW
    except Exception:
        pass


def work_area(window) -> tuple[int, int, int, int]:
    """작업 영역 (left, top, right, bottom). 윈도우는 작업표시줄, 맥은 메뉴바·Dock 을 뺀 것."""
    sw, sh = window.winfo_screenwidth(), window.winfo_screenheight()
    if IS_WIN:
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        rect = RECT()
        ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0)
        return rect.left, rect.top, rect.right, rect.bottom
    if IS_MAC:
        area = macos.work_area()
        if area:
            return area
    return 0, 0, sw, sh


def make_floating(window, on: bool = True) -> None:
    """항상 위. 맥의 Tk 9 는 테두리 없는 창에 -topmost 가 안 먹어 NSWindow 레벨을 직접 올린다."""
    try:
        window.attributes("-topmost", bool(on))
    except tk.TclError:
        pass
    if IS_MAC:
        macos.set_floating(window, on)


# ---------------------------------------------------------------- 설정창

UI_BG = "#1c1c1c"
UI_FG = "#fafafa"
UI_DIM = "#9a9a9a"
UI_ACCENT = "#57c8ff"
UI_LINE = "#3a3a3a"

COLOR_FIELDS = (
    ("bg", "배경"),
    ("fg_label", "라벨 (5H / 7D)"),
    ("fg_time", "남은 시간"),
    ("fg_ok", "여유"),
    ("fg_warn", "주의"),
    ("fg_crit", "경고"),
)

BUBBLE_SLOTS = 4   # 말풍선 문구 입력 칸 수

STATE_FIELDS = (
    ("msg_needs_input", "입력 필요"),
    ("msg_working", "작업 중"),
    ("msg_done", "완료"),
    ("msg_error", "에러"),
)

if IS_MAC:
    FONT_CHOICES = ("Helvetica Neue", "SF Pro Text", "Apple SD Gothic Neo", "Menlo", "SF Mono")
else:
    FONT_CHOICES = ("Segoe UI", "Segoe UI Semibold", "맑은 고딕", "Consolas", "Cascadia Mono")

PRESETS = {
    # Anthropic 클레이 톤. 평상시 몸 색이 클레이라 캐릭터가 클로드 색을 띤다.
    # ok(클레이)와 crit(빨강)이 둘 다 따뜻한 계열이라 채도 차이로 구분한다.
    "클레이": dict(bg="#26201e", fg_label="#a1897d", fg_time="#8b7469",
                 fg_ok="#d97757", fg_warn="#e8b34a", fg_crit="#ef4444"),
    "다크": dict(bg="#202024", fg_label="#8a8a93", fg_time="#8a8a93",
               fg_ok="#4ec97a", fg_warn="#f0a03a", fg_crit="#ff5f6b"),
    "라이트": dict(bg="#f2f2f4", fg_label="#5b5b64", fg_time="#5b5b64",
                fg_ok="#1a7f43", fg_warn="#a86a00", fg_crit="#c62834"),
    "네온": dict(bg="#0c0c12", fg_label="#5b6272", fg_time="#5eead4",
               fg_ok="#39ff9e", fg_warn="#ffd23f", fg_crit="#ff2e63"),
    "모노": dict(bg="#000000", fg_label="#6e6e73", fg_time="#6e6e73",
               fg_ok="#ffffff", fg_warn="#d1d1d6", fg_crit="#ff453a"),
}


def apply_ui_theme(window) -> None:
    """Win11 Fluent(Sun Valley) 다크 테마 + 다크 타이틀바.

    두 패키지 모두 없어도 동작한다. 그 경우 Tk 기본 외형으로 떨어진다.
    """
    if sv_ttk is not None:
        try:
            sv_ttk.set_theme("dark")
        except Exception:
            pass
    if pywinstyles is not None:
        try:
            pywinstyles.change_header_color(window, UI_BG)
            pywinstyles.change_title_color(window, UI_FG)
            pywinstyles.change_border_color(window, UI_LINE)
        except Exception:
            pass


def swatch_image(color: str, width: int = 46, height: int = 24) -> ImageTk.PhotoImage:
    """색상 칩 이미지. ttk.Label 에 얹어야 노트북 탭 안에서도 확실히 그려진다."""
    scale = 2  # 2배로 그린 뒤 줄여서 모서리를 매끄럽게
    img = Image.new("RGBA", (width * scale, height * scale), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle(
        [1, 1, width * scale - 2, height * scale - 2], radius=7 * scale,
        fill=color, outline="#5a5a66", width=2)
    return ImageTk.PhotoImage(img.resize((width, height), Image.LANCZOS))


def raise_widget(widget) -> None:
    """위젯을 형제들 위로 올린다.

    Canvas·Text 는 lift 와 tkraise 를 모두 tag_raise 로 덮어써서 인자를 요구한다.
    Misc 쪽을 직접 불러야 한다. 실패해도 창 구성이 깨지지 않게 감싼다.
    """
    try:
        tk.Misc.tkraise(widget)
    except tk.TclError:
        pass


def round_rect(canvas, x1, y1, x2, y2, radius, **kwargs):
    if hasattr(canvas, "round_rect"):          # 맥의 PIL 캔버스는 둥근 사각형을 직접 그린다
        return canvas.round_rect(x1, y1, x2, y2, radius, **kwargs)
    points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
              x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
              x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
    return canvas.create_polygon(points, smooth=True, **kwargs)


class SettingsWindow:
    """색·글꼴·주기를 고르는 창. 고르는 즉시 위젯과 트레이 아이콘에 반영된다."""

    # 맥은 ttk 위젯이 더 커서 상태 탭 12줄이 안 들어간다. 여유 있게 잡는다
    TAB_W, TAB_H = (560, 500) if IS_MAC else (466, 404)

    def __init__(self, app: "App") -> None:
        self.app = app
        self.snapshot = dict(CONFIG)  # 취소하면 여기로 되돌린다
        self.swatches = {}
        self.vars = {}
        self.readouts = {}

        self.win = tk.Toplevel(app.widget.tk_root)
        self.win.title("Claude 사용량 표시기")
        self.win.configure(bg=UI_BG)
        self.win.resizable(False, False)
        self.win.protocol("WM_DELETE_WINDOW", self.cancel)
        self.win.update_idletasks()
        hide_from_taskbar(self.win)  # 설정창이 작업표시줄에 잡히지 않게
        make_floating(self.win)
        apply_ui_theme(self.win)
        self.app.widget.apply_theme()  # 테마 적용이 오버레이를 건드렸을 경우 되돌린다

        outer = ttk.Frame(self.win, padding=16)
        outer.pack(fill="both", expand=True)

        self._build_preview(outer)
        self._build_presets(outer)

        book = ttk.Notebook(outer)
        book.pack(fill="both", expand=True, pady=(14, 0))
        for title, build in (("색상", self._tab_colors),
                             ("표시", self._tab_display),
                             ("동작", self._tab_behavior),
                             ("캐릭터", self._tab_pet),
                             ("말풍선", self._tab_bubble),
                             ("상태", self._tab_states)):
            tab = ttk.Frame(book, padding=14, width=self.TAB_W, height=self.TAB_H)
            tab.grid_propagate(False)  # 탭을 옮겨도 창 크기가 튀지 않게 고정
            book.add(tab, text=f"  {title}  ")
            build(tab)

        self._build_buttons(outer)
        self._center()
        self._activate()

    # -- 조각 -------------------------------------------------------
    def _build_preview(self, parent) -> None:
        ttk.Label(parent, text="미리보기", foreground=UI_DIM).pack(anchor="w")
        border = tk.Frame(parent, bg=UI_LINE, padx=1, pady=1)
        border.pack(fill="x", pady=(6, 0))
        self.preview = tk.Frame(border, bg=CONFIG["bg"])
        self.preview.pack(fill="both")
        raise_widget(border)
        raise_widget(self.preview)
        self._render_preview()

    def _render_preview(self) -> None:
        """실제 위젯과 같은 규칙으로 그려서 지금 설정이 어떻게 보이는지 보여준다."""
        for child in self.preview.winfo_children():
            child.destroy()
        bg = CONFIG["bg"]
        self.preview.configure(bg=bg, padx=12, pady=8)
        base = (CONFIG["font_family"], CONFIG["font_size"] - 1)
        bold = (CONFIG["font_family"], CONFIG["font_size"], "bold")
        data = self.app.data or {}
        for idx, (key, _shape, _short, _label, tag) in enumerate(WINDOWS):
            if idx:
                tk.Label(self.preview, text="·", bg=bg, fg=CONFIG["fg_label"],
                         font=base).pack(side="left", padx=7)
            window = data.get(key)
            remaining = remaining_pct(window)
            tk.Label(self.preview, text=tag, bg=bg, fg=CONFIG["fg_label"],
                     font=base).pack(side="left")
            tk.Label(self.preview, bg=bg, font=bold,
                     text="--" if remaining is None else f"{remaining:.0f}%",
                     fg=CONFIG[severity_key(remaining)]).pack(side="left", padx=4)
            tk.Label(self.preview, text=resets_short(window), bg=bg,
                     fg=CONFIG["fg_time"], font=base).pack(side="left")

    def _build_presets(self, parent) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(14, 0))
        ttk.Label(row, text="프리셋", foreground=UI_DIM).pack(side="left", padx=(0, 10))
        for name in PRESETS:
            ttk.Button(row, text=name, width=5,
                       command=lambda n=name: self.apply_preset(n)).pack(side="left", padx=2)

    def _tab_colors(self, tab) -> None:
        for row, (key, label) in enumerate(COLOR_FIELDS):
            self._color_row(tab, row, key, label)

    def _color_row(self, tab, row: int, key: str, label: str, on_pick=None) -> None:
        ttk.Label(tab, text=label, width=14).grid(row=row, column=0, sticky="w", pady=5)
        chip = ttk.Label(tab, cursor="hand2")
        chip.grid(row=row, column=1, padx=(0, 12))
        chip.bind("<Button-1>", lambda _e, k=key: (self.pick_color(k), on_pick and on_pick()))
        var = tk.StringVar(value=CONFIG[key])
        entry = ttk.Entry(tab, textvariable=var, width=11)
        entry.grid(row=row, column=2)
        entry.bind("<Return>", lambda _e, k=key: self.set_color(k, self.swatches[k][1].get()))
        entry.bind("<FocusOut>", lambda _e, k=key: self.set_color(k, self.swatches[k][1].get()))
        self.swatches[key] = (chip, var)
        self._paint_chip(key)

    def _paint_chip(self, key: str) -> None:
        chip = self.swatches[key][0]
        image = swatch_image(CONFIG[key])
        chip.configure(image=image)
        chip.image = image  # 참조를 붙들어두지 않으면 GC 되어 사라진다

    def _tab_display(self, tab) -> None:
        ttk.Label(tab, text="글꼴", width=14).grid(row=0, column=0, sticky="w", pady=8)
        family = tk.StringVar(value=CONFIG["font_family"])
        box = ttk.Combobox(tab, textvariable=family, values=FONT_CHOICES,
                           width=19, state="readonly")
        box.grid(row=0, column=1, columnspan=2, sticky="w")
        box.bind("<<ComboboxSelected>>", lambda _e: self.set_value("font_family", family.get()))
        self.vars["font_family"] = family

        self._slider(tab, 1, "font_size", "글자 크기", 7, 20, "{:.0f} pt", int)
        self._slider(tab, 2, "opacity", "투명도", 0.3, 1.0, "{:.0%}", float)

        topmost = tk.BooleanVar(value=CONFIG["topmost"])
        ttk.Checkbutton(tab, text="항상 맨 위에 표시", style="Switch.TCheckbutton",
                        variable=topmost,
                        command=lambda: self.set_value("topmost", topmost.get())
                        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(14, 0))
        self.vars["topmost"] = topmost

        tray_icons = tk.BooleanVar(value=CONFIG["tray_icons"])
        ttk.Checkbutton(tab, text="메뉴바 아이콘 표시" if IS_MAC else "트레이 아이콘 표시",
                        style="Switch.TCheckbutton", variable=tray_icons,
                        command=lambda: self.set_value("tray_icons", tray_icons.get())
                        ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.vars["tray_icons"] = tray_icons

    def _tab_behavior(self, tab) -> None:
        self._slider(tab, 0, "poll_minutes", "조회 주기", 1, 60, "{:.0f}분", int)
        self._slider(tab, 1, "warn_at", "주의 임계값", 1, 99, "{:.0f}%", int)
        self._slider(tab, 2, "crit_at", "경고 임계값", 1, 99, "{:.0f}%", int)
        # 자동 실행은 CONFIG 가 아니라 시작프로그램 폴더의 파일 존재 여부가 진실이다
        auto = tk.BooleanVar(value=autostart_enabled())
        ttk.Checkbutton(tab, text="윈도우 시작 시 자동 실행", style="Switch.TCheckbutton",
                        variable=auto,
                        command=lambda: (toggle_autostart(), auto.set(autostart_enabled()))
                        ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(14, 0))

        ttk.Label(tab, foreground=UI_DIM, wraplength=430, justify="left",
                  text="남은 비율이 임계값 아래로 내려가면 색이 바뀌고 알림이 한 번 뜹니다. "
                       "조회 주기 변경은 다음 조회부터 적용됩니다."
                  ).grid(row=4, column=0, columnspan=3, sticky="w", pady=(14, 0))

    def _tab_pet(self, tab) -> None:
        enabled = tk.BooleanVar(value=CONFIG["pet_enabled"])
        ttk.Checkbutton(tab, text="작업표시줄에 캐릭터 표시", style="Switch.TCheckbutton",
                        variable=enabled,
                        command=lambda: self.set_value("pet_enabled", enabled.get())
                        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 12))
        self.vars["pet_enabled"] = enabled
        self._slider(tab, 1, "pet_size", "크기", 14, 48, "{:.0f} px", int)
        self._slider(tab, 2, "pet_speed", "걷는 속도", 1, 6, "{:.0f}", int)
        self._slider(tab, 3, "pet_max", "최대 마릿수", 1, 6, "{:.0f} 마리", int)

        mode = tk.StringVar(value=CONFIG["pet_color_mode"])
        picker = ttk.Frame(tab)
        picker.grid(row=4, column=0, columnspan=3, sticky="w", pady=(10, 2))
        ttk.Label(picker, text="몸 색", width=14).pack(side="left")
        for value, text in (("usage", "잔량 따라"), ("fixed", "직접 지정")):
            ttk.Radiobutton(picker, text=text, value=value, variable=mode,
                            command=lambda: self.set_value("pet_color_mode", mode.get())
                            ).pack(side="left", padx=(0, 12))
        self.vars["pet_color_mode"] = mode

        # 색을 직접 고르면 고정 모드가 의도이므로 같이 넘어간다
        self._color_row(tab, 5, "pet_color", "지정 색",
                        on_pick=lambda: (mode.set("fixed"),
                                         self.set_value("pet_color_mode", "fixed")))

        ttk.Label(tab, foreground=UI_DIM, wraplength=430, justify="left",
                  text="'잔량 따라'는 5시간 한도의 남은 비율에 맞춰 색이 바뀝니다. "
                       "클릭하면 폴짝 뛰면서 남은 사용량을 말풍선으로 보여줍니다. "
                       "서브에이전트가 돌면 그 수만큼 늘어나고, 최대 마릿수에서 멈춥니다."
                  ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(12, 0))

    def _tab_bubble(self, tab) -> None:
        on = tk.BooleanVar(value=CONFIG["pet_bubble_enabled"])
        ttk.Checkbutton(tab, text="말풍선 사용", style="Switch.TCheckbutton",
                        variable=on,
                        command=lambda: self.set_value("pet_bubble_enabled", on.get())
                        ).grid(row=0, column=0, sticky="w", pady=(0, 6))
        self.vars["pet_bubble_enabled"] = on

        mode = tk.StringVar(value=CONFIG["pet_bubble_mode"])
        modes = ttk.Frame(tab)
        modes.grid(row=1, column=0, sticky="w", pady=(0, 8))
        for value, text in (("click", "클릭할 때만"), ("always", "항상 표시")):
            ttk.Radiobutton(modes, text=text, value=value, variable=mode,
                            command=lambda: self.set_value("pet_bubble_mode", mode.get())
                            ).pack(side="left", padx=(0, 14))
        self.vars["pet_bubble_mode"] = mode

        ttk.Label(tab, text="문구 (한 줄에 하나, 여러 개면 무작위로 하나)",
                  foreground=UI_DIM).grid(row=2, column=0, sticky="w", pady=(0, 4))

        # tk.Text 는 ttk.Notebook 탭 안에서 그려지지 않는 환경이 있어 ttk.Entry 로 대체한다
        self.bubble_entries = []
        for index in range(BUBBLE_SLOTS):
            var = tk.StringVar()
            entry = ttk.Entry(tab, textvariable=var, width=44)
            entry.grid(row=3 + index, column=0, sticky="w", pady=2)
            var.trace_add("write", lambda *_a: self._read_bubble_box())
            self.bubble_entries.append(var)
        self._fill_bubble_box()

        ttk.Label(tab, foreground=UI_DIM, wraplength=430, justify="left",
                  text="치환자: {5h} {7d} 는 남은 %, {5h_reset} {7d_reset} 은 리셋까지 남은 시간. "
                       "'항상 표시'는 값이 계속 갱신되고, 문구가 여럿이면 30초마다 바뀝니다."
                  ).grid(row=3 + BUBBLE_SLOTS, column=0, sticky="w", pady=(8, 0))

    def _tab_states(self, tab) -> None:
        ttk.Label(tab, foreground=UI_DIM, wraplength=430, justify="left",
                  text="Claude Code 훅이 알려주는 상태마다 문구·동작·유지시간을 정합니다. "
                       "문구를 비우면 말풍선 없이 동작만, 유지 0 은 다음 상태까지 계속입니다."
                  ).grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 8))
        for column, title in enumerate(("", "문구", "동작", "유지")):
            ttk.Label(tab, text=title, foreground=UI_DIM).grid(
                row=1, column=column, sticky="w", padx=(0, 6))

        self.state_vars = {}
        actions = list(ACTION_LABELS.values())
        for row, (key, label, _msg, _act, _hold) in enumerate(STATE_DEFS, start=2):
            conf = state_conf(key)
            ttk.Label(tab, text=label, width=12).grid(row=row, column=0, sticky="w", pady=2)

            msg = tk.StringVar(value=conf["msg"])
            ttk.Entry(tab, textvariable=msg, width=20).grid(row=row, column=1, padx=(0, 6))
            msg.trace_add("write", lambda *_a, k=key, v=msg: self.set_state(k, "msg", v.get()))

            act = tk.StringVar(value=ACTION_LABELS.get(conf["act"], "없음"))
            box = ttk.Combobox(tab, textvariable=act, values=actions, width=11, state="readonly")
            box.grid(row=row, column=2, padx=(0, 6))
            box.bind("<<ComboboxSelected>>",
                     lambda _e, k=key, v=act: self.set_state(k, "act", action_key(v.get())))

            hold = tk.IntVar(value=int(conf["hold"]))
            ttk.Spinbox(tab, from_=0, to=120, width=4, textvariable=hold,
                        command=lambda k=key, v=hold: self.set_state(k, "hold", v.get())
                        ).grid(row=row, column=3)
            self.state_vars[key] = (msg, act, hold)

    def set_state(self, key: str, field: str, value) -> None:
        states = dict(CONFIG.get("states") or {})
        entry = dict(state_conf(key))
        entry[field] = value
        states[key] = entry
        CONFIG["states"] = states
        self.app.refresh_pet_look()

    def _fill_bubble_box(self) -> None:
        messages = list(CONFIG.get("pet_bubble_messages") or [])
        for index, var in enumerate(self.bubble_entries):
            var.set(messages[index] if index < len(messages) else "")

    def _read_bubble_box(self) -> None:
        lines = [var.get().strip() for var in self.bubble_entries if var.get().strip()]
        CONFIG["pet_bubble_messages"] = lines   # 타이핑 중 위젯을 다시 그리지 않는다
        self.app.refresh_pet_look()

    def _slider(self, tab, row: int, key: str, label: str, lo, hi, fmt: str, cast) -> None:
        ttk.Label(tab, text=label, width=14).grid(row=row, column=0, sticky="w", pady=8)
        var = tk.DoubleVar(value=float(CONFIG[key]))
        readout = ttk.Label(tab, text=fmt.format(CONFIG[key]), width=6, foreground=UI_ACCENT)

        def on_move(_value=None):
            current = int(round(var.get())) if cast is int else round(var.get(), 2)
            readout.configure(text=fmt.format(current))
            self.set_value(key, current)

        ttk.Scale(tab, from_=lo, to=hi, variable=var, length=180, command=on_move
                  ).grid(row=row, column=1, sticky="w", padx=(0, 12))
        readout.grid(row=row, column=2, sticky="w")
        self.vars[key] = var
        self.readouts[key] = (readout, fmt)

    def _build_buttons(self, parent) -> None:
        row = ttk.Frame(parent)
        row.pack(fill="x", pady=(16, 0))
        ttk.Button(row, text="기본값 복원", command=self.restore_defaults).pack(side="left")
        ttk.Button(row, text="저장", style="Accent.TButton",
                   command=self.save).pack(side="right")
        ttk.Button(row, text="취소", command=self.cancel).pack(side="right", padx=(0, 8))

    def _activate(self) -> None:
        """OS·Tk 양쪽에서 이 창을 앞으로 가져와 키 입력을 받게 한다."""
        self.win.deiconify()
        self.win.lift()
        if IS_MAC:
            macos.activate_app()   # Dock 없는 앱은 스스로 활성화해야 키 입력을 받는다
            make_floating(self.win)
        hwnd = window_hwnd(self.win)
        if hwnd:
            try:
                ctypes.windll.user32.SetForegroundWindow(wintypes.HWND(hwnd))
            except Exception:
                pass
        self.win.focus_force()

    def _center(self) -> None:
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        x = (self.win.winfo_screenwidth() - w) // 2
        y = (self.win.winfo_screenheight() - h) // 3
        self.win.geometry(f"{w}x{h}+{x}+{y}")

    # -- 값 변경 ----------------------------------------------------
    def set_value(self, key: str, value) -> None:
        CONFIG[key] = value
        if key.startswith("msg_"):   # 타이핑 중 위젯·미리보기까지 다시 그리지 않는다
            self.app.refresh_pet_look()
            return
        self.app.widget.apply_theme()
        self._render_preview()
        if key in ("warn_at", "crit_at"):
            self.app.repaint_icons()
        if key == "tray_icons":
            self.app.apply_tray_visibility()
        if key in ("pet_enabled", "pet_size", "pet_max"):
            self.app.rebuild_pet()
        elif key.startswith("pet_"):
            self.app.refresh_pet_look()

    def set_color(self, key: str, value: str) -> None:
        value = (value or "").strip()
        if not (value.startswith("#") and len(value) == 7):
            return
        try:
            int(value[1:], 16)
        except ValueError:
            return
        CONFIG[key] = value
        self._paint_chip(key)
        self.app.widget.apply_theme()
        self.app.repaint_icons()
        self.app.refresh_pet_look()
        self._render_preview()

    def pick_color(self, key: str) -> None:
        chosen = colorchooser.askcolor(color=CONFIG[key], parent=self.win)[1]
        if chosen:
            self.swatches[key][1].set(chosen)
            self.set_color(key, chosen)

    def apply_preset(self, name: str) -> None:
        CONFIG.update(PRESETS[name])
        self._sync_inputs()
        self.app.widget.apply_theme()
        self.app.repaint_icons()
        self._render_preview()

    def restore_defaults(self) -> None:
        keep = {"x": CONFIG["x"], "y": CONFIG["y"]}
        CONFIG.update(DEFAULTS)
        CONFIG.update(keep)
        self._sync_inputs()
        self.app.widget.apply_theme()
        self.app.repaint_icons()
        self._render_preview()

    def _sync_inputs(self) -> None:
        for key, (_chip, var) in self.swatches.items():
            var.set(CONFIG[key])
            self._paint_chip(key)
        for key, var in self.vars.items():
            var.set(CONFIG[key])
        for key, (readout, fmt) in self.readouts.items():
            readout.configure(text=fmt.format(CONFIG[key]))
        if getattr(self, "bubble_entries", None):
            self._fill_bubble_box()
        for key, (msg, act, hold) in getattr(self, "state_vars", {}).items():
            conf = state_conf(key)
            msg.set(conf["msg"])
            act.set(ACTION_LABELS.get(conf["act"], "없음"))
            hold.set(int(conf["hold"]))

    # -- 버튼 -------------------------------------------------------
    def save(self) -> None:
        save_config()
        self.app.settings = None
        self.win.destroy()

    def cancel(self) -> None:
        CONFIG.update(self.snapshot)
        self.app.widget.apply_theme()
        self.app.repaint_icons()
        self.app.settings = None
        self.win.destroy()


# ---------------------------------------------------------------- 위젯

WINDOWS = (
    ("five_hour", "circle", "5시간 한도", "5시간 세션 한도", "5H"),
    ("seven_day", "square", "주간 한도", "주간(7일) 한도", "7D"),
)


class Widget:
    """테두리 없는 최상위 창. 작업표시줄 위에 얹혀 항상 보인다."""

    def __init__(self, app: "App") -> None:
        self.app = app
        self.tk_root = app.tk_root
        self.root = tk.Toplevel(self.tk_root)
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.title("claude-usage-widget")   # 맥에서 NSWindow 를 찾는 열쇠
        self.root.configure(bg=CONFIG["bg"])

        self.frame = tk.Frame(self.root, bg=CONFIG["bg"], padx=10, pady=3)
        self.frame.pack()

        self.parts = {}
        self.seps = []
        for idx, (key, _shape, _short, _label, tag) in enumerate(WINDOWS):
            if idx:
                sep = tk.Label(self.frame, text="·", bg=CONFIG["bg"])
                sep.pack(side="left", padx=7)
                self.seps.append(sep)
            name = tk.Label(self.frame, text=tag, bg=CONFIG["bg"])
            name.pack(side="left")
            pct = tk.Label(self.frame, bg=CONFIG["bg"], text="--")
            pct.pack(side="left", padx=(4, 4))
            left = tk.Label(self.frame, bg=CONFIG["bg"], text="--")
            left.pack(side="left")
            self.parts[key] = (name, pct, left)

        self.menu = tk.Menu(self.root, tearoff=0)
        self.menu.add_command(label="지금 새로고침",
                              command=lambda: self.app.refresh_async(notify=False))
        self.menu.add_command(label="설정...", command=self.app.open_settings)
        self.pet_var = tk.BooleanVar(value=CONFIG["pet_enabled"])
        self.autostart_var = tk.BooleanVar(value=autostart_enabled())
        self.menu.add_checkbutton(label="캐릭터 표시", variable=self.pet_var,
                                  command=self.app.toggle_pet)
        self.tray_var = tk.BooleanVar(value=CONFIG["tray_icons"])
        self.menu.add_checkbutton(label="메뉴바 아이콘 표시" if IS_MAC else "트레이 아이콘 표시",
                                  variable=self.tray_var, command=self.app.toggle_tray)
        self.menu.add_checkbutton(label=AUTOSTART_LABEL,
                                  variable=self.autostart_var, command=toggle_autostart)
        self.menu.add_command(label="위치 초기화", command=self.reset_position)
        self.menu.add_separator()
        self.menu.add_command(label="종료", command=self.app.quit)

        self.apply_theme()
        hide_from_taskbar(self.root)  # 창이 재생성되므로 배치보다 먼저
        self._place((CONFIG["x"], CONFIG["y"]) if CONFIG["x"] is not None else None)
        self.root.deiconify()
        self.root.update_idletasks()
        make_floating(self.root, bool(CONFIG["topmost"]))

    # -- 테마 -------------------------------------------------------
    def apply_theme(self) -> None:
        bg = CONFIG["bg"]
        base = (CONFIG["font_family"], CONFIG["font_size"] - 1)
        bold = (CONFIG["font_family"], CONFIG["font_size"], "bold")
        self.root.configure(bg=bg)
        self.frame.configure(bg=bg)
        for sep in self.seps:
            sep.configure(bg=bg, fg=CONFIG["fg_label"], font=base)
        for name, pct, left in self.parts.values():
            name.configure(bg=bg, fg=CONFIG["fg_label"], font=base)
            pct.configure(bg=bg, font=bold)
            left.configure(bg=bg, fg=CONFIG["fg_time"], font=base)
        make_floating(self.root, bool(CONFIG["topmost"]))
        self.root.attributes("-alpha", float(CONFIG["opacity"]))
        self._bind_all()
        self.redraw()

    def _bind_all(self) -> None:
        for target in (self.root, self.frame, *self.frame.winfo_children()):
            target.bind("<Button-1>", self._drag_start)
            target.bind("<B1-Motion>", self._drag_move)
            target.bind("<ButtonRelease-1>", self._drag_end)
            bind_context_menu(target, self._popup)

    # -- 위치 -------------------------------------------------------
    def _place(self, pos) -> None:
        self.root.update_idletasks()
        w = self.root.winfo_reqwidth()
        h = self.root.winfo_reqheight()
        if pos is None:
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            _left, _top, right, bottom = work_area(self.root)
            if IS_WIN:
                tb = sh - bottom
                # 시계·트레이·IME 자리를 비워두고 작업표시줄 세로 중앙에 얹는다
                x = max(0, sw - w - 350)
                y = bottom + (tb - h) // 2 if tb > h else sh - h - 8
            else:
                # 맥은 작업표시줄이 없다. Dock 바로 위 오른쪽 구석에 둔다
                x = max(0, right - w - 20)
                y = max(0, bottom - h - 12)
            pos = (x, y)
        self.root.geometry(f"{w}x{h}+{int(pos[0])}+{int(pos[1])}")

    def reset_position(self) -> None:
        CONFIG["x"] = CONFIG["y"] = None
        save_config()
        self._place(None)

    def _drag_start(self, event) -> None:
        self._grab = (event.x_root - self.root.winfo_x(),
                      event.y_root - self.root.winfo_y())

    def _drag_move(self, event) -> None:
        if not getattr(self, "_grab", None):
            return
        self.root.geometry(f"+{event.x_root - self._grab[0]}+{event.y_root - self._grab[1]}")

    def _drag_end(self, _event) -> None:
        self._grab = None
        CONFIG["x"], CONFIG["y"] = self.root.winfo_x(), self.root.winfo_y()
        save_config()

    def _popup(self, event) -> None:
        # 체크 표시를 실제 상태에 맞춘다 (설정창에서 바꿨을 수도 있다)
        self.pet_var.set(CONFIG["pet_enabled"])
        self.tray_var.set(CONFIG["tray_icons"])
        self.autostart_var.set(autostart_enabled())
        try:
            self.menu.tk_popup(event.x_root, event.y_root)
        finally:
            self.menu.grab_release()

    # -- 갱신 -------------------------------------------------------
    def redraw(self) -> None:
        data = self.app.data or {}
        for key, _shape, _short, _label, _tag in WINDOWS:
            window = data.get(key)
            remaining = remaining_pct(window)
            _name, pct, left = self.parts[key]
            pct.configure(
                text="--" if remaining is None else f"{remaining:.0f}%",
                fg=CONFIG[severity_key(remaining)],
            )
            left.configure(text=resets_short(window) if remaining is not None else "")
        self.root.update_idletasks()
        self.root.geometry(f"{self.root.winfo_reqwidth()}x{self.root.winfo_reqheight()}")

    def tick(self) -> None:
        """조회 없이 카운트다운만 다시 그린다."""
        self.redraw()
        self.root.after(TICK_SECONDS * 1000, self.tick)

    # -- z-order ----------------------------------------------------
    def keep_on_top(self) -> None:
        """트레이 플라이아웃·작업표시줄이 올라오면 그 위로 다시 올린다.

        Tk의 -topmost는 한 번 걸어두면 끝이라, 셸이 작업표시줄 체인을 올릴 때
        밀려난다. SetWindowPos로 주기적으로 재확보해야 계속 보인다.
        """
        if CONFIG["topmost"]:
            hwnd = window_hwnd(self.root)
            if hwnd:
                # HWND_TOPMOST(-1), SWP_NOSIZE|SWP_NOMOVE|SWP_NOACTIVATE
                ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0013)
            elif IS_MAC:
                # 레벨은 한 번 올리면 유지되지만 Tk 가 창을 다시 만드는 경우가 있어 재확인한다
                macos.set_floating(self.root, True)   # 캐릭터 창은 네이티브라 레벨이 유지된다
        self.root.after(TOPMOST_MS, self.keep_on_top)


# ---------------------------------------------------------------- 캐릭터

PET_KEY = "#ff00fe"  # 투명 처리용 키 컬러 — 스프라이트엔 쓰지 않는 색


def pet_window_bg(window) -> str:
    """캐릭터 창을 투명하게 만들고 그 배경색을 돌려준다.

    윈도우는 -transparentcolor 키 컬러. 안 되면 위젯 배경색으로 칠해서 위젯 위에
    서 있는 것처럼 보이게 한다. (맥은 Tk 창을 안 쓴다 — macos.PetWindow 참고)
    """
    try:
        window.attributes("-transparentcolor", PET_KEY)
        return PET_KEY
    except tk.TclError:
        return CONFIG["bg"]


def bind_context_menu(widget, handler) -> None:
    """우클릭. 맥은 Tk 버전에 따라 Button-2 이기도 하고 Ctrl+클릭도 우클릭이다."""
    widget.bind("<Button-3>", handler)
    if IS_MAC:
        widget.bind("<Button-2>", handler)
        widget.bind("<Control-Button-1>", handler)
PET_INK = "#17171b"
HELMET_COLOR = "#f5c542"
HAMMER_HEAD = "#b9bec7"
HAMMER_GRIP = "#8a5a2b"

# 직접 그린 14x14 픽셀 스프라이트.
#   . 투명   B 몸통   D 외곽선·입   E 눈   W 하이라이트   H 안전모
PET_BODY = (
    "..DD......DD..",   # 귀
    ".DBBD....DBBD.",
    ".DBBBBBBBBBBD.",
    "DBBBBBBBBBBBBD",
    "DBWBBBBBBBBBBD",   # 왼쪽 위 하이라이트
    "DBBBBBBBBBBBBD",   # 눈 (윗줄)
    "DBBBBBBBBBBBBD",   # 눈 (아랫줄)
    "DBBBBBBBBBBBBD",
    "DBBBBDDDDBBBBD",   # 입
    "DBBBBBBBBBBBBD",
    ".DBBBBBBBBBBD.",
    "..DDDDDDDDDD..",
)
# 걷기 4프레임의 다리. 한쪽씩 번갈아 앞으로 내민다
PET_LEGS = (
    ("..DD......DD..", "..DD......DD.."),
    (".DD.......DD..", ".DD.......DD.."),
    ("..DD......DD..", "..DD......DD.."),
    ("..DD.......DD.", "..DD.......DD."),
)
# 작업 중일 때 귀 자리를 덮는 안전모
PET_HELMET = (
    "....HHHHHH....",
    "..HHHHHHHHHH..",
    ".HHHHHHHHHHHH.",
)
# 손에 든 망치 (M 머리, K 자루)
PET_HAMMER = ("MMM", ".K.", ".K.", ".K.")

PET_W = 14
PET_H = len(PET_BODY) + len(PET_LEGS[0])
EYE_ROWS = (5, 6)
EYE_COLS = (3, 4, 9, 10)
X_EYE_ROWS = (4, 5, 6)
X_EYE_PATTERN = ("X.X", ".X.", "X.X")
X_EYE_LEFT, X_EYE_RIGHT = 3, 8


def pet_body_color(data) -> str:
    """캐릭터 몸 색. 잔량을 따라가거나 지정한 색으로 고정한다."""
    if CONFIG["pet_color_mode"] == "fixed":
        return CONFIG["pet_color"]
    return CONFIG[severity_key(remaining_pct((data or {}).get("five_hour")))]


def shade(color: str, factor: float) -> str:
    red, green, blue = hex_to_rgb(color)
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(channel * factor))) for channel in (red, green, blue))


def sprite_frame(facing: int, blinking: bool, leg_index: int,
                 dead_eyes: bool = False, helmet: bool = False) -> tuple:
    """방향·깜빡임·걸음·표정에 맞는 한 프레임을 만든다."""
    rows = list(PET_BODY)
    blank = rows[EYE_ROWS[0]]

    if dead_eyes:  # 에러일 때 눈이 X 자로
        for row, pattern in zip(X_EYE_ROWS, X_EYE_PATTERN):
            line = list(rows[row])
            for offset, cell in enumerate(pattern):
                if cell == "X":
                    line[X_EYE_LEFT + offset] = "E"
                    line[X_EYE_RIGHT + offset] = "E"
            rows[row] = "".join(line)
    elif blinking:
        line = list(blank)
        for col in EYE_COLS:
            line[col] = "D"
        rows[EYE_ROWS[0]], rows[EYE_ROWS[1]] = blank, "".join(line)
    else:
        line = list(blank)
        for col in EYE_COLS:  # 눈이 진행 방향으로 한 칸 쏠린다
            shifted = col + (1 if facing > 0 else -1)
            if 2 <= shifted <= PET_W - 3:
                line[shifted] = "E"
        eyes = "".join(line)
        rows[EYE_ROWS[0]] = rows[EYE_ROWS[1]] = eyes

    if helmet:
        rows[0:len(PET_HELMET)] = list(PET_HELMET)

    rows.extend(PET_LEGS[leg_index % len(PET_LEGS)])
    return tuple(rows)


def glitch(text: str, bucket: int) -> str:
    """에러 말풍선용. 같은 bucket 안에서는 같은 결과라 화면이 떨지 않는다."""
    chars = list(text)
    random.Random(bucket).shuffle(chars)
    return "".join(chars)


def action_key(label: str) -> str:
    for key, text in ACTION_LABELS.items():
        if text == label:
            return key
    return "none"


def state_conf(state: str) -> dict:
    """상태 설정 한 벌. 설정에 없으면 기본값으로 메운다."""
    saved = (CONFIG.get("states") or {}).get(state) or {}
    base = DEFAULT_STATES.get(state, {"msg": "", "act": "none", "hold": 0})
    return {**base, **saved}


def agent_message(state: str, data) -> str:
    """상태별 말풍선 문구. 비워두면 그 상태에선 말풍선을 띄우지 않는다."""
    conf = state_conf(state)
    text = (conf.get("msg") or "").strip()
    if not text:
        return ""
    if conf.get("act") in GLITCH_ACTIONS:      # 깨진 느낌으로 글자를 섞는다
        return glitch(text, int(time.monotonic() * 2.5))
    return fill_placeholders(text, data)


class Pet:
    """위젯 윗변을 걸어다니는 캐릭터.

    Claude Code 훅이 남긴 상태 파일을 읽어 반응한다 — 입력을 기다릴 땐 계속
    뛰고, 작업 중이면 안전모에 망치를 들고, 에러면 눈이 X 가 된다.

    투명 키 컬러를 건 테두리 없는 창이라 그린 픽셀만 화면에 남고 빈 곳은
    클릭이 통과한다. 스프라이트는 캔버스 사각형으로 한 픽셀씩 찍는다 —
    PIL 이미지를 얹으면 안티에일리어싱된 가장자리에 키 컬러가 섞여
    분홍 테두리가 생긴다.
    """

    BUBBLE_H = 40
    FRAME_MS = 60
    HOP_FRAMES = 14
    BUBBLE_ROTATE_S = 30   # 항상 표시일 때 문구가 여럿이면 이 주기로 바꾼다
    STATE_POLL_MS = 300    # 상태 파일을 들여다보는 주기

    def __init__(self, app: "App", index: int = 0) -> None:
        self.app = app
        self.index = index          # 0 이 기본 캐릭터, 1 이상은 서브에이전트
        self.unit = max(1.0, max(10, int(CONFIG["pet_size"])) / PET_H)
        self.sprite_w = round(PET_W * self.unit)
        self.sprite_h = round(PET_H * self.unit)
        self.size = self.sprite_h
        self.w = 320  # 말풍선이 들어갈 여유. 남는 폭은 전부 투명이고 클릭도 통과한다
        self.h = self.sprite_h + self.BUBBLE_H

        if IS_MAC:
            # Tk 9 는 투명 캔버스를 못 그린다. AppKit 창에 PIL 로 그린 프레임을 얹는다
            self.win = macos.PetWindow(self.w, self.h, on_left=lambda *_a: self.on_click(),
                                       on_right=self._popup_at)
            self.canvas = macos.PilCanvas(self.w, self.h)
        else:
            self.win = tk.Toplevel(app.widget.tk_root)
            self.win.withdraw()                   # 투명 속성은 창이 뜨기 전에 걸어야 한다
            self.win.overrideredirect(True)
            hide_from_taskbar(self.win)
            self.bg = pet_window_bg(self.win)
            self.win.configure(bg=self.bg)
            self.canvas = tk.Canvas(self.win, width=self.w, height=self.h, bg=self.bg,
                                    highlightthickness=0, bd=0)
            self.canvas.pack()
            self.canvas.bind("<Button-1>", self.on_click)
            bind_context_menu(self.canvas, app.widget._popup)

        left, right, _floor = self._bounds()
        # 마리마다 시작 위치를 벌려둔다. 겹쳐서 한 마리로 보이지 않게
        span = max(1.0, right - left)
        self.x = left + span * ((index * 0.37 + random.random() * 0.2) % 1.0)
        self.facing = random.choice((-1, 1))
        self.state = "walk"
        self.state_left = random.randint(60, 180)
        self.frame = 0
        self.leg = 0
        self.hop = 0
        self.blink_in = random.randint(45, 130)
        self.blinking = False
        self.bubble_until = 0.0
        self.says_template = ""
        self.says_rotate_at = 0.0
        self.agent_state = "idle"
        self.agent_at = 0.0
        self._jumped_for = None
        self._state_stamp = None
        self.alive = True
        self._drawn = None                            # 마지막으로 그린 프레임의 지문
        self._reposition()
        self.draw()
        if not IS_MAC:
            self.win.deiconify()
            self.win.update_idletasks()
            make_floating(self.win)

    def _popup_at(self, x_root: int, y_root: int) -> None:
        """맥 네이티브 창의 우클릭을 위젯의 Tk 메뉴로 넘긴다."""
        event = type("Event", (), {"x_root": x_root, "y_root": y_root})()
        self.app.widget._popup(event)

    # -- 수명 -------------------------------------------------------
    def destroy(self) -> None:
        self.alive = False
        try:
            self.win.destroy()
        except tk.TclError:
            pass

    def effective_state(self) -> str:
        return self.app.effective_state()

    # -- 위치 -------------------------------------------------------
    def _bounds(self):
        """걸어다닐 구간과 발이 닿을 높이. 위젯 윗변이 바닥이다.

        위젯을 드래그하면 따라와야 하므로 매 프레임 다시 읽는다.
        """
        root = self.app.widget.root
        width = root.winfo_width()
        if width <= 1:                                # 아직 배치 전이면 요청 크기로
            width = root.winfo_reqwidth()
        pad = self.sprite_w / 2
        left = root.winfo_x() + pad
        right = max(left + 1, root.winfo_x() + width - pad)
        return left, right, root.winfo_y()

    def _lift(self) -> int:
        if not self.hop:
            return 0
        return int(math.sin(math.pi * (1 - self.hop / self.HOP_FRAMES)) * 13)

    def _reposition(self) -> None:
        """이동과 점프는 창을 옮겨서 처리한다. 캔버스를 다시 그릴 필요가 없다."""
        _left, _right, floor = self._bounds()
        x = int(self.x - self.w / 2)                  # 창은 캐릭터를 가운데 둔다
        y = int(floor - self.h + 3 - self._lift())    # 발이 위젯 윗변에 닿게
        self.win.geometry(f"{self.w}x{self.h}+{x}+{y}")

    # -- 상호작용 ---------------------------------------------------
    def on_click(self, _event=None) -> None:
        self.hop = self.HOP_FRAMES
        if not CONFIG["pet_bubble_enabled"]:
            return
        self.says_template = pick_bubble_template()
        if CONFIG["pet_bubble_mode"] == "always":
            self.says_rotate_at = time.monotonic() + self.BUBBLE_ROTATE_S
        else:
            self.bubble_until = time.monotonic() + 3.5

    def _bubble_now(self):
        """지금 그릴 말풍선 문구. 없으면 None.

        문구(템플릿)는 유지한 채 값만 매번 다시 채운다 — 항상 표시일 때
        남은 %와 리셋 시간이 계속 최신으로 보이게 하려는 것이다.
        """
        if not CONFIG["pet_bubble_enabled"]:
            return None
        state = self.effective_state()
        if state != "idle" and self.index == 0:  # 상태 문구는 대표 한 마리만
            forced = agent_message(state, self.app.data)  # 상태 문구가 평소 문구보다 우선
            if forced:
                return forced
        now = time.monotonic()
        if CONFIG["pet_bubble_mode"] == "always":
            if not self.says_template or now >= self.says_rotate_at:
                self.says_template = pick_bubble_template()
                self.says_rotate_at = now + self.BUBBLE_ROTATE_S
        elif not (now < self.bubble_until and self.says_template):
            return None
        return fill_placeholders(self.says_template, self.app.data)

    # -- 애니메이션 -------------------------------------------------
    def step(self) -> None:
        if not self.alive:
            return
        self.frame += 1
        agent = self.effective_state()
        action = state_conf(agent).get("act", "none")

        self.state_left -= 1
        if self.state_left <= 0:  # 걷다가 가끔 멈춰 서고, 설 때 방향도 바꾼다
            if self.state == "walk":
                self.state, self.state_left = "idle", random.randint(30, 80)
            else:
                self.state, self.state_left = "walk", random.randint(60, 200)
                if random.random() < 0.45:
                    self.facing = -self.facing

        left, right, _floor = self._bounds()
        if self.state == "walk":
            self.x += self.facing * max(1, int(CONFIG["pet_speed"]))
            if self.x <= left or self.x >= right:
                self.x = min(max(self.x, left), right)
                self.facing = -self.facing
            if self.frame % 4 == 0:
                self.leg += 1
        else:
            self.leg = 0
        self.x = min(max(self.x, left), right)        # 위젯이 움직여도 벗어나지 않게

        if action == "jump_loop" and not self.hop:    # 봐달라고 계속 뛴다
            self.hop = self.HOP_FRAMES
        elif action == "jump" and agent != self._jumped_for:
            self.hop = self.HOP_FRAMES               # 상태가 바뀔 때 한 번만
        self._jumped_for = agent if action == "jump" else None
        if self.hop:
            self.hop -= 1

        self.blink_in -= 1
        self.blinking = self.blink_in < 0
        if self.blink_in < -3:
            self.blink_in = random.randint(45, 130)

        self._reposition()
        self.draw()
        self.app.tk_root.after(self.FRAME_MS, self.step)

    # -- 그리기 -----------------------------------------------------
    def draw(self) -> None:
        body = pet_body_color(self.app.data)
        agent = self.effective_state()
        action = state_conf(agent).get("act", "none")
        dead_eyes = action == "dead"
        helmet = action == "gear"
        says = self._bubble_now()
        fingerprint = (self.facing, self.blinking, self.leg % len(PET_LEGS), body,
                       says, dead_eyes, helmet)
        if fingerprint == self._drawn:
            return  # 픽셀 200개를 매 프레임 다시 찍지 않게, 바뀔 때만 그린다
        self._drawn = fingerprint

        canvas = self.canvas
        canvas.delete("all")
        palette = {"B": body, "D": shade(body, 0.55), "E": PET_INK,
                   "W": shade(body, 1.35), "H": HELMET_COLOR,
                   "M": HAMMER_HEAD, "K": HAMMER_GRIP}

        unit = self.unit
        origin_x = (self.w - self.sprite_w) / 2
        origin_y = self.h - 3 - self.sprite_h
        rows = sprite_frame(self.facing, self.blinking, self.leg, dead_eyes, helmet)
        self._blit(canvas, rows, palette, origin_x, origin_y, unit)

        if helmet:
            self._draw_hammer(canvas, palette, origin_x, origin_y, unit)
        if says:
            self._draw_bubble(canvas, self.w / 2, origin_y, says)
        if IS_MAC:
            self.win.set_image(canvas.image)

    def _blit(self, canvas, rows, palette, origin_x, origin_y, unit) -> None:
        for row, line in enumerate(rows):
            y0 = origin_y + round(row * unit)
            y1 = origin_y + round((row + 1) * unit)
            for col, cell in enumerate(line):
                if cell == ".":
                    continue
                x0 = origin_x + round(col * unit)
                x1 = origin_x + round((col + 1) * unit)
                canvas.create_rectangle(x0, y0, x1, y1,
                                        fill=palette[cell], outline=palette[cell])

    def _draw_hammer(self, canvas, palette, origin_x, origin_y, unit) -> None:
        """몸통 옆에 망치를 들려준다. 걸음에 맞춰 위아래로 흔들린다."""
        swing = -1 if (self.frame // 4) % 2 else 0
        col = PET_W if self.facing > 0 else -len(PET_HAMMER[0])
        row0 = 4 + swing
        self._blit(canvas, PET_HAMMER, palette,
                   origin_x + col * unit, origin_y + row0 * unit, unit)

    def _measure(self, canvas, text: str, font) -> float:
        if hasattr(canvas, "measure"):
            return canvas.measure(text, font)
        probe = canvas.create_text(-999, -999, text=text, font=font, anchor="w")
        x1, _y1, x2, _y2 = canvas.bbox(probe)
        canvas.delete(probe)
        return x2 - x1

    def _draw_bubble(self, canvas, cx: float, top_of_body: float, text: str) -> None:
        font = (UI_FONT, 8, "bold")

        limit = self.w - 26  # 창 밖으로 나가면 잘리므로 들어갈 만큼만 남긴다
        width = self._measure(canvas, text, font)
        while width > limit and len(text) > 4:
            text = text[:-2] + "…"
            width = self._measure(canvas, text, font)
        half = width / 2 + 9

        bottom = max(14, top_of_body - 5)
        top = max(1, bottom - 20)
        round_rect(canvas, cx - half, top, cx + half, bottom, 7,
                   fill="#f4f4f6", outline="#c9c9cf")
        canvas.create_polygon(cx - 4, bottom - 1, cx + 4, bottom - 1, cx, bottom + 5,
                              fill="#f4f4f6", outline="#f4f4f6")
        canvas.create_text(cx, (top + bottom) / 2, text=text, font=font, fill=PET_INK)


# ---------------------------------------------------------------- 앱

class App:
    def __init__(self, with_widget: bool = True) -> None:
        self.data = read_cache()
        self.error = None
        self.notified = {}
        self.settings = None
        self.pets = []
        self._pet_job = None
        self.agent_state = "idle"
        self.agent_at = 0.0
        self.agent_count = 0        # 살아있는 서브에이전트 수
        self._state_stamp = None
        self.stop = threading.Event()

        # Tk 루트를 제일 먼저 만든다. 맥의 Tk 는 자기가 만든 NSApplication 서브클래스를
        # 기대해서, AppKit(메뉴바 아이콘)이 먼저 NSApp 을 만들어 두면 초기화 중에 죽는다.
        # 루트는 숨긴 일반 창으로 두고 오버레이는 Toplevel 로 내린다 — 루트가
        # overrideredirect 면 활성화가 안 돼 앱 전체의 키보드 포커스가 묶이기 때문.
        self.tk_root = tk.Tk()
        self.tk_root.withdraw()
        if IS_MAC:
            macos.hide_from_dock()   # Dock 아이콘 없이 메뉴바에만 산다

        self.icons = {}
        icon_cls = macos.Icon if IS_MAC else pystray.Icon
        for key, shape, short, label, _tag in WINDOWS:
            self.icons[key] = icon_cls(
                f"claude_{key}",
                render_icon(None, shape),
                f"Claude {short}",
                menu=self._menu(key, label),
            )
        self.widget = Widget(self) if with_widget else None
        self.sync_pets()

    # -- 메뉴 -------------------------------------------------------
    def _menu(self, key: str, label: str) -> pystray.Menu:
        return pystray.Menu(
            pystray.MenuItem(label, None, enabled=False),
            pystray.MenuItem(lambda _item: self._detail(key), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("지금 새로고침", lambda *_a: self.refresh_async(notify=False)),
            pystray.MenuItem("설정...", lambda *_a: self._on_tk(self.open_settings)),
            pystray.MenuItem("캐릭터 표시", lambda *_a: self._on_tk(self.toggle_pet),
                             checked=lambda _item: bool(CONFIG["pet_enabled"])),
            pystray.MenuItem(
                AUTOSTART_LABEL,
                lambda *_a: (toggle_autostart(), self.refresh_menus()),
                checked=lambda _item: autostart_enabled(),
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("종료", self.quit),
        )

    def _detail(self, key: str) -> str:
        if self.error:
            return self.error
        window = (self.data or {}).get(key)
        remaining = remaining_pct(window)
        if remaining is None:
            return "데이터 없음"
        return f"{remaining:.0f}% 남음 (사용 {100 - remaining:.0f}%) · {resets_in(window)}"

    def open_settings(self) -> None:
        if not self.widget:
            return
        if self.settings is not None:
            try:
                self.settings._activate()
                return
            except tk.TclError:
                pass
        self.settings = SettingsWindow(self)

    # -- 스레드 경계 ------------------------------------------------
    def _on_tk(self, fn) -> None:
        """tkinter 위젯은 반드시 tk 스레드에서만 건드린다.

        맥에선 AppKit(메뉴바 아이콘)도 메인 스레드에서만 만져야 하므로 같은 길로 보낸다.
        """
        if self.tk_root is not None:
            try:
                self.tk_root.after(0, fn)
            except RuntimeError:
                pass

    def refresh_async(self, notify: bool = True) -> None:
        threading.Thread(target=self.refresh, args=(notify,), daemon=True).start()

    # -- 에이전트 상태 ----------------------------------------------
    def poll_agent_state(self) -> None:
        """훅이 남긴 상태 파일을 읽는다. 바뀌었을 때만 파싱한다.

        여러 마리가 각자 읽을 이유가 없어 App 이 한 번만 읽고 나눠준다.
        """
        if self.widget:
            self.widget.root.after(Pet.STATE_POLL_MS, self.poll_agent_state)
        try:
            stamp = AGENT_STATE_FILE.stat().st_mtime_ns
        except OSError:
            return
        if stamp == self._state_stamp:
            return
        self._state_stamp = stamp
        try:
            data = json.loads(AGENT_STATE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        state = data.get("state", "idle")
        if state in AGENT_STATES:
            self.agent_state = state
            self.agent_at = time.time()
        try:
            self.agent_count = max(0, int(data.get("agents", 0)))
        except (TypeError, ValueError):
            self.agent_count = 0
        self.sync_pets()

    def effective_state(self) -> str:
        """오래된 상태는 흘려보낸다. 훅을 놓쳐도 캐릭터가 굳어있지 않게."""
        if self.agent_state == "idle":
            return "idle"
        age = time.time() - self.agent_at
        hold = int(state_conf(self.agent_state).get("hold", 0) or 0)
        if hold and age > hold:
            return "idle"
        return self.agent_state if age <= AGENT_STALE_S else "idle"

    def wanted_pets(self) -> int:
        """기본 한 마리 + 서브에이전트 수, 설정한 최대치까지."""
        if not (self.widget and CONFIG["pet_enabled"]):
            return 0
        ceiling = max(1, int(CONFIG.get("pet_max", 3)))
        if self.effective_state() == "idle":
            return 1                       # 턴이 끝나면 다시 한 마리로
        return max(1, min(ceiling, 1 + self.agent_count))

    def sync_pets(self) -> None:
        """마릿수를 목표에 맞춘다. 남는 마리는 지우고 모자라면 새로 만든다."""
        if not self.widget:
            return
        target = self.wanted_pets()
        while len(self.pets) > target:
            self.pets.pop().destroy()
        while len(self.pets) < target:
            pet = Pet(self, index=len(self.pets))
            self.pets.append(pet)
            pet.step()

    def rebuild_pet(self) -> None:
        """캐릭터를 다시 만든다. 크기·창 속성은 생성 시점에만 정해지기 때문이다.

        슬라이더를 끄는 동안 매 픽셀마다 창을 새로 만들지 않도록 잠깐 묶어둔다.
        """
        if not self.widget:
            return
        if self._pet_job is not None:
            try:
                self.widget.root.after_cancel(self._pet_job)
            except (tk.TclError, ValueError):
                pass
        self._pet_job = self.widget.root.after(250, self._respawn_pet)

    def _respawn_pet(self) -> None:
        self._pet_job = None
        while self.pets:
            self.pets.pop().destroy()
        self.sync_pets()

    def refresh_pet_look(self) -> None:
        """창을 새로 만들지 않고 스프라이트만 다시 그린다 (색·모드 변경용)."""
        for pet in self.pets:
            pet._drawn = None
            pet.draw()

    def toggle_pet(self) -> None:
        CONFIG["pet_enabled"] = not CONFIG["pet_enabled"]
        save_config()
        self.rebuild_pet()
        self.refresh_menus()

    def toggle_tray(self) -> None:
        CONFIG["tray_icons"] = not CONFIG["tray_icons"]
        save_config()
        self.apply_tray_visibility()

    def apply_tray_visibility(self) -> None:
        """아이콘을 없애지 않고 숨긴다. 켜면 그 자리에 다시 나온다."""
        def _apply():
            for icon in self.icons.values():
                try:
                    icon.visible = bool(CONFIG["tray_icons"])
                except Exception:
                    pass
        if IS_MAC:
            self._on_tk(_apply)   # AppKit 은 메인 스레드에서만
        else:
            _apply()

    def refresh_menus(self) -> None:
        """맥의 pystray 는 메뉴를 한 번만 만들어 체크 표시가 굳는다. 바뀔 때마다 다시 만든다."""
        if not IS_MAC:
            return
        for icon in self.icons.values():
            try:
                icon.update_menu()
            except Exception:
                pass

    def repaint_icons(self) -> None:
        """임계값·색을 바꿨을 때 조회 없이 아이콘만 다시 그린다."""
        for key, shape, _short, _label, _tag in WINDOWS:
            remaining = remaining_pct((self.data or {}).get(key))
            self.icons[key].icon = render_icon(remaining, shape)

    # -- 갱신 -------------------------------------------------------
    def refresh(self, notify: bool = True) -> None:
        try:
            self.data = fetch_usage()
            self.error = None
        except Exception as exc:  # 네트워크·토큰 문제는 직전 캐시로 버틴다
            self.error = f"조회 실패: {type(exc).__name__}"
        self._apply(notify)

    def _apply(self, notify: bool) -> None:
        if IS_MAC and threading.current_thread() is not threading.main_thread():
            self._on_tk(lambda: self._apply(notify))   # AppKit 은 메인 스레드에서만
            return
        for key, shape, short, _label, _tag in WINDOWS:
            icon = self.icons[key]
            window = (self.data or {}).get(key)
            remaining = remaining_pct(window)
            icon.icon = render_icon(remaining, shape)
            icon.title = self._tooltip(key, short, window, remaining)
            if notify:
                self._maybe_notify(icon, key, short, remaining)
        if self.widget:
            self._on_tk(lambda: self.widget.redraw())

    def _tooltip(self, key: str, short: str, window, remaining) -> str:
        if remaining is None:
            return f"Claude {short}\n{self.error or '데이터 없음'}"
        lines = [
            f"Claude {short}",
            f"{remaining:.0f}% 남음 (사용 {100 - remaining:.0f}%)",
            resets_in(window),
        ]
        if key == "seven_day":
            lines += scoped_lines(self.data)[:2]
        return "\n".join(lines)[:120]

    def _maybe_notify(self, icon, key: str, short: str, remaining) -> None:
        if remaining is None:
            return
        level = 2 if remaining <= CONFIG["crit_at"] else 1 if remaining <= CONFIG["warn_at"] else 0
        if level > self.notified.get(key, 0):
            try:
                icon.notify(f"{short} {remaining:.0f}% 남음", "Claude 사용량 경고")
            except Exception:
                pass
        self.notified[key] = level

    def _loop(self) -> None:
        while not self.stop.is_set():
            self.refresh()
            # 설정에서 주기를 바꾸면 다음 사이클부터 반영되도록 짧게 나눠 기다린다
            deadline = time.monotonic() + max(60, int(CONFIG["poll_minutes"]) * 60)
            while not self.stop.is_set() and time.monotonic() < deadline:
                self.stop.wait(3)

    def quit(self, *_args) -> None:
        self.stop.set()
        if IS_MAC:
            # 맥의 icon.stop() 은 NSApp 자체를 세우려 든다. 아이콘만 감추고 Tk 루프를 끝낸다
            def _teardown():
                for icon in self.icons.values():
                    try:
                        icon.visible = False
                    except Exception:
                        pass
                for pet in list(self.pets):
                    pet.destroy()
                self.tk_root.destroy()
            self._on_tk(_teardown)
            return
        for icon in self.icons.values():
            try:
                icon.stop()
            except Exception:
                pass
        for pet in list(self.pets):
            self._on_tk(pet.destroy)
        self._on_tk(self.tk_root.destroy if self.tk_root else (lambda: None))

    def run(self) -> None:
        if IS_MAC:
            # 메뉴바 아이콘은 메인 스레드의 NSApp 루프를 탄다 — Tk mainloop 이 그 루프다
            for icon in self.icons.values():
                icon.run_detached(setup=lambda _icon: None)
                icon.visible = bool(CONFIG["tray_icons"])
        else:
            for icon in self.icons.values():
                threading.Thread(target=icon.run, daemon=True).start()
            time.sleep(0.5)  # 각 아이콘이 자기 메시지 루프를 잡을 시간
            self.apply_tray_visibility()
        threading.Thread(target=self._loop, daemon=True).start()
        if self.widget:
            self.widget.redraw()
            self.widget.tick()
            self.widget.keep_on_top()
            self.poll_agent_state()
        if self.tk_root is not None:
            self.tk_root.mainloop()
        else:
            while not self.stop.wait(1):
                pass


def print_once() -> None:
    try:
        data = fetch_usage()
    except Exception as exc:
        print(f"조회 실패: {exc}")
        return
    for key, _shape, short, _label, _tag in WINDOWS:
        window = data.get(key)
        remaining = remaining_pct(window)
        print(f"{short}: {remaining:.0f}% 남음 (사용 {100 - remaining:.0f}%) · {resets_in(window)}")
    for line in scoped_lines(data):
        print(f"  - {line}")


if __name__ == "__main__":
    load_config()
    if "--once" in sys.argv:
        print_once()
    else:
        if IS_WIN:
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)  # 고DPI에서 흐려지지 않게
            except Exception:
                pass
        App(with_widget="--no-widget" not in sys.argv).run()
