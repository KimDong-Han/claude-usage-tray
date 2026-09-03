"""macOS 전용 글루. tray.py 가 sys.platform == "darwin" 일 때만 불러 쓴다.

윈도우의 ctypes/user32 호출을 대신한다. 전부 pyobjc(AppKit) 위에서 돌고,
AppKit 이 없으면 각 함수가 조용히 아무것도 안 하거나 무난한 값을 돌려준다.

  hide_from_dock()      Dock 아이콘·Cmd-Tab 목록에서 앱을 뺀다 (accessory 정책)
  activate_app()        설정창을 앞으로 가져올 때. accessory 앱은 이걸 안 하면 포커스를 못 받는다
  work_area()           메뉴바·Dock 을 뺀 화면 영역 (Tk 좌표계)
  set_floating(win)     창을 항상 위로. Tk 9 의 -topmost 는 테두리 없는 창에 안 먹어서 NSWindow 레벨을 직접 올린다
  Icon                  pystray.Icon 의 레티나 대응판 (2배 픽셀로 그려 흐릿하지 않게)
  autostart_*           ~/Library/LaunchAgents 의 plist 로 로그인 시 자동 실행
"""
from __future__ import annotations

import plistlib
import subprocess
import sys
from pathlib import Path

try:
    import AppKit
    import Foundation
except ImportError:      # pyobjc 가 없으면 기능만 빠지고 앱은 뜬다
    AppKit = Foundation = None

import pystray

LAUNCH_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.claude-usage-tray.plist"
FONT_CANDIDATES = (   # 메뉴바 아이콘의 숫자용. 숫자만 찍으므로 한글은 필요 없다
    "/System/Library/Fonts/Supplemental/Arial Rounded Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/System/Library/Fonts/SFNSRounded.ttf",
    "/System/Library/Fonts/Supplemental/Verdana Bold.ttf",
)


def _app():
    return AppKit.NSApplication.sharedApplication() if AppKit else None


def hide_from_dock() -> None:
    """Info.plist 의 LSUIElement 와 같은 효과. 메뉴바 앱은 Dock 에 안 뜨는 게 맞다."""
    app = _app()
    if app is not None:
        app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)


def activate_app() -> None:
    app = _app()
    if app is not None:
        app.activateIgnoringOtherApps_(True)


def work_area() -> tuple[int, int, int, int] | None:
    """(left, top, right, bottom). 주 화면 기준이라 Tk 좌표와 원점이 같다.

    AppKit 은 원점이 왼쪽 아래라 세로만 뒤집는다.
    """
    if AppKit is None:
        return None
    screens = AppKit.NSScreen.screens()
    if not screens:
        return None
    primary = screens[0]
    height = primary.frame().size.height
    vis = primary.visibleFrame()
    left = int(vis.origin.x)
    right = int(vis.origin.x + vis.size.width)
    top = int(height - (vis.origin.y + vis.size.height))
    bottom = int(height - vis.origin.y)
    return left, top, right, bottom


def _find_window(title: str):
    app = _app()
    if app is None:
        return None
    for win in app.windows():
        if win.title() == title:
            return win
    return None


def set_floating(window, on: bool = True) -> bool:
    """Tk 창을 NSWindow 로 찾아 레벨을 올린다.

    Tk 는 NSWindow 핸들을 안 내주므로 제목으로 찾는다. overrideredirect 창도
    제목은 유지되니 tray.py 쪽에서 창마다 고유한 제목을 붙여둔다.
    """
    win = _find_window(window.title())
    if win is None:
        return False
    win.setLevel_(AppKit.NSFloatingWindowLevel if on else AppKit.NSNormalWindowLevel)
    return True


class Icon(pystray.Icon):
    """메뉴바 아이콘. pystray 는 22pt 를 22px 로 그려 레티나에서 흐리다."""

    def _assert_image(self):
        if AppKit is None:
            return super()._assert_image()
        thickness = int(self._status_bar.thickness())
        scale = int(AppKit.NSScreen.mainScreen().backingScaleFactor() or 2)
        px = thickness * scale
        if self._icon_image is not None and self._icon_image.size().width == thickness:
            return
        import io
        from PIL import Image
        source = self._icon if self._icon.size == (px, px) else self._icon.resize((px, px), Image.LANCZOS)
        buf = io.BytesIO()
        source.save(buf, "png")
        image = AppKit.NSImage.alloc().initWithData_(Foundation.NSData(buf.getvalue()))
        image.setSize_(Foundation.NSSize(thickness, thickness))
        self._icon_image = image
        self._status_item.button().setImage_(image)

    def _update_icon(self):
        self._icon_image = None   # 크기 검사에 걸리지 않게 비운 뒤 다시 그린다
        super()._update_icon()


