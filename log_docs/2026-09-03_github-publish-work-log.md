# Claude 사용량 표시기 작업 로그 — 2026-09-03 · GitHub 공개

> 이전: `2026-09-03_macos-port-work-log.md`(macOS 포팅 — AppKit 글루, 네이티브 캐릭터 창, .venv + .app 번들).
> 이 세션: 공개 전 정리(민감 경로 제거, README/LICENSE/.gitignore/config.example.json 정비) 후 git 저장소로 만들어 GitHub에 퍼블릭으로 올렸고, 서브에이전트로 폴더 구조를 검토했다.

---

## 0. 한눈에
- `run.vbs` 에서 하드코딩된 개인 윈도우 경로 제거, README 전면 재작성, LICENSE(MIT) 추가, `.gitignore` 신규, `config.example.json` 실제 설정 키에 맞춰 재생성
- `git init -b main`, 커밋 2개(`58786c9`, `3a8c4db`), remote `origin` 연결 후 `git push -u origin main` → `https://github.com/KimDong-Han/claude-usage-tray.git` 퍼블릭 공개
- 서브에이전트로 폴더 구조 유지보수성 검토(읽기 전용) — 현재 규모에선 문제없다는 결론, 즉시 반영/향후 과제 지적 사항 반영
- 사용자 질문에 답해 Vercel GitHub 앱 알림 동작을 설명(직접 조치 없음, 배포 여부 미확인)

## 1. 배경
- 사용자 요청: "커밋도 하자, README 잘 써봐, 퍼블릭으로 올려버리자" → 이후 "에이전트 써서 폴더 구조가 유지보수 가능한지 확인해 달라", "구조 검토 끝나고 올리자"
- 앞선 로그(macOS 포팅)는 `/worklog` 로 이미 기록되어 있었음 (`log_docs/2026-09-03_macos-port-work-log.md`)

## 2. 공개 전 정리
- `/Users/smithkim/Desktop/claude-usage-tray/run.vbs`: 하드코딩된 개인 윈도우 경로 `C:\Users\hr_of\AppData\Local\Programs\Python\Python312\pythonw.exe` 제거, `pythonw.exe`(PATH)만 사용하도록 수정
- `/Users/smithkim/Desktop/claude-usage-tray/.gitignore` 신규 작성: `config.json`, `widget-state.json`, `__pycache__/`, `*.pyc`, `.venv/`, `Claude Usage Tray.app/`(install.py 가 기기별 파이썬 경로를 박아 생성하므로), `.DS_Store`
- `/Users/smithkim/Desktop/claude-usage-tray/README.md` 전면 재작성: 소개 + 영어 한 줄 요약, 설치(윈도우/맥 공통 + 맥 `.venv`/`.app` 설명, `brew install python-tk`), 실행 표(윈도우/맥 열), 자동 시작, 설정, 캐릭터가 반응하는 상태 표(훅 11종), 사용량 출처(윈도우 credentials 파일 / 맥 키체인, 토큰 만료 시 `?` 안내, 토큰은 외부로 보내지 않음), 파일 표, 플랫폼 메모(윈도우/맥 대응표 + Tk 9 주의점), 확인한 환경(윈도우 11 + Python 3.12, macOS Tk 9.0 + Python 3.14). 이후 "## 라이선스" 절(MIT)과 파일 표에 `requirements.txt`, `config.example.json`, `log_docs/` 행 추가
- `/Users/smithkim/Desktop/claude-usage-tray/LICENSE` 신규: MIT, `Copyright (c) 2026 KimDong-Han`(GitHub 계정명 기준, 사용자가 MIT 선택)
- `/Users/smithkim/Desktop/claude-usage-tray/config.example.json`: 실제 설정 키와 어긋나던 항목(`tray_icons`, `pet_max` 없음, 말풍선 문구가 개인 문구 "Superbin")을 코드의 `tray.DEFAULTS` 에서 생성(x/y 제외, 클레이 프리셋 색, `pet_color_mode: fixed`)해 24개 키로 교체

## 3. git / GitHub 공개
- `git init -b main`(원래 git 저장소가 아니었음)
- 커밋 1 `58786c9` "Claude 사용량 표시기 — 윈도우 + macOS" — 전체 파일 첫 커밋
- 커밋 2 `3a8c4db` "LICENSE(MIT) 추가, 설정 예시를 현재 키에 맞춤, README 파일 표 보강"
- remote `origin` = `https://github.com/KimDong-Han/claude-usage-tray.git`(사용자가 GitHub 웹에서 빈 퍼블릭 레포를 만들어 URL 제공). `gh` CLI 는 이 맥에 없어 사용하지 않음
- `git push -u origin main` 성공 → 퍼블릭 공개됨. 두 커밋 모두 `Co-Authored-By: Claude Fable 5.1` 트레일러 포함

## 4. 폴더 구조 검토 (서브에이전트, 읽기 전용)
- 첫 시도는 API 529 Overloaded 로 중단, 재시도해서 완료
- 결론: 현재 규모(약 2500줄)에선 유지보수 가능, 당장 쪼갤 필요 없음
- 즉시 반영한 지적: LICENSE 미커밋 상태였음 → 커밋 2에 포함 / `config.example.json` 키 불일치 및 개인 문구 노출 → 재생성 / README 파일 표 누락 → 보강
- 나중 과제로 남긴 지적: `tray.py` 1926줄 단일 파일 — `# ----` 섹션 경계(설정 103 / 데이터 수집 180 / 값 다듬기 227 / 아이콘 319 / 자동 시작 373 / 화면 좌표 406 / 설정창 484 / 위젯 981 / 캐릭터 1156 / 앱 1582)를 따라 `config.py` `usage_api.py` `settings_window.py` `widget.py` `pet.py` `app.py` 로 쪼갤 수 있음; 윈도우 전용 ctypes 호출이 tray.py 8곳에 흩어져 있어 `macos.py` 와 비대칭 — 쪼갤 때 `windows.py` 로 짝 맞추기; README 에 스크린샷 없음; 네 클래스가 전역 `CONFIG` 를 공유해 한 클래스만 읽어선 흐름 파악이 어려움
- `.gitignore` 는 문제 없음으로 판정

## 5. 확인 / 배포
- `git push` 성공으로 GitHub 퍼블릭 공개 확인
- README·LICENSE·.gitignore·config.example.json 은 커밋에 포함된 것을 커밋 로그로 확인
- 사용자가 Vercel 이 이 레포를 봤다고 함 → Vercel GitHub 앱이 "모든 저장소" 권한이면 알림만 오고, 프로젝트로 import 하지 않은 레포는 배포되지 않는다고 설명. Vercel 은 건드리지 않았고 배포 여부는 확인하지 않음(미확인)
- `~/.claude/worklog-config.json` projectMap 에 `~/Desktop/claude-usage-tray: claude-usage-tray` 는 앞 세션에서 이미 추가됨

## 남은 것
- README 스크린샷/GIF 추가
- `tray.py` 분할 + `windows.py` 대칭화(규모가 커지면)
- 펫 스킨 교체(CC0 팩, PNG 스프라이트 로딩 경로 필요) — 앞 세션에서 넘어온 항목
- Vercel 대시보드에 프로젝트가 생겼는지 사용자 확인
