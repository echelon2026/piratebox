"""
Structured command journal: one JSON object per line in logs/piratebox-YYYY-MM-DD.jsonl.
Every stage of every request is recorded with the same request_id, e.g.

  {"ts": "...", "request_id": "a1b2c3d4", "stage": "input", "source": "console", "text": "set bpm to 142"}
  {"ts": "...", "request_id": "a1b2c3d4", "stage": "interpreted", "command": {...}}
  {"ts": "...", "request_id": "a1b2c3d4", "stage": "result", "success": true, "status": "OK", ...}
"""

import json
import logging
import threading
from datetime import datetime
from pathlib import Path

log = logging.getLogger("piratebox.journal")


class Journal:
    def __init__(self, log_dir: Path):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def record(self, request_id: str, stage: str, **fields) -> None:
        now = datetime.now().astimezone()
        entry = {"ts": now.isoformat(timespec="milliseconds"), "request_id": request_id, "stage": stage, **fields}
        line = json.dumps(entry, default=str)
        log.info(line)
        path = self.log_dir / f"piratebox-{now:%Y-%m-%d}.jsonl"
        try:
            with self._lock, open(path, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            # Losing a log line must never take down command handling, but it must not be silent either.
            log.exception("could not write journal to %s", path)


class NullJournal(Journal):
    def __init__(self):
        self._lock = threading.Lock()

    def record(self, request_id, stage, **fields):
        pass
