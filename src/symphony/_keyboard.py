"""Cross-platform raw-mode keyboard polling for the TUI.

Why this exists: the Kanban TUI uses `rich.live.Live(screen=True)` which
takes over the alternate screen buffer for flicker-free renders. To accept
keyboard input *inside the app* (not terminal scrollback), we need to read
stdin in raw mode. Windows uses `msvcrt` and Unix uses `termios + select`.

Returned key tokens are normalized to short stable strings so the TUI
handler can match without caring about platform: ``"j"``, ``"DOWN"``,
``"PGDN"``, ``"q"`` …
"""

from __future__ import annotations

import contextlib
import sys
from typing import Iterator

# Platform detection without importing both modules eagerly.
_IS_WINDOWS = sys.platform.startswith("win")

if _IS_WINDOWS:
    import msvcrt  # type: ignore[import-not-found]
else:
    import select
    import termios
    import tty


# Windows scancodes after the \x00 / \xe0 prefix from msvcrt.getwch().
_WIN_SPECIAL = {
    "H": "UP",
    "P": "DOWN",
    "K": "LEFT",
    "M": "RIGHT",
    "I": "PGUP",
    "Q": "PGDN",
    "G": "HOME",
    "O": "END",
}

# Unix CSI sequences after ESC '['.
_UNIX_CSI = {
    "A": "UP",
    "B": "DOWN",
    "D": "LEFT",
    "C": "RIGHT",
    "5~": "PGUP",
    "6~": "PGDN",
    "H": "HOME",
    "F": "END",
}


@contextlib.contextmanager
def raw_mode() -> Iterator[None]:
    """Put stdin in raw mode for the duration of the context."""
    if _IS_WINDOWS:
        # msvcrt.getwch reads without needing termios setup.
        yield
        return
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def poll_key() -> str | None:
    """Non-blocking read of a single key event. Returns normalized token or None.

    Tokens: single printable char ('j', 'q', ...), or one of:
    'UP', 'DOWN', 'LEFT', 'RIGHT', 'PGUP', 'PGDN', 'HOME', 'END', 'ESC',
    'ENTER', 'CTRL_C'.
    """
    if _IS_WINDOWS:
        return _poll_windows()
    return _poll_unix()


def _poll_windows() -> str | None:
    if not msvcrt.kbhit():
        return None
    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        # Two-byte extended scancode.
        if not msvcrt.kbhit():
            return None
        scan = msvcrt.getwch()
        return _WIN_SPECIAL.get(scan, None)
    return _normalize_simple(ch)


def _poll_unix() -> str | None:
    r, _, _ = select.select([sys.stdin], [], [], 0)
    if not r:
        return None
    ch = sys.stdin.read(1)
    if ch == "\x1b":
        # Possible CSI escape sequence. Peek with short timeout for the rest.
        r2, _, _ = select.select([sys.stdin], [], [], 0.01)
        if not r2:
            return "ESC"
        bracket = sys.stdin.read(1)
        if bracket != "[":
            return "ESC"
        # Read up to 2 more chars (covers '5~', '6~', 'A'..'F').
        rest = sys.stdin.read(1)
        if rest in ("5", "6"):
            tilde = sys.stdin.read(1)
            return _UNIX_CSI.get(rest + tilde, None)
        return _UNIX_CSI.get(rest, None)
    return _normalize_simple(ch)


def _normalize_simple(ch: str) -> str:
    if ch == "\r" or ch == "\n":
        return "ENTER"
    if ch == "\x03":
        return "CTRL_C"
    if ch == "\x1b":
        return "ESC"
    return ch
