# Claude 사용량 표시기 작업 로그 — 2026-09-03 · macOS 포팅

> 이전: 없음 (첫 로그).
> 이 세션: 윈도우 전용이던 Claude 사용량 표시기(tkinter + pystray + PIL)를 macOS 로 포팅. AppKit(pyobjc) 글루 모듈 신설, tray.py/install.py 플랫폼 분기, 실기(Homebrew Python 3.14 + Tk 9.0.4)에서 위젯·메뉴바 아이콘·캐릭터·설정창을 실제로 띄워 확인.

---

## 0. 한눈에
- `macos.py` 신설 — AppKit 글루(Dock 숨김, 플로팅 창, 레티나 메뉴바 아이콘, PIL 기반 캔버스, 네이티브 투명 펫 창, LaunchAgent 자동 실행)
- `tray.py` 를 `IS_WIN`/`IS_MAC` 로 광범위하게 분기 (Tk 초기화 순서, 아이콘 실행 방식, 토큰 위치, 폰트, work area, 항상 위, 컨텍스트 메뉴, Pet 렌더링)
- `install.py` : 맥 전용 패키지, Homebrew Python 의 externally-managed 문제로 `.venv` 도입, `.app` 번들 생성, 훅 목록에서 `PostModelSwitch` 제거
- 새 설정 `tray_icons`(메뉴바 아이콘 표시 여부 토글) 추가, 이 맥의 `config.json` 에는 꺼둠
- `run.command`, `requirements.txt`(플랫폼 마커), `README.md`(윈도우/맥 공용 문구로 수정) 갱신
- 사용자 홈의 `~/.claude/settings.json` 훅 16개로 재등록, `~/.claude/worklog-config.json` projectMap 에 `claude-usage-tray` 추가

## 1. 배경
- 원래 윈도우 전용 파이썬 앱(tkinter + pystray + PIL). README 에 "macOS 로 포팅한다면" 가이드가 있었음. 사용자 요청: "이거 맥OS용으로 포팅해줘".
- 이 맥 환경: Homebrew python 3.14.7, `brew install python-tk@3.14` 로 Tk 9.0.4 설치(이 세션에서 설치). 시스템 python 3.9.6 은 Tk 8.5.9 라 미사용.

## 2. 새 파일

### `/Users/smithkim/Desktop/claude-usage-tray/macos.py`
AppKit(pyobjc) 글루 모듈.
- `hide_from_dock()` : NSApplication accessory 정책으로 Dock·Cmd-Tab 에서 숨김
- `activate_app()` : 설정창 포커스용 `activateIgnoringOtherApps_`
- `work_area()` : `NSScreen.screens()[0].visibleFrame` 을 Tk 좌표계(left, top, right, bottom)로 변환
- `set_floating(window)` : Tk 창을 제목으로 NSWindow 를 찾아 `setLevel_(NSFloatingWindowLevel)`. Tk 9 는 overrideredirect 창에 `-topmost` 가 안 먹는 것을 확인(설정 후 읽으면 0)해서 도입
- `Icon(pystray.Icon)` : 메뉴바 아이콘을 backingScaleFactor(2)배 픽셀로 그려 `setSize_` 로 22pt 로 맞춤 (pystray 기본은 22px 단일 rep 이라 레티나에서 흐림)
- `autostart_enabled()/autostart_set()` : `~/Library/LaunchAgents/com.claude-usage-tray.plist` 생성/삭제. `launchctl load` 는 RunAtLoad 로 즉시 두 번째 인스턴스가 뜨므로 하지 않음(다음 로그인부터 적용)
- `PilCanvas` : tk.Canvas 의 `delete/create_rectangle/create_polygon/create_text/round_rect/measure` 를 PIL 로 흉내. 2배 스케일. 글꼴은 `/System/Library/Fonts/AppleSDGothicNeo.ttc` 의 Bold 얼굴(ttc index 를 런타임에 스캔, 이 맥에선 index 6) — Arial 계열은 한글과 '·' 가 없어 tofu 로 나오는 걸 확인하고 교체
- `_PetView(NSView)` + `PetWindow` : 테두리·그림자 없는 투명 NSWindow(NSFloatingWindowLevel, CanJoinAllSpaces|Stationary). 좌클릭/우클릭/Ctrl+클릭을 Tk 화면 좌표로 콜백. `setMovable_(False)`, `setMovableByWindowBackground_(False)`, `mouseDragged_/mouseUp_` 무시 — 사용자가 "펫 누르고 옮기면 마우스에 다시 붙는다"고 보고해서 추가
- `FONT_CANDIDATES` (메뉴바 아이콘 숫자용): Arial Rounded Bold, Arial Bold, SFNSRounded, Verdana Bold