# ---------------------------------------------------------------- 자동 시작

def autostart_enabled() -> bool:
    return LAUNCH_AGENT.exists()


def autostart_set(on: bool, script: Path) -> None:
    label = LAUNCH_AGENT.stem
    if not on:
        subprocess.run(["launchctl", "unload", str(LAUNCH_AGENT)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        LAUNCH_AGENT.unlink(missing_ok=True)
        return
    LAUNCH_AGENT.parent.mkdir(parents=True, exist_ok=True)
    plist = {
        "Label": label,
        "ProgramArguments": [sys.executable, str(script)],
        "WorkingDirectory": str(script.parent),
        "RunAtLoad": True,
        "ProcessType": "Interactive",
        "StandardOutPath": "/tmp/claude-usage-tray.log",
        "StandardErrorPath": "/tmp/claude-usage-tray.log",
    }
    # 여기서 load 하면 RunAtLoad 때문에 두 번째 인스턴스가 바로 뜬다. 다음 로그인부터 적용된다
    LAUNCH_AGENT.write_bytes(plistlib.dumps(plist))


# ---------------------------------------------------------------- 캐릭터 창

# Tk 9 의 -transparent 는 캔버스 내용을 아예 안 그리거나 배경을 검게 칠한다 (둘 다 봤다).
# 그래서 캐릭터는 Tk 를 거치지 않고 AppKit 창에 PIL 로 그린 프레임을 얹는다.
# PilCanvas 는 tray.py 의 그리기 코드가 쓰는 tk.Canvas 메서드 이름을 그대로 흉내 낸다.

from PIL import Image, ImageDraw, ImageFont  # noqa: E402

_font_cache: dict = {}
TEXT_FONT = "/System/Library/Fonts/AppleSDGothicNeo.ttc"   # 한글·기호가 다 있는 시스템 글꼴


def _text_face() -> tuple[str, int]:
    """말풍선 글꼴 파일과 ttc 안의 굵은 얼굴 번호. Arial 계열은 한글과 '·' 이 없다."""
    if Path(TEXT_FONT).exists():
        for index in range(12):
            try:
                _family, style = ImageFont.truetype(TEXT_FONT, 10, index=index).getname()
            except OSError:
                break
            if style.lower() == "bold":
                return TEXT_FONT, index
        return TEXT_FONT, 0
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return path, 0
    return "", 0


def _pil_font(spec, scale: int):
    """Tk 글꼴 튜플 (family, size, 'bold') → PIL 트루타입."""
    size = int(round((spec[1] if isinstance(spec, (tuple, list)) and len(spec) > 1 else 9) * scale * 1.15))
    if size not in _font_cache:
        path, index = _text_face()
        try:
            _font_cache[size] = ImageFont.truetype(path, size, index=index) if path else ImageFont.load_default()
        except OSError:
            _font_cache[size] = ImageFont.load_default()
    return _font_cache[size]


class PilCanvas:
    """tk.Canvas 흉내. 좌표는 Tk 의 논리 픽셀이고 내부에선 레티나용으로 2배로 그린다."""

    def __init__(self, width: int, height: int, scale: int = 2) -> None:
        self.w, self.h, self.scale = width, height, scale
        self.image = None
        self.delete("all")

    def _s(self, *coords):
        return [c * self.scale for c in coords]

    def delete(self, _what="all") -> None:
        self.image = Image.new("RGBA", (self.w * self.scale, self.h * self.scale), (0, 0, 0, 0))
        self.draw = ImageDraw.Draw(self.image)

    def create_rectangle(self, x0, y0, x1, y1, fill=None, outline=None, **_kw) -> None:
        # PIL 은 끝점을 포함하므로 확대한 뒤에 1 을 뺀다. 확대 전에 빼면 픽셀 사이에 틈이 생겨
        # 스프라이트가 격자무늬로 보인다
        s = self.scale
        self.draw.rectangle([round(x0 * s), round(y0 * s), round(x1 * s) - 1, round(y1 * s) - 1],
                            fill=fill or None, outline=None)

    def create_polygon(self, *points, fill=None, outline=None, **_kw) -> None:
        pts = list(points[0]) if len(points) == 1 else list(points)
        xy = [(pts[i] * self.scale, pts[i + 1] * self.scale) for i in range(0, len(pts) - 1, 2)]
        self.draw.polygon(xy, fill=fill or None, outline=outline or None)

    def round_rect(self, x1, y1, x2, y2, radius, fill=None, outline=None, **_kw) -> None:
        self.draw.rounded_rectangle(self._s(x1, y1, x2, y2), radius=radius * self.scale,
                                    fill=fill or None, outline=outline or None,
                                    width=self.scale if outline else 0)

    def create_text(self, x, y, text="", font=None, fill="#000000", anchor="center", **_kw) -> None:
        pil_font = _pil_font(font, self.scale)
        pil_anchor = {"center": "mm", "w": "lm", "e": "rm", "n": "mt", "s": "mb"}.get(anchor, "mm")
        self.draw.text((x * self.scale, y * self.scale), text, font=pil_font, fill=fill, anchor=pil_anchor)

    def measure(self, text: str, font) -> float:
        left, _t, right, _b = _pil_font(font, self.scale).getbbox(text)
        return (right - left) / self.scale


if AppKit is not None:
    import objc

    class _PetView(AppKit.NSView):
        """프레임 이미지를 그리고 클릭을 파이썬 콜백으로 넘기는 뷰."""

        def initWithFrame_(self, frame):
            self = objc.super(_PetView, self).initWithFrame_(frame)
            if self is not None:
                self.image = None
                self.on_left = None
                self.on_right = None
            return self

        def drawRect_(self, _rect):
            if self.image is not None:
                self.image.drawInRect_fromRect_operation_fraction_(
                    self.bounds(), AppKit.NSZeroRect, AppKit.NSCompositingOperationSourceOver, 1.0)

        def acceptsFirstMouse_(self, _event):
            return True

        def mouseDown_(self, event):
            if event.modifierFlags() & AppKit.NSEventModifierFlagControl:
                _fire(self.on_right)
            else:
                _fire(self.on_left)

        def rightMouseDown_(self, _event):
            _fire(self.on_right)

        def mouseDragged_(self, _event):   # 끌기는 창 이동으로 이어지지 않게 삼킨다
            pass

        def mouseUp_(self, _event):
            pass

    def _fire(callback):
        """콜백에 Tk 화면 좌표를 넘긴다. (NSObject 메서드는 이름 규칙이 있어 밖으로 뺐다)"""
        if callback is None:
            return
        loc = AppKit.NSEvent.mouseLocation()   # 주 화면 왼쪽 아래가 원점
        height = AppKit.NSScreen.screens()[0].frame().size.height
        callback(int(loc.x), int(height - loc.y))


class PetWindow:
    """테두리·그림자 없는 투명 NSWindow. Tk Toplevel 이 하던 일 중 캐릭터에 필요한 것만."""

    def __init__(self, width: int, height: int, on_left=None, on_right=None) -> None:
        self.w, self.h = width, height
        rect = Foundation.NSMakeRect(0, 0, width, height)
        self.win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
        self.win.setOpaque_(False)
        self.win.setBackgroundColor_(AppKit.NSColor.clearColor())
        self.win.setHasShadow_(False)
        self.win.setLevel_(AppKit.NSFloatingWindowLevel)
        self.win.setCollectionBehavior_(
            AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
            | AppKit.NSWindowCollectionBehaviorStationary)
        self.win.setReleasedWhenClosed_(False)
        # 테두리 없는 창은 배경을 잡고 끌면 창 자체가 따라오는 경우가 있다.
        # 캐릭터 위치는 앱이 정하니 끌기는 막는다
        self.win.setMovableByWindowBackground_(False)
        self.win.setMovable_(False)
        self.view = _PetView.alloc().initWithFrame_(rect)
        self.view.on_left = on_left
        self.view.on_right = on_right
        self.win.setContentView_(self.view)
        self.win.orderFrontRegardless()

    def geometry(self, spec: str) -> None:
        """Tk 식 'WxH+X+Y'. 창을 캐릭터 위치로 옮긴다."""
        size, x, y = spec.replace("+", " ").split()
        height = AppKit.NSScreen.screens()[0].frame().size.height
        self.win.setFrameOrigin_(Foundation.NSMakePoint(int(x), height - int(y) - self.h))

    def set_image(self, image) -> None:
        import io
        buf = io.BytesIO()
        image.save(buf, "png")
        ns = AppKit.NSImage.alloc().initWithData_(Foundation.NSData(buf.getvalue()))
        ns.setSize_(Foundation.NSSize(self.w, self.h))
        self.view.image = ns
        self.view.setNeedsDisplay_(True)

    def destroy(self) -> None:
        self.win.orderOut_(None)
        self.win.close()
