"""Shell resolution — pick a bash binary that can actually run Symphony's hooks.

On Windows, ``where bash`` often returns:

    C:\\Windows\\System32\\bash.exe          # WSL launcher
    C:\\Program Files\\Git\\usr\\bin\\bash.exe   # Git Bash (MSYS)
    C:\\Users\\<u>\\AppData\\Local\\Microsoft\\WindowsApps\\bash.exe   # WSL alias

The WSL launcher is the wrong choice for Symphony: WSL mounts Windows drives
at ``/mnt/c/...`` (not ``/c/...``), can't transparently invoke Windows ``.exe``
files in user hooks, and runs in a separate Linux filesystem from the
workspace cwd. We want MSYS ``bash`` (Git Bash), which speaks ``/c/...``,
inherits the Windows cwd verbatim, and runs Windows binaries directly.

This helper centralizes the lookup so every hook + backend uses the same
binary. Set ``SYMPHONY_BASH`` to override.
"""

from __future__ import annotations

import asyncio
import errno
import os
import shutil
import sys
from functools import lru_cache
from typing import Any


# Common Git for Windows install locations. Scoop and Winget installs land
# under ``%USERPROFILE%\scoop\apps\git\current\bin\bash.exe`` and
# ``%LOCALAPPDATA%\Programs\Git\bin\bash.exe`` respectively — those are
# picked up by the ``shutil.which`` fallback below if the user's PATH is set
# correctly. Extend this tuple if you need to support those installs without
# requiring PATH config.
_WIN_GIT_BASH_CANDIDATES = (
    r"C:\Program Files\Git\bin\bash.exe",
    r"C:\Program Files\Git\usr\bin\bash.exe",
    r"C:\Program Files (x86)\Git\bin\bash.exe",
    r"C:\Program Files (x86)\Git\usr\bin\bash.exe",
)

_WSL_LAUNCHER_FRAGMENTS = (
    r"\windows\system32\bash.exe",
    r"\microsoft\windowsapps\bash.exe",
)


def _is_wsl_launcher(path: str) -> bool:
    p = path.lower()
    return any(frag in p for frag in _WSL_LAUNCHER_FRAGMENTS)


@lru_cache(maxsize=1)
def resolve_bash() -> str:
    """Return the bash executable to use for hooks and backend subprocesses.

    Result is cached for the process lifetime — set ``SYMPHONY_BASH`` before
    the first call (typically before importing symphony) if you need to
    override. Tests that toggle the env var mid-process must call
    ``resolve_bash.cache_clear()`` between toggles.

    A ``SYMPHONY_BASH`` override pointing at the Windows WSL launcher is
    rejected (the whole reason this helper exists is to avoid that binary);
    we fall through to the default detection so a misconfigured override
    can't silently re-introduce the bug. Other override values are returned
    as-is and validated by ``doctor.check_shell``.
    """
    override = os.environ.get("SYMPHONY_BASH")
    if override:
        if _is_wsl_launcher(override):
            # Misconfiguration — fall through to default detection rather
            # than honor a value we know will not work for hooks.
            pass
        else:
            return override

    if sys.platform != "win32":
        return "bash"

    for candidate in _WIN_GIT_BASH_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate

    found = shutil.which("bash")
    if found and not _is_wsl_launcher(found):
        return found

    # Last resort: bare ``bash`` so spawn-time error is reproducible from
    # the doctor's ``check_shell`` failure (instead of failing at the first
    # hook dispatch with an opaque ``FileNotFoundError``).
    return "bash"


async def safe_proc_wait(proc: Any, *, timeout: float | None = None) -> int | None:
    """Reap an asyncio subprocess without depending on the child watcher.

    Background: Python 3.12 + asyncio + Textual on macOS sometimes leaves the
    child watcher unable to observe SIGCHLD for processes spawned via
    ``asyncio.create_subprocess_exec``. The visible symptom is a zombie
    ``<defunct>`` child plus an ``await proc.wait()`` that never returns.

    Workaround: do the wait in a worker thread via ``os.waitpid``. The thread
    blocks in the kernel until the child exits — independent of any asyncio
    watcher state — and yields back to the loop the moment the kernel hands
    over the exit status.

    `timeout` is in seconds. Returns the exit code, or ``None`` on timeout
    (caller is responsible for sending SIGKILL and retrying).
    """
    pid = proc.pid
    if pid is None:
        return None
    if proc.returncode is not None:
        return proc.returncode

    def _blocking_wait() -> int | None:
        try:
            _, status = os.waitpid(pid, 0)
        except ChildProcessError:
            # Already reaped (asyncio watcher won the race, or never
            # registered). Either way, nothing more to do.
            return None
        except OSError as exc:
            if exc.errno == errno.ECHILD:
                return None
            raise
        if os.WIFEXITED(status):
            return os.WEXITSTATUS(status)
        if os.WIFSIGNALED(status):
            return -os.WTERMSIG(status)
        return None

    if timeout is None:
        return await asyncio.to_thread(_blocking_wait)
    try:
        return await asyncio.wait_for(asyncio.to_thread(_blocking_wait), timeout=timeout)
    except asyncio.TimeoutError:
        return None