### `/Users/smithkim/Desktop/claude-usage-tray/run.command`
터미널과 함께 실행(debug.cmd 의 맥 버전). `.venv/bin/python` 있으면 그걸, 없으면 `python3`. chmod +x.

## 3. 수정한 파일

### `/Users/smithkim/Desktop/claude-usage-tray/tray.py`
- `IS_WIN`/`IS_MAC` 도입. `ctypes`/`wintypes` 는 윈도우에서만 import. `pywinstyles` 는 윈도우에서만 사용
- `App.__init__` 이 `tk.Tk()` 를 제일 먼저 만들도록 순서 변경 (AppKit 이 먼저 NSApp 을 만들면 Tk 초기화 중 `-[NSApplication macOSVersion]: unrecognized selector` 로 크래시하는 걸 실제로 겪음). `Widget` 은 `app.tk_root` 를 받아 씀. `App.tk_root` 를 `_on_tk` 의 기준으로 통일, `--no-widget` 도 맥에선 Tk 루프로 동작
- 맥에서 pystray 아이콘은 `run_detached(setup=lambda i: None)` 후 `visible` 설정, Tk mainloop 이 NSApp 루프를 돌림. `_apply()` 는 맥에서 메인 스레드가 아니면 `_on_tk` 로 재진입
- `quit()` 맥 경로: `icon.stop()` 대신 `visible=False` 후 `tk_root.destroy()`
- `_token()` : `~/.claude/.credentials.json` 이 없으면 맥에선 키체인 `security find-generic-password -s "Claude Code-credentials" -w` 에서 토큰 읽음 (맥 Claude Code 가 토큰을 키체인에 두는 것 확인)
- `_font()` 플랫폼별 경로, `UI_FONT` (맥 "Helvetica Neue"), `FONT_CHOICES` 맥용 목록, 말풍선 폰트 `UI_FONT`
- `work_area(window)` 로 교체(구 `work_area_bottom`). 맥 기본 위젯 위치: Dock 바로 위 오른쪽 구석(우측 -20, 하단 -12)
- `make_floating()` 헬퍼, `keep_on_top()` 맥 경로(2초 주기 `set_floating` 재확인), `TOPMOST_MS` 맥 2000
- `hide_from_taskbar()`/`window_hwnd()` 는 맥에서 no-op/0
- `bind_context_menu()` : 맥은 `<Button-2>`, `<Control-Button-1>` 도 우클릭으로 바인딩
- `Pet` : 맥에선 Tk Toplevel 대신 `macos.PetWindow` + `macos.PilCanvas`. `draw()` 끝에 `set_image`. `_measure`/`round_rect` 는 PIL 캔버스로 위임. `_popup_at()` 로 네이티브 우클릭 → Tk 메뉴. 프레임 타이머는 `app.tk_root.after`
- 자동 실행 메뉴 라벨 `AUTOSTART_LABEL` (맥 "로그인 시 자동 실행")
- 새 설정 `tray_icons`(기본 True): 위젯 우클릭 메뉴 "메뉴바 아이콘 표시"와 설정 › 표시 탭 스위치. `App.toggle_tray()/apply_tray_visibility()` 로 아이콘을 지우지 않고 `visible` 만 토글. 사용자 요청("이건 그냥 안 뜨게 할 수 있나")으로 추가했고 이 맥의 `config.json` 에 `tray_icons: false` 로 꺼둠
- `SettingsWindow.TAB_W, TAB_H` 를 맥에서 (560, 500) 으로 확대 — 상태 탭 12줄과 "유지" 열이 잘리던 문제(사용자 스크린샷으로 보고)
- 맥 `refresh_menus()` : pystray 맥 백엔드는 메뉴를 한 번만 만들어 체크 표시가 굳어서 토글 시 `update_menu()`
- `__main__` 의 DPI 호출을 `IS_WIN` 으로 가드

### `/Users/smithkim/Desktop/claude-usage-tray/install.py`
- `PACKAGES` 플랫폼별(맥: pyobjc-framework-Cocoa, 윈도우: pywinstyles)
- 맥은 Homebrew 파이썬이 pip 를 막아(externally-managed) 첫 설치가 조용히 실패했음 → 프로젝트 안 `.venv`(`--system-site-packages`)를 만들어 거기에 설치. `app_python()` 이 `.venv/bin/python`. pip 실패 시 메시지 출력
- `make_app_bundle()` : `Claude Usage Tray.app` (Contents/Info.plist 에 LSUIElement, MacOS/run 셸 스크립트가 `.venv` 파이썬으로 tray.py 실행)
- 훅 목록에서 `PostModelSwitch` 제거 — Claude Code 가 "Unknown hook event" 경고를 냄. 훅 재등록 결과 16개

