#!/usr/bin/env bash
# tui-open.sh — launch Symphony's Textual Kanban TUI in a new terminal window.
#
# Usage:
#   ./tui-open.sh [path/to/WORKFLOW.md]
#
# - Defaults to ./WORKFLOW.md in the script's directory.
# - Prefers the project venv at .venv/bin/symphony; falls back to PATH.
#   `pip install -e .` pulls textual automatically as a runtime dep — if
#   the launcher errors with `ModuleNotFoundError: textual`, your active
#   environment is stale; re-run `pip install -e .` inside .venv.
# - Runs `symphony --tui WORKFLOW.md`, which starts both the orchestrator
#   AND the TUI in a single process (the server port from WORKFLOW.md's
#   `server.port` is also exposed).
# - Quit the TUI with `q` from inside the app (drains workers cleanly) or
#   close the spawned terminal window. Ctrl-C also works.
#
# Platform behaviour:
#   macOS                   -> opens a new iTerm.app window if installed,
#                              otherwise Terminal.app, via a .command launcher
#   Linux + $TERMINAL set   -> spawns $TERMINAL -e ...
#   Linux + DISPLAY         -> tries gnome-terminal, konsole, xterm in order
#   anywhere else           -> runs in current shell (foreground)
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKFLOW="${1:-${SCRIPT_DIR}/WORKFLOW.md}"

if [[ ! -f "${WORKFLOW}" ]]; then
  echo "tui-open: workflow not found: ${WORKFLOW}" >&2
  exit 2
fi

if [[ -x "${SCRIPT_DIR}/.venv/bin/symphony" ]]; then
  SYMPHONY="${SCRIPT_DIR}/.venv/bin/symphony"
elif command -v symphony >/dev/null 2>&1; then
  SYMPHONY="$(command -v symphony)"
else
  echo "tui-open: 'symphony' not found on PATH and no .venv/bin/symphony" >&2
  echo "tui-open: install with: python3.11 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 3
fi

# Preflight in current terminal so the user sees doctor output before the
# TUI repaints the screen.
echo "tui-open: running doctor preflight..."
"${SYMPHONY}" doctor "${WORKFLOW}" || {
  echo "tui-open: doctor reported FAIL — aborting launch" >&2
  exit 4
}

# The actual command we want a new terminal to run.
LAUNCH_CMD="cd '${SCRIPT_DIR}' && '${SYMPHONY}' --tui '${WORKFLOW}'"

case "$(uname -s)" in
  Darwin)
    # macOS: write a .command launcher and `open` it in a NEW window.
    # Prefer iTerm if installed; fall back to Terminal.app. Both apps
    # register as handlers for .command files and ALWAYS open them in
    # a new window — unlike `osascript do script`, which silently adds
    # a tab to the frontmost existing window (often invisible behind
    # another Space or minimised).
    LAUNCHER="${SCRIPT_DIR}/.tui-launcher.command"
    cat >"${LAUNCHER}" <<EOF
#!/usr/bin/env bash
cd "${SCRIPT_DIR}"
exec "${SYMPHONY}" --tui "${WORKFLOW}"
EOF
    chmod +x "${LAUNCHER}"
    if [[ -d "/Applications/iTerm.app" ]] || mdfind "kMDItemCFBundleIdentifier == 'com.googlecode.iterm2'" 2>/dev/null | grep -q iTerm; then
      TERMINAL_APP="iTerm"
    else
      TERMINAL_APP="Terminal"
    fi
    echo "tui-open: opening new ${TERMINAL_APP} window via ${LAUNCHER}..."
    open -a "${TERMINAL_APP}" "${LAUNCHER}"
    ;;
  Linux)
    if [[ -n "${TERMINAL:-}" ]]; then
      "${TERMINAL}" -e bash -lc "${LAUNCH_CMD}; exec bash" &
    elif command -v gnome-terminal >/dev/null 2>&1; then
      gnome-terminal -- bash -lc "${LAUNCH_CMD}; exec bash" &
    elif command -v konsole >/dev/null 2>&1; then
      konsole -e bash -lc "${LAUNCH_CMD}; exec bash" &
    elif command -v xterm >/dev/null 2>&1; then
      xterm -e bash -lc "${LAUNCH_CMD}; exec bash" &
    else
      echo "tui-open: no known terminal emulator found, running foreground" >&2
      exec bash -lc "${LAUNCH_CMD}"
    fi
    ;;
  *)
    echo "tui-open: unknown OS '$(uname -s)', running foreground" >&2
    exec bash -lc "${LAUNCH_CMD}"
    ;;
esac

echo "tui-open: launched. Headless logs (if any) tail with:"
echo "  tail -F '${SCRIPT_DIR}/log/symphony.log'"
