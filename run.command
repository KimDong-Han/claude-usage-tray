#!/bin/sh
# 터미널과 함께 실행. 오류 확인용 (debug.cmd 의 맥 버전).
# 터미널 없이 띄우려면 install.py 가 만든 'Claude Usage Tray.app' 을 쓴다.
cd "$(dirname "$0")"
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
exec "$PY" tray.py "$@"
