"""Counts AI requests per day, so the free daily limit can't be used up by accident or abuse."""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class DailyLimitReached(Exception):
    """Today's AI requests have run out."""


class UsageTracker:
    _lock = threading.Lock()

    def __init__(self, data_dir: Path, daily_limit: int) -> None:
        self.path = data_dir / "ai_usage.json"
        self.daily_limit = daily_limit

    def count_request(self) -> None:
        """Record one request, or raise DailyLimitReached if none are left today."""
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            used = self._read().get(today, 0)
            if used >= self.daily_limit:
                raise DailyLimitReached(f"The limit of {self.daily_limit} AI requests a day has been reached.")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps({today: used + 1}), encoding="utf-8")

    def _read(self) -> dict[str, int]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
