"""Usage limits, so the free AI allowance and the server can't be used up by accident or abuse."""

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# An analysis usually takes 3 AI requests: plan and code, a repair, and the explanation.
REQUESTS_PER_ANALYSIS = 3


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

    def remaining(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock:
            return max(self.daily_limit - self._read().get(today, 0), 0)

    def _read(self) -> dict[str, int]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}


def next_reset() -> datetime:
    """The daily allowance resets at midnight UTC."""
    tomorrow = datetime.now(timezone.utc).date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=timezone.utc)


class VisitorLimitReached(Exception):
    """This visitor has used their share for now. The message is safe to show."""


class VisitorLimiter:
    """Allows each visitor a limited number of actions an hour.

    Visitors are identified by their network address, which is stored only as a hash.
    People sharing a network, such as an office, share a limit.
    """

    _lock = threading.Lock()
    WINDOW_SECONDS = 3600

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "visitor_usage.json"

    def record(self, visitor: str, action: str, hourly_limit: int) -> None:
        key = f"{action}:{hashlib.sha256(visitor.encode()).hexdigest()[:16]}"
        now = time.time()
        with self._lock:
            data = self._read()
            # Forget anything older than an hour.
            data = {k: [t for t in v if now - t < self.WINDOW_SECONDS] for k, v in data.items()}
            data = {k: v for k, v in data.items() if v}
            recent = data.get(key, [])
            if len(recent) >= hourly_limit:
                minutes = max(1, round((self.WINDOW_SECONDS - (now - min(recent))) / 60))
                raise VisitorLimitReached(
                    f"You've reached the limit of {hourly_limit} an hour. Please try again in about {minutes} minutes."
                )
            data[key] = recent + [now]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(data), encoding="utf-8")

    def _read(self) -> dict[str, list[float]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