### 기타
- `/Users/smithkim/Desktop/claude-usage-tray/requirements.txt` : `pywinstyles; sys_platform == "win32"`, `pyobjc-framework-Cocoa; sys_platform == "darwin"` 마커
- `/Users/smithkim/Desktop/claude-usage-tray/README.md` : 윈도우·맥 공용으로 문구 수정, 실행 표에 `.app`/`run.command`, 자동 실행 설명, 키체인 토큰, `.venv` 설명, 표시 탭에 트레이 아이콘 표시 여부, 상태 표에서 PostModelSwitch 줄 삭제, "macOS 로 포팅한다면" 섹션을 "macOS 에서 다른 점" 섹션(윈도우/맥 대응표 + Tk 9 주의점)으로 교체
- 사용자 홈의 `~/.claude/settings.json` : install.py 로 훅 16개 등록(백업 파일 자동 생성됨). `~/.claude/worklog-config.json` 의 projectMap 에 `~/Desktop/claude-usage-tray: claude-usage-tray` 추가

## 2. 확인 / 배포

- 실험 스크립트(scratchpad)로 확인: Tk 9.0.4 의 `-transparent` + `systemTransparent` 캔버스는 내용을 아예 안 그리거나(alpha 전부 0) 배경을 검게 칠함(alpha 전부 255) — 둘 다 재현. 그래서 캐릭터를 네이티브 창으로 감쌈
- `screencapture -l <windowNumber>` 로 창 단위 캡처해 확인: 네이티브 펫 창 alpha0=77671 / alpha255=6809 (배경 투명, 스프라이트 불투명), level=3, 위젯 창 level=3, 메뉴바 아이콘 22pt/44px rep, accessory 정책(1). NSEvent 합성으로 `mouseDown_` 호출 시 `hop=14` 로 클릭 반응 확인
- PIL 스프라이트 격자 틈 버그 수정(`create_rectangle` 이 확대 전에 1을 빼서 픽셀 사이 틈이 생겨 흰 배경에서 격자무늬로 보임 — 사용자 스크린샷으로 보고) 후 렌더 이미지로 확인
- 설정창 상태 탭을 띄워 캡처: 12줄 전부와 "유지" 열 표시 확인
- `python tray.py --once` : 키체인 토큰 읽기는 성공했으나 HTTP 401 — 키체인 토큰이 15시간 전 만료된 상태(expiresAt 확인). 포팅 버그 아님, Claude Code CLI 실행 시 갱신됨
- `open "Claude Usage Tray.app"` 으로 실제 실행, `pgrep` 으로 프로세스 확인. 여러 번 재시작하며 사용자가 직접 위젯·캐릭터·설정창 확인("잘나오는구만", "좋군")
- `py_compile` 로 tray.py/macos.py/install.py 컴파일 확인
- 윈도우 쪽 동작은 코드 경로만 유지했고 윈도우에서 실제 실행은 이 세션에서 하지 않음(미검증)
- 메뉴바 아이콘 클릭 메뉴, 캐릭터 우클릭 팝업, 설정창 키 입력은 실제 마우스·키보드 조작으로는 자동 검증하지 못함(코드 경로만 확인)

## 3. 마지막에 한 것
- 사용자 요청으로 펫 디자인 레퍼런스 웹 검색: CC0 16×16 팩 Tiny Creatures(https://clintbellanger.itch.io/tiny-creatures, 180개, 굵은 외곽선), itch.io CC0 16x16 태그, Desktop Pet 태그, claude-pet(https://github.com/xtrimsystems/claude-pet), OpenPets Shimeji 대안 글. 적용은 하지 않음
- 자동 실행(LaunchAgent)은 사용자가 켜보겠다고 했고 결과는 미확인

## 남은 것
- 펫 스킨 교체: 현재 `PET_BODY`/`PET_LEGS` 문자 격자 구조라 PNG 스프라이트 로딩 경로가 필요. 팩 선택 대기
- 키체인 토큰 만료 시 앱이 직접 refresh 하지는 않음(Claude Code 실행으로 갱신)
- 윈도우에서 회귀 확인 필요
- 로그인 자동 실행 동작 확인(다음 로그인)
