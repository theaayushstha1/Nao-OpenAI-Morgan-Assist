# -*- coding: utf-8 -*-
"""Structured JSONL logger for NAO-side runtime (Python 2.7 compatible).

Why this exists: Phase 1 of the v2 rework needs structured per-turn telemetry
on the robot itself, not just the server. structlog is Python 3.6+ only, so
we hand-roll a tiny logger here that emits one JSON line per event with a
consistent key shape and supports `bind()` for context propagation
(component, user, session_id, turn_idx, ...).

Log location: ~/nao_assist/logs/nao_<YYYY-MM-DD>.jsonl
Rotation: 50 MB cap, 5 backups (per docs/PHASE_1_TASK_MAP.md).
Thread-safe through the underlying logging.handlers lock.

No external deps. Stdlib only — naoqi runtime ships logging + json + threading.
"""
from __future__ import print_function

import json
import logging
import os
import threading
import time
import traceback
from logging.handlers import RotatingFileHandler


_LOG_DIR = os.path.expanduser("~/nao_assist/logs")
_lock = threading.Lock()
_configured = False


def _ensure_dir():
    """Best-effort mkdir; first writer wins, EEXIST is fine."""
    try:
        if not os.path.isdir(_LOG_DIR):
            os.makedirs(_LOG_DIR)
    except OSError:
        # Race with another writer or permission issue; let the
        # FileHandler raise on open() if it really can't write.
        pass


class _JsonFormatter(logging.Formatter):
    """Render LogRecord as a single-line JSON document.

    Root keys (always present): ts, level, event, msg.
    Optional: any keys passed via the `extras` dict on the record (set by
    _BoundLogger), exc trace if exc_info is attached.
    """

    def format(self, record):
        rec = {
            "ts": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)
            ),
            "level": record.levelname.lower(),
            "event": getattr(record, "event", record.getMessage()),
            "msg": record.getMessage(),
        }
        extras = getattr(record, "extras", None)
        if isinstance(extras, dict):
            # Caller-bound context wins over the auto-derived event field
            # since the caller passed it explicitly with a value.
            for k, v in extras.items():
                rec[k] = v
        if record.exc_info:
            try:
                rec["exc"] = "".join(
                    traceback.format_exception(*record.exc_info)
                )
            except Exception:
                rec["exc"] = "<unformattable traceback>"
        try:
            return json.dumps(rec, default=_json_fallback)
        except Exception:
            # Last-ditch — never let logging crash the runtime.
            return json.dumps(
                {"ts": rec["ts"], "level": rec["level"],
                 "event": rec["event"], "msg": "<unserializable record>"}
            )


def _json_fallback(o):
    """json.dumps default= hook for objects that aren't JSON-serializable.

    NAO code passes naoqi proxy objects, sets, custom dicts, etc. into log
    contexts. We coerce to str rather than raise so a single bad key doesn't
    swallow an entire log line.
    """
    try:
        return str(o)
    except Exception:
        return "<unrepr>"


def configure_logger(level="INFO"):
    """Idempotent setup. Call once at process start (main.py does this).

    Subsequent calls are no-ops so library code can safely call it too.
    """
    global _configured
    with _lock:
        if _configured:
            return
        _ensure_dir()
        log_path = os.path.join(
            _LOG_DIR, "nao_" + time.strftime("%Y-%m-%d") + ".jsonl"
        )
        try:
            handler = RotatingFileHandler(
                log_path, maxBytes=50 * 1024 * 1024, backupCount=5
            )
        except (IOError, OSError) as exc:
            # Fall back to stderr-only logging so the robot can still boot
            # even if /home/nao is read-only or the disk is full.
            handler = logging.StreamHandler()
            handler.setFormatter(_JsonFormatter())
            root = logging.getLogger("nao")
            root.setLevel(_level_const(level))
            root.handlers = [handler]
            root.propagate = False
            _configured = True
            try:
                root.warning("logger_fallback_stderr: " + str(exc))
            except Exception:
                pass
            return
        handler.setFormatter(_JsonFormatter())
        root = logging.getLogger("nao")
        root.setLevel(_level_const(level))
        root.handlers = [handler]
        root.propagate = False
        _configured = True


def _level_const(level):
    """Resolve a level name string to a logging constant; default INFO."""
    if isinstance(level, int):
        return level
    name = str(level or "INFO").upper()
    return getattr(logging, name, logging.INFO)


class _BoundLogger(object):
    """Lightweight context-bound logger.

    Mirrors the structlog/zerolog `bind()` pattern: each `.bind(**ctx)`
    returns a new logger that prepends those keys to every subsequent
    record. Works on Python 2.7 and 3.x. Methods `info/warn/error/debug`
    accept a string event tag plus arbitrary keyword fields.
    """

    __slots__ = ("_base", "_ctx")

    def __init__(self, base, ctx):
        self._base = base
        self._ctx = ctx

    def bind(self, **kwargs):
        new_ctx = dict(self._ctx)
        new_ctx.update(kwargs)
        return _BoundLogger(self._base, new_ctx)

    def _log(self, level, event, **kwargs):
        extras = dict(self._ctx)
        extras.update(kwargs)
        extras["event"] = event
        # We pass the event as the LogRecord message so basic stdlib tooling
        # still gets a readable text. The JSON formatter promotes `extras`
        # to top-level keys.
        try:
            self._base.log(level, event, extra={"extras": extras})
        except Exception:
            # Logging must never raise into business logic.
            try:
                self._base.log(level, event)
            except Exception:
                pass

    def info(self, event, **kw):
        self._log(logging.INFO, event, **kw)

    def warn(self, event, **kw):
        self._log(logging.WARNING, event, **kw)

    # Alias to match stdlib naming convention (warn is deprecated in py3)
    warning = warn

    def error(self, event, **kw):
        self._log(logging.ERROR, event, **kw)

    def debug(self, event, **kw):
        self._log(logging.DEBUG, event, **kw)

    def exception(self, event, **kw):
        """Log error with current exception info attached."""
        extras = dict(self._ctx)
        extras.update(kw)
        extras["event"] = event
        try:
            self._base.error(event, exc_info=True, extra={"extras": extras})
        except Exception:
            pass


def get_logger(**ctx):
    """Return a context-bound logger. Auto-configures if main forgot to."""
    if not _configured:
        configure_logger()
    return _BoundLogger(logging.getLogger("nao"), ctx)


if __name__ == "__main__":
    # Hand-run smoke test: prove a JSON line lands in the rotating log.
    # `python nao/logger.py` produces output you can `tail -f`.
    configure_logger(level="DEBUG")
    log = get_logger(component="logger_smoke").bind(user="test", turn_idx=1)
    log.info(
        "turn_complete",
        phase_ms={"vad": 12, "stt": 184, "agent_first_token": 380},
        transcript="hello world",
        outcome="ok",
    )
    log.warn("vad_floor_high", energy_floor=420)
    try:
        raise RuntimeError("synthetic error for smoke test")
    except RuntimeError:
        log.exception("turn_error", outcome="rejected")
    log_path = os.path.join(
        _LOG_DIR, "nao_" + time.strftime("%Y-%m-%d") + ".jsonl"
    )
    print("Wrote sample log lines to:", log_path)
    try:
        with open(log_path, "r") as fh:
            tail = fh.readlines()[-3:]
        for line in tail:
            print("  ", line.rstrip())
    except IOError as exc:
        print("Could not read back log file:", exc)
