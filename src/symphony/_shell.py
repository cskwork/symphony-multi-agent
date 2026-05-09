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

import os
import shutil
import sys
from functools import lru_cache


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
    """
    override = os.environ.get("SYMPHONY_BASH")
    if override:
        return override

    if sys.platform != "win32":
        return "bash"

    for candidate in _WIN_GIT_BASH_CANDIDATES:
        if os.path.isfile(candidate):
            return candidate

    found = shutil.which("bash")
    if found and not _is_wsl_launcher(found):
        return found

    # Last resort: PATH-based lookup, even if it points at WSL — keeps the
    # behavior predictable rather than silently failing at spawn time.
    return "bash"
