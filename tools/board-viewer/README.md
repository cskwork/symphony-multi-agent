# Symphony Board Viewer

Symphony multi-agent orchestrator(headless 모드)와 sync되는 정적 HTML 기반 Kanban 보드 뷰어.
브라우저에서 Symphony TUI를 모방하되, 기존 headless server와 동시에 떠도 race 없이 **read-only** 로 안전하게 동작한다.

## 실행

### 권장: Symphony service 로 함께 실행

```bash
symphony service start ./WORKFLOW.md --port 9999 --viewer-port 8765
symphony service status ./WORKFLOW.md
```

`symphony service`는 orchestrator와 이 board viewer를 같은 lifecycle로
관리합니다. 같은 `WORKFLOW.md`가 다른 포트에서 중복 실행되는 것도
run-state 파일로 막습니다.

### 가장 빠른 방법 (launcher 사용)

```bash
# 프로젝트 루트(./kanban 이 있는 디렉토리)에서:
/path/to/symphony-multi-agent/tools/board-viewer/board-viewer-open.sh
# 또는 dograh-demo 내부 사본:
./tools/board-viewer/board-viewer-open.sh
```

### kanban 디렉토리 직접 지정

```bash
./tools/board-viewer/board-viewer-open.sh /path/to/some/kanban
# 또는
python3 tools/board-viewer/server.py --kanban /path/to/some/kanban --port 8765
```

### 환경변수

- `BOARD_VIEWER_PORT` — 정적 서버 포트 (기본 8765)
- `BOARD_VIEWER_KANBAN_DIR` — kanban 경로 (CLI `--kanban` 이 우선)
- `SYMPHONY_BASE` — Symphony orchestrator URL (기본 `http://127.0.0.1:9999`)

### Kanban 경로 결정 우선순위

1. `--kanban DIR` CLI 인자
2. `BOARD_VIEWER_KANBAN_DIR` 환경변수
3. `$PWD/kanban` (현재 작업 디렉토리에 `kanban/`이 있으면)
4. `tools/board-viewer/../../kanban` (이 도구가 위치한 repo 내부 fallback)

## 무엇을 하는가

`server.py` 가 세 가지 역할을 동시에 수행:

1. 정적 파일 serving (`index.html`, `src/css/*`, `src/js/*`)
2. Symphony API 프록시 (read-only):
   - `GET /api/symphony/state` → `GET http://127.0.0.1:9999/api/v1/state`
   - `GET /api/symphony/<ID>` → `GET http://127.0.0.1:9999/api/v1/<ID>`
3. Kanban 파일 인덱스/원본:
   - `GET /api/kanban/index` — `kanban/*.md` 전체 frontmatter 인덱스 (JSON)
   - `GET /api/kanban/<ID>.md` — 단일 ticket 원본 .md

브라우저는 5초마다 인덱스 + Symphony state를 병렬 fetch하여 칸반을 갱신.

## 키보드 단축키

| 키 | 동작 |
|----|------|
| `r` | 즉시 새로고침 |
| `/` | 티켓 검색 (ID / title / label) |
| `j` `k` | 카드 포커스 이동 |
| `Enter` | 포커스된 카드 상세 열기 |
| `Esc` | 모달 닫기 / 검색 클리어 |
| `[` | UI 크기 줄이기 |
| `]` | UI 크기 키우기 |
| `\` | UI 크기 리셋 |

## UI 크기 조절

- 헤더 우측 `−` / `+` / `⟲` 버튼 또는 `[` / `]` / `\` 단축키
- 70% ~ 180% 범위 (5% 단위)
- localStorage(`boardViewer.uiZoom`)에 저장 — 재방문 시 자동 복원
- header / board / footer / 상세보기 모달이 모두 비례 확대/축소 (modal backdrop은 viewport 유지)

## 보안 / 안전성

- 모든 API 호출이 **GET** 한정. Symphony `POST /refresh|/pause|/resume` 같은 mutating endpoint는 **호출하지 않는다.**
- kanban/*.md 도 read 전용 (Write/Edit 없음).
- Symphony 가 down 이어도 file-based로 보드 동작 (상단 인디케이터 빨강).
- path traversal 차단 (정적 root 외부 / kanban_dir 외부 접근 거부).
- 마크다운 본문은 [DOMPurify](https://github.com/cure53/DOMPurify) 로 sanitize — kanban 본문이 외부 에이전트 prompt-injection 으로 오염돼도 XSS 차단.
- `<script>`, `<iframe>`, `on*=` 등은 화이트리스트에서 명시적으로 제외.

## 한계 / TODO

- frontmatter 파서가 단순 (nested dict 미지원). 현재 dograh-demo kanban 형식엔 충분.
- markdown rendering은 CDN의 `marked.js` + `DOMPurify` 사용. 오프라인에서는 본문이 `<pre>` 로 fallback.
- 가상 스크롤 없음 — ticket 100+ 시 약간 느려질 수 있음.
- WebSocket 미사용 (Symphony가 push를 제공하지 않으므로 5s polling).
- CSS `zoom` 속성은 Firefox에서 표준 지원이 약함 — Chrome/Safari/Edge 권장.

## 파일 구조

```
tools/board-viewer/
├── README.md
├── server.py                 # stdlib HTTP 서버 + Symphony 프록시 (Python 3.11+)
├── board-viewer-open.sh      # launcher (auto-discover kanban, venv-free)
├── index.html                # 진입점
├── src/
│   ├── css/style.css         # 다크 테마 + zoom + scrollbar
│   └── js/
│       ├── api.js            # fetch 래퍼
│       ├── board.js          # 칸반 + polling(setTimeout 재귀) + 키바인딩 + zoom
│       ├── ticket.js         # 카드/모달 렌더 + DOMPurify sanitize
│       └── utils.js          # el() (innerHTML 미지원, 안전 first), escapeHtml 등
└── screenshots/              # 시각 확인용 캡처
```

## Symphony 본체 설치 권장 위치

이 도구는 Symphony multi-agent의 기본 셋업 일부로 동봉할 것을 권장합니다.

```
symphony-multi-agent/
├── tui-open.sh
├── tui-open.bat
├── WORKFLOW.example.md
└── tools/
    └── board-viewer/   ← 이 디렉토리
```

새 프로젝트에 Symphony 부트스트랩 시 `tui-open.sh`와 함께 따라가도록 함께 복사하세요.
