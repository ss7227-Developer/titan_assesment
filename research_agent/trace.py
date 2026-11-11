"""Local JSONL events. Never dump model messages or thinking tokens."""

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .runtime import Runtime


def redact(value):
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if re.search(
            r"password|secret|token|api[_-]?key|authorization|cookie", key, re.I
        ) else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"\b(?:sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{12,})",
                       "[REDACTED]", value)
        value = re.sub(r"(?i)\b(bearer\s+)\S+", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)\b(password|api[_-]?key|secret|token)\s*[:=]\s*[^\s,;]+",
                       r"\1=[REDACTED]", value)
        # Remove terminal control sequences from user/provider-controlled text.
        return re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", value)
    return value


class Trace:
    def __init__(self, directory: Path, runtime: Runtime):
        self.runtime = runtime
        self.run_id = uuid.uuid4().hex
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"{self.run_id}.jsonl"
        self.file = self.path.open("x", encoding="utf-8")
        self.path.chmod(0o600)
        self.sequence = 0
        self.finished = False

    def event(self, event: str, node: str = "", **data):
        self.sequence += 1
        record = {"schema_version": 1, "run_id": self.run_id,
                  "sequence": self.sequence,
                  "timestamp": datetime.now(timezone.utc).isoformat(),
                  "elapsed_ms": round(self.runtime.elapsed * 1000, 2),
                  "event": event, "node": node,
                  "counters": self.runtime.counters(), **data}
        self.file.write(json.dumps(redact(record), ensure_ascii=False) + "\n")
        self.file.flush()

    def finish(self, reason: str, exit_status: int):
        if not self.finished:
            self.event("run_end", termination_reason=reason, exit_status=exit_status)
            self.finished = True

    def close(self):
        self.file.close()
