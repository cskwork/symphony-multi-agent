"""SPEC §13 — structured logging with stable key=value phrasing."""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from typing import Any, TextIO


_SENSITIVE_KEYS = {"api_key", "token", "authorization", "secret", "password"}


def _redact(value: Any) -> Any:
    if isinstance(value, str) and len(value) > 8:
        return value[:2] + "***" + value[-2:]
    return "***"


def _format_pair(key: str, value: Any) -> str:
    if key.lower() in _SENSITIVE_KEYS:
        value = _redact(value)
    if isinstance(value, str):
        if any(c in value for c in " \t\"="):
            return f'{key}={json.dumps(value, ensure_ascii=False)}'
        return f"{key}={value}"
    if value is None:
        return f"{key}=null"
    if isinstance(value, bool):
        return f"{key}={'true' if value else 'false'}"
    if isinstance(value, (int, float)):
        return f"{key}={value}"
    return f'{key}={json.dumps(value, ensure_ascii=False, default=str)}'


class StructuredLogger:
    """Stable key=value structured logger. §13.1, §13.2."""

    def __init__(self, name: str = "symphony", streams: list[TextIO] | None = None) -> None:
        self.name = name
        self._streams = streams or [sys.stderr]
        self._lock = threading.Lock()
        self._level = logging.INFO

    def set_level(self, level: int) -> None:
        self._level = level

    def add_stream(self, stream: TextIO) -> None:
        self._streams.append(stream)

    def _emit(self, level: int, level_name: str, message: str, **fields: Any) -> None:
        if level < self._level:
            return
        ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        head = f"ts={ts} level={level_name} logger={self.name} message={json.dumps(message, ensure_ascii=False)}"
        tail = " ".join(_format_pair(k, v) for k, v in fields.items())
        line = head + ((" " + tail) if tail else "") + "\n"
        with self._lock:
            for stream in list(self._streams):
                try:
                    stream.write(line)
                    stream.flush()
                except Exception:
                    # §13.2: a sink failure must not crash the service.
                    if stream in self._streams:
                        try:
                            self._streams.remove(stream)
                        except ValueError:
                            pass

    def debug(self, message: str, **fields: Any) -> None:
        self._emit(logging.DEBUG, "DEBUG", message, **fields)

    def info(self, message: str, **fields: Any) -> None:
        self._emit(logging.INFO, "INFO", message, **fields)

    def warning(self, message: str, **fields: Any) -> None:
        self._emit(logging.WARNING, "WARN", message, **fields)

    def error(self, message: str, **fields: Any) -> None:
        self._emit(logging.ERROR, "ERROR", message, **fields)


_default = StructuredLogger()


def get_logger() -> StructuredLogger:
    return _default


def configure_logging(level: str | None = None) -> StructuredLogger:
    if level is None:
        level = os.environ.get("SYMPHONY_LOG_LEVEL", "INFO")
    _default.set_level(getattr(logging, level.upper(), logging.INFO))
    return _default
