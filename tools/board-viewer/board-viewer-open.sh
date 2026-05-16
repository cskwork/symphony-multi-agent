#!/usr/bin/env bash
# board-viewer-open.sh
#
# Symphony Board Viewer launcher.
# 어디서 호출되든 자동으로 kanban 디렉토리를 발견하고 정적 서버를 띄운다.
#
# 사용법:
#   ./board-viewer-open.sh                       # CWD/kanban 자동 탐지
#   ./board-viewer-open.sh /path/to/kanban       # kanban 디렉토리 직접 지정
#   BOARD_VIEWER_PORT=9000 ./board-viewer-open.sh  # 포트 변경
#
# 환경변수:
#   BOARD_VIEWER_PORT        — 정적 서버 포트 (기본 8765)
#   BOARD_VIEWER_KANBAN_DIR  — kanban 경로 (CLI 인자가 우선)
#   SYMPHONY_BASE            — Symphony orchestrator URL (기본 http://127.0.0.1:9999)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
SERVER_PY="$HERE/server.py"

if [ ! -f "$SERVER_PY" ]; then
  echo "error: $SERVER_PY 가 없습니다." >&2
  exit 1
fi

# kanban 경로 결정 (인자 > env > CWD/kanban)
KANBAN_ARG="${1:-${BOARD_VIEWER_KANBAN_DIR:-}}"
if [ -z "$KANBAN_ARG" ]; then
  if [ -d "$PWD/kanban" ]; then
    KANBAN_ARG="$PWD/kanban"
  else
    echo "usage: $0 [/path/to/kanban]" >&2
    echo "  또는 ./kanban 가 있는 디렉토리에서 실행하세요." >&2
    exit 2
  fi
fi

if [ ! -d "$KANBAN_ARG" ]; then
  echo "error: '$KANBAN_ARG' is not a directory" >&2
  exit 3
fi

# Python 선택 (3.11+)
PYTHON=""
for c in python3.11 python3.12 python3.13 python3 python; do
  if command -v "$c" >/dev/null 2>&1; then
    PYTHON="$c"
    break
  fi
done
if [ -z "$PYTHON" ]; then
  echo "error: python3 (3.11+) 가 PATH 에 없습니다." >&2
  exit 4
fi

exec "$PYTHON" "$SERVER_PY" --kanban "$KANBAN_ARG"
