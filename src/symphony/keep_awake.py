"""Prevent the host OS from sleeping while Symphony is running.

macOS ships ``caffeinate`` which holds a `kIOPMAssertionTypePreventUserIdleDisplaySleep`
assertion for the duration of the child process. We launch it with
``-w <pid>`` so the assertion is released the moment the Symphony process
exits — even on SIGKILL — without our own cleanup having to run. Other
platforms are no-ops; if a Linux/Windows equivalent is needed later, plug
the backend selection in :func:`_spawn`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .logging import get_logger

log = get_logger()

_CAFFEINATE_TERMINATE_TIMEOUT_S = 2.0


class KeepAwake:
    """Hold a wake-lock for the lifetime of the orchestrator.

    The wake-lock is engaged when :meth:`start` succeeds and released when
    :meth:`stop` runs (or, on macOS, when the watched pid disappears for
    any reason — kill -9, panic, etc.). Calling :meth:`start` on a non-mac
    host or when ``caffeinate`` is missing logs the reason and returns
    without raising; the caller treats the absence of a wake-lock as a
    soft warning, never a fatal error.
    """

    def __init__(self, *, watch_pid: int | None = None) -> None:
        self._watch_pid = watch_pid if watch_pid is not None else os.getpid()
        self._proc: subprocess.Popen[bytes] | None = None

    @property
    def active(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> bool:
        """Engage the wake-lock. Returns True iff a lock was actually taken."""
        if self._proc is not None:
            return self.active
        if sys.platform != "darwin":
            log.info(
                "keep_awake_skipped",
                reason="unsupported_platform",
                platform=sys.platform,
            )
            return False
        binary = shutil.which("caffeinate")
        if binary is None:
            log.warning(
                "keep_awake_skipped",
                reason="caffeinate_missing",
                hint="install Xcode CLT or check PATH",
            )
            return False
        try:
            self._proc = subprocess.Popen(
                [binary, "-d", "-i", "-w", str(self._watch_pid)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            log.warning(
                "keep_awake_spawn_failed",
                error=str(exc),
                binary=binary,
            )
            self._proc = None
            return False
        log.info(
            "keep_awake_active",
            pid=self._proc.pid,
            watch_pid=self._watch_pid,
            flags="-d -i",
        )
        return True

    def stop(self) -> None:
        """Release the wake-lock. Safe to call multiple times."""
        proc = self._proc
        if proc is None:
            return
        self._proc = None
        if proc.poll() is not None:
            return
        try:
            proc.terminate()
        except OSError as exc:
            log.warning("keep_awake_terminate_failed", error=str(exc))
            return
        try:
            proc.wait(timeout=_CAFFEINATE_TERMINATE_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
        log.info("keep_awake_released")

    def __enter__(self) -> "KeepAwake":
        self.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.stop()
