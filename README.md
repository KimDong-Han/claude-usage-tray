# Claude 사용량 표시기

Claude Code 의 사용량(5시간 세션 · 주간 한도)을 화면 구석에 띄워두고, Claude Code 가
지금 뭘 하고 있는지 픽셀 캐릭터로 보여주는 작은 도구. 윈도우와 macOS 에서 같은 코드로 돈다.

```
       (  ᴗ )                     ← 캐릭터가 위젯 위를 걸어다닌다
 5H  76%  2h02m  ·  7D  88%  2d3h  ← 떠 있는 위젯
```

- **위젯** — 남은 % 와 리셋까지 남은 시간. 항상 위에 떠 있고 드래그로 옮긴다
- **캐릭터** — 위젯 윗변을 걸어다니고, 클릭하면 폴짝 뛰며 말풍선을 띄운다.
  Claude Code 가 권한을 물으면 계속 뛰고, 작업 중엔 안전모에 망치를 들고, 에러가 나면 눈이 X 가 된다.
  서브에이전트가 늘면 마릿수도 는다
- **트레이 · 메뉴바 아이콘** — 원형=5시간, 사각형=주간, 숫자는 남은 %. 필요 없으면 끌 수 있다

사용량은 로컬 로그를 세는 추정치가 아니라 Claude 서버가 주는 실제 값이다.

> *English:* a floating usage widget for Claude Code (5-hour / weekly limits) with a pixel pet that
> reacts to Claude Code hook events. Windows and macOS, one codebase. Python 3.10+.

## 설치

파이썬 3.10 이상. 폴더를 원하는 곳에 두고 한 번:

```
python install.py
```

- 의존성을 설치한다 — `pystray` `pillow` `sv-ttk`, 윈도우는 `pywinstyles`, 맥은 `pyobjc-framework-Cocoa`
- Claude Code 훅을 `~/.claude/settings.json` 에 등록한다. 다른 도구가 등록한 훅은 건드리지 않고,
  실행 전에 백업을 남긴다. 훅 경로는 이 폴더 위치에서 뽑으므로 폴더를 어디 둬도 된다
- 맥은 Homebrew 파이썬이 pip 를 막으므로 폴더 안 `.venv` 에 의존성을 깔고, 더블클릭용
  `Claude Usage Tray.app` 을 만든다

| 명령 | 하는 일 |
| --- | --- |
| `python install.py` | 의존성 설치 + 훅 등록 (+ 맥은 앱 번들) |
| `python install.py --deps` | 의존성만 |
| `python install.py --hooks` | 훅만 |
| `python install.py --remove` | 등록한 훅 제거 |

맥에서 `tkinter` 가 없다고 하면 `brew install python-tk` 를 먼저 한다.

## 실행

| | 윈도우 | 맥 |
| --- | --- | --- |
| 평소 | `run.vbs` 더블클릭 (콘솔 없음) | `Claude Usage Tray.app` 더블클릭 (터미널 없음) |
| 오류 확인 | `debug.cmd` | `run.command` |

터미널에서 바로 보려면 `python tray.py --once`, 위젯 없이 아이콘만 쓰려면 `python tray.py --no-widget`.

**자동 시작**은 위젯 우클릭 메뉴에 있다. 윈도우는 시작프로그램 폴더에 `.vbs` 를, 맥은
`~/Library/LaunchAgents` 에 plist 를 넣는다. 켜짐/꺼짐은 그 파일의 존재 여부로 판단하고,
맥은 다음 로그인부터 적용된다.

## 설정

위젯이나 트레이 아이콘 우클릭 → 설정. `config.json` 에 저장된다. 예시는 `config.example.json`.

- **색상** — 배경 · 라벨 · 남은 시간 · 여유/주의/경고. 프리셋 5종(클레이·다크·라이트·네온·모노)
- **표시** — 글꼴, 크기, 투명도, 항상 맨 위, 트레이(메뉴바) 아이콘 표시 여부
- **동작** — 조회 주기, 주의·경고 임계값, 자동 실행
- **캐릭터** — 표시 여부, 크기, 걷는 속도, 최대 마릿수, 몸 색(잔량 따라 / 직접 지정)
- **말풍선** — 클릭할 때만 / 항상 표시, 문구 4칸
- **상태** — 아래 상태마다 문구 · 동작 · 유지시간

문구에는 치환자를 쓸 수 있다: `{5h}` `{7d}` 는 남은 %, `{5h_reset}` `{7d_reset}` 은 리셋까지 남은 시간.

## 캐릭터가 반응하는 상태

`hook.py` 가 Claude Code 훅 이벤트를 받아 `~/.claude/usage-tray-agent-state.json` 에 상태를
적고, 앱이 300ms 마다 그 파일을 본다. 훅은 Claude Code 를 붙잡으면 안 되므로 하는 일이 그게 전부다.

