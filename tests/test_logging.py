"""SPEC §17.6 — structured logging behavior."""

from __future__ import annotations

import io

from symphony.logging import StructuredLogger


def test_key_value_format():
    buf = io.StringIO()
    log = StructuredLogger(streams=[buf])
    log.info("dispatch", issue_id="abc", issue_identifier="MT-1", attempt=2)
    line = buf.getvalue()
    assert "level=INFO" in line
    assert "message=\"dispatch\"" in line
    assert "issue_id=abc" in line
    assert "issue_identifier=MT-1" in line
    assert "attempt=2" in line


def test_logger_redacts_sensitive_keys():
    buf = io.StringIO()
    log = StructuredLogger(streams=[buf])
    log.info("event", api_key="lin_abcdef1234567890")
    line = buf.getvalue()
    assert "lin_abcdef1234567890" not in line
    assert "api_key=" in line


def test_failed_sink_does_not_crash():
    class Bad(io.StringIO):
        def write(self, _value):
            raise OSError("disk full")

    bad = Bad()
    good = io.StringIO()
    log = StructuredLogger(streams=[bad, good])
    log.info("event", k=1)
    log.info("event2", k=2)
    # Good sink still receives output even after bad sink fails.
    assert "event2" in good.getvalue()
