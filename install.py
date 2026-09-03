#!/usr/bin/env python3
"""새 PC 세팅용. 의존성 설치 + Claude Code 훅 등록.

    python install.py            의존성 설치 + 훅 등록
    python install.py --hooks    훅만 등록
    python install.py --deps     의존성만 설치
    python install.py --remove   등록한 훅 제거

훅 경로는 이 파일 위치에서 뽑으므로, 폴더를 어디에 두든 그대로 동작한다.
기존 훅(다른 도구가 등록한 것)은 건드리지 않는다.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
HOOK_SCRIPT = str(BASE_DIR / "hook.py")
SETTINGS = Path.home() / ".claude" / "settings.json"
IS_MAC = sys.platform == "darwin"
PACKAGES = ["pystray", "pillow", "sv-ttk"] + (
    ["pyobjc-framework-Cocoa"] if IS_MAC else ["pywinstyles"])
APP_BUNDLE = BASE_DIR / "Claude Usage Tray.app"
VENV = BASE_DIR / ".venv"   # 맥: Homebrew 파이썬은 pip 를 막아서(externally-managed) 여기에 깐다

NOTIFICATION_MATCHER = "permission_prompt|elicitation_dialog|agent_needs_input"
# (이벤트, 매처, 상태, 서브에이전트 카운터 증감)
WIRING = [
    ("SessionStart",       "",                   "session_start",     "0"),
    ("UserPromptSubmit",   "",                   "working",           "0"),
    ("PreToolUse",         "",                   "working",           ""),
    ("SubagentStart",      "",                   "subagent",          "+"),
    ("SubagentStop",       "",                   "working",           "-"),
    ("PostCompact",        "",                   "working",           ""),
    ("Notification",       NOTIFICATION_MATCHER, "needs_input",       ""),
    ("PermissionRequest",  "",                   "needs_input",       ""),
    ("Elicitation",        "",                   "needs_input",       ""),
    ("PermissionDenied",   "",                   "permission_denied", ""),
    ("PostToolUseFailure", "",                   "tool_failed",       ""),
    ("TaskCompleted",      "",                   "task_done",         ""),
    ("PreCompact",         "",                   "compacting",        ""),
    ("Stop",               "",                   "done",              "0"),
    ("StopFailure",        "",                   "failed",            "0"),
    ("SessionEnd",         "",                   "idle",              "0"),
]


def app_python() -> Path:
    """앱과 훅이 쓸 파이썬. 맥은 프로젝트 안의 .venv, 윈도우는 지금 파이썬."""
    if IS_MAC:
        return VENV / "bin" / "python"
    return Path(sys.executable)


def runner() -> str:
    """훅이 쓸 파이썬. 윈도우는 콘솔 창이 안 뜨는 pythonw 를 우선한다. 맥은 그런 게 없다."""
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    return str(pythonw if pythonw.exists() else app_python())


def make_app_bundle() -> None:
    """맥용 더블클릭 실행기. 터미널 없이 뜨고 Dock 에도 안 잡히는 최소 .app 번들.

    파이썬 경로를 박아두므로 파이썬을 바꾸면 install.py 를 다시 돌린다.
    """
    macos_dir = APP_BUNDLE / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True, exist_ok=True)
    launcher = macos_dir / "run"
    launcher.write_text(
        "#!/bin/sh\n"
        f"cd {shlex_quote(str(BASE_DIR))}\n"
        f"exec {shlex_quote(str(app_python()))} tray.py \"$@\"\n")
    launcher.chmod(0o755)
    (APP_BUNDLE / "Contents" / "Info.plist").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0"><dict>\n'
        '  <key>CFBundleName</key><string>Claude Usage Tray</string>\n'
        '  <key>CFBundleIdentifier</key><string>com.claude-usage-tray</string>\n'
        '  <key>CFBundleExecutable</key><string>run</string>\n'
        '  <key>CFBundlePackageType</key><string>APPL</string>\n'
        '  <key>LSUIElement</key><true/>\n'
        '</dict></plist>\n')
    print("앱 번들:", APP_BUNDLE.name)


def shlex_quote(text: str) -> str:
    import shlex
    return shlex.quote(text)


def install_deps() -> None:
    print("의존성 설치:", ", ".join(PACKAGES))
    python = sys.executable
    if IS_MAC:
        if not app_python().exists():
            # 시스템 tkinter 를 그대로 쓰도록 --system-site-packages
            subprocess.run([sys.executable, "-m", "venv", "--system-site-packages", str(VENV)], check=True)
        python = str(app_python())
        print("가상환경:", VENV)
    result = subprocess.run([python, "-m", "pip", "install", "-q", *PACKAGES], check=False)
    if result.returncode:
        print("! pip 설치 실패. 위 오류를 확인하세요")


def is_ours(hook: dict) -> bool:
    return (hook.get("args") or [])[:1] == [HOOK_SCRIPT]


def strip_ours(hooks: dict) -> int:
    """이미 등록된 우리 항목만 걷어낸다. 재실행해도 중복이 안 쌓인다."""
    removed = 0
    for event in list(hooks):
        kept = []
        for entry in hooks[event]:
            inner = [h for h in entry.get("hooks", []) if not is_ours(h)]
            removed += len(entry.get("hooks", [])) - len(inner)
            if inner:
                kept.append({**entry, "hooks": inner})
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    return removed


def write_hooks(remove_only: bool = False) -> None:
    if not SETTINGS.exists():
        print(f"! {SETTINGS} 가 없습니다. Claude Code 를 한 번 실행한 뒤 다시 시도하세요.")
        return

    backup = SETTINGS.with_suffix(f".json.bak-usage-tray-{time.strftime('%Y%m%d%H%M%S')}")
    shutil.copy2(SETTINGS, backup)
    print("백업:", backup.name)

    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    hooks = data.setdefault("hooks", {})
    print("기존 항목 제거:", strip_ours(hooks), "개")

    if not remove_only:
        python = runner()
        for event, matcher, state, delta in WIRING:
            args = [HOOK_SCRIPT, state] + ([delta] if delta else [])
            hooks.setdefault(event, []).append({
                "matcher": matcher,
                "hooks": [{"type": "command", "command": python,
                           "args": args, "async": True, "timeout": 5}],
            })
        print("등록:", len(WIRING), "개")

    SETTINGS.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print("완료:", SETTINGS)


if __name__ == "__main__":
    args = set(sys.argv[1:])
    if "--remove" in args:
        write_hooks(remove_only=True)
    else:
        if "--hooks" not in args:
            install_deps()
        if "--deps" not in args:
            write_hooks()
        if IS_MAC:
            make_app_bundle()
            print(f"\n실행: '{APP_BUNDLE.name}' 을 더블클릭하세요 (터미널 없이 시작)")
            print("문제가 있으면 run.command 로 터미널과 함께 실행해 오류를 확인하세요")
        else:
            print("\n실행: run.vbs 를 더블클릭하세요 (콘솔 없이 시작)")
            print("문제가 있으면 debug.cmd 로 콘솔과 함께 실행해 오류를 확인하세요")
