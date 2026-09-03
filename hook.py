#!/usr/bin/env python3
"""Claude Code 훅에서 호출되는 상태 기록기.

    python hook.py <state> [delta]

  state  앱의 STATE_DEFS 와 맞춘 상태 이름
  delta  서브에이전트 카운터 — '+' 증가 / '-' 감소 / '0' 초기화 / 생략 시 유지

트레이 앱이 이 파일을 300ms 마다 들여다보고 캐릭터 반응과 마릿수를 정한다.
아는 상태 이름인지는 앱이 판단한다. 훅은 Claude Code 를 붙잡으면 안 되므로
하는 일을 최소로 유지한다.
"""
import json
import re
import sys
import time
from pathlib import Path

STATE_FILE = Path.home() / ".claude" / "usage-tray-agent-state.json"
MAX_AGENTS = 16  # 훅을 놓쳐 카운터가 새더라도 끝없이 늘지 않게

state = sys.argv[1] if len(sys.argv) > 1 else "idle"
delta = sys.argv[2] if len(sys.argv) > 2 else None

if re.fullmatch(r"[a-z_]{1,32}", state):
    current = 0
    try:
        current = int(json.loads(STATE_FILE.read_text(encoding="utf-8")).get("agents", 0))
    except (OSError, ValueError, TypeError):
        pass

    if delta == "+":
        agents = min(MAX_AGENTS, current + 1)
    elif delta == "-":
        agents = max(0, current - 1)
    elif delta == "0":
        agents = 0
    else:
        agents = max(0, min(MAX_AGENTS, current))

    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(
            json.dumps({"state": state, "at": time.time(), "agents": agents}),
            encoding="utf-8")
    except OSError:
        pass