| 상태 | 훅 이벤트 | 기본 동작 |
| --- | --- | --- |
| 세션 시작 | SessionStart | 한 번 점프 |
| 작업 중 | UserPromptSubmit · PreToolUse · SubagentStop · PostCompact | 안전모+망치 |
| 입력 필요 | Notification · PermissionRequest · Elicitation | 계속 점프 |
| 권한 거부 | PermissionDenied | 없음 |
| 도구 실패 | PostToolUseFailure | 눈이 X |
| 서브에이전트 | SubagentStart | 안전모+망치 (마릿수 +1) |
| 작업 하나 완료 | TaskCompleted | 한 번 점프 |
| 대화 압축 | PreCompact | 안전모+망치 |
| 전체 완료 | Stop | 한 번 점프 |
| 중단 · 실패 | StopFailure | 눈이 X |
| 평상시 | SessionEnd | 없음 |

수동으로 확인하려면 `python hook.py needs_input` 처럼 상태 이름을 직접 넣어 본다.
`SubagentStart` 가 마릿수를 올리고 `SubagentStop` 이 내리며, 턴이 끝나면(`Stop`) 0 으로 돌아간다.

## 사용량은 어디서 오나

Claude Code 가 저장해 둔 OAuth 토큰 — 윈도우는 `~/.claude/.credentials.json`, 맥은 키체인의
`Claude Code-credentials` — 으로 `https://api.anthropic.com/api/oauth/usage` 를 부른다.
토큰은 호출할 때마다 다시 읽으므로 Claude Code 가 갱신해도 앱을 재시작할 필요가 없다.
기본 5분 주기, 실패하면 직전 값을 유지한다.

Claude Code 를 한동안 안 쓰면 토큰이 만료된 채 남아 `?` 가 뜰 수 있다. Claude Code 를
한 번 실행하면 갱신된다. 이 앱은 토큰을 어디로도 보내지 않고 위 주소만 부른다.

## 파일

| 파일 | 역할 |
| --- | --- |
| `tray.py` | 앱 본체 — 위젯 · 캐릭터 · 트레이 아이콘 · 설정창 |
| `macos.py` | 맥 전용 글루 (AppKit). 윈도우에선 불러오지 않는다 |
| `hook.py` | 훅에서 호출되는 상태 기록기 |
| `install.py` | 의존성 · 훅 · 맥 앱 번들 |
| `run.vbs` `debug.cmd` | 윈도우 실행기 |
| `run.command` | 맥 실행기 (터미널) |
| `requirements.txt` | 의존성. 플랫폼 마커로 윈도우·맥 분기 |
| `config.example.json` | 설정 예시 (클레이 프리셋). 실제 설정은 `config.json` 에 저장되고 git 에는 안 올라간다 |
| `log_docs/` | 작업 로그 |

## 플랫폼 메모

플랫폼에 묶인 코드는 `macos.py` 와 `tray.py` 의 `IS_WIN` / `IS_MAC` 분기가 전부다.
사용량 조회, 상태 기계, 스프라이트, 설정 구조는 공통이다.

| 윈도우 | 맥 |
| --- | --- |
| 작업표시줄 · Alt+Tab 에서 창 숨김 (`WS_EX_TOOLWINDOW`) | 앱을 accessory 정책으로 띄워 Dock · Cmd-Tab 에 안 나온다 |
| `SetWindowPos(HWND_TOPMOST)` 로 최상위 재확보 | Tk 9 는 테두리 없는 창에 `-topmost` 가 안 먹는다. 창 제목으로 NSWindow 를 찾아 `level` 을 올린다 |
| 작업표시줄 위에 위젯 배치 | Dock 바로 위 오른쪽 구석 (`NSScreen.visibleFrame`) |
| 캐릭터 창은 `-transparentcolor` 키 컬러 | Tk 9 의 `-transparent` 는 캔버스를 못 그린다. AppKit 투명 창에 PIL 로 그린 프레임을 얹는다 |
| 트레이 아이콘은 스레드마다 `icon.run()` | 메뉴바 아이콘은 메인 스레드 NSApp 루프를 타야 해서 `run_detached()` 후 Tk `mainloop` 이 돌린다. 레티나용으로 2배로 그린다 |
| 우클릭 = `<Button-3>` | `<Button-2>` 와 Ctrl+클릭도 우클릭 |

- 맥의 Tk 는 자기가 만든 `NSApplication` 서브클래스를 기대한다. AppKit 을 Tk 보다 먼저
  건드리면 초기화 중에 죽으므로 `tk.Tk()` 를 제일 먼저 만든다
- 캐릭터 그림은 직접 그린 것이다. 다른 곳의 에셋을 쓰지 않았다
- 설정창 안의 위젯은 전부 ttk 다. classic Tk 위젯은 `ttk.Notebook` 탭 안에서 안 그려지는 환경이 있다
- `sv-ttk` 와 `pywinstyles` 가 없어도 동작한다. 설정창 외형만 Tk 기본으로 떨어진다
- 윈도우 11 + Python 3.12, macOS (Tk 9.0) + Python 3.14 에서 확인했다

## 라이선스

MIT
