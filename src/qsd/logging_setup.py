"""JSON-lines logging with secret redaction applied to every record (spec §13, §15, §133)."""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

# Header names / key names whose values must never reach logs.
_SENSITIVE_KEYS = (
    r"(authorization|cookie|set-cookie|x-api-key|api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"session[_-]?id|csrf[_-]?token|x-csrf-token|password|secret)"
)
_KEY_VALUE = re.compile(rf"(?i)(\"?{_SENSITIVE_KEYS}\"?\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;&]+)")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*")
_KNOWN_KEY_SHAPES = re.compile(r"\b(sk-[A-Za-z0-9_\-]{16,}|sk-ant-[A-Za-z0-9_\-]{16,}|AKIA[0-9A-Z]{16})\b")
REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    text = _BEARER.sub(f"Bearer {REDACTED}", text)
    text = _KEY_VALUE.sub(lambda m: f"{m.group(1)}{REDACTED}", text)
    return _KNOWN_KEY_SHAPES.sub(REDACTED, text)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        for key in ("stage", "source_id", "handler", "campaign_id"):
            if hasattr(record, key):
                payload[key] = redact(str(getattr(record, key)))
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def setup_logging(log_dir: Path | None = None, level: int = logging.INFO) -> logging.Logger:
    root = logging.getLogger("qsd")
    root.setLevel(level)
    root.handlers.clear()
    stream = logging.StreamHandler()
    stream.setFormatter(JsonFormatter())
    root.addHandler(stream)
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(log_dir / "qsd.jsonl", encoding="utf-8")
        fh.setFormatter(JsonFormatter())
        root.addHandler(fh)
    root.propagate = False
    return root
