"""Usage limits, so the free AI allowances and the server can't be used up by accident or abuse."""

import hashlib
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# An analysis usually takes 3 AI requests: plan and code, a repair, and the explanation.
REQUESTS_PER_ANALYSIS = 3


class DailyLimitReached(Exception):
    """Today's free AI allowance has run out. The message is safe to show."""


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


class UsageTracker:
    """Counts AI requests and tokens per provider per day, in one small JSON file.

    File format: {"2026-11-07": {"groq": {"requests": 3, "tokens": 9000}}}
    """

    _lock = threading.Lock()

    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "ai_usage.json"

    def used(self, provider: str) -> tuple[int, int]:
        """Today's (requests, tokens) for a provider."""
        with self._lock:
            entry = self._today_entry().get(provider, {})
        return int(entry.get("requests", 0)), int(entry.get("tokens", 0))

    def record_request(self, provider: str) -> None:
        self._add(provider, "requests", 1)

    def record_tokens(self, provider: str, tokens: int) -> None:
        if tokens > 0:
            self._add(provider, "tokens", tokens)

    def _add(self, provider: str, field: str, amount: int) -> None:
        today = _today()
        with self._lock:
            entry = self._today_entry()
            counts = entry.setdefault(provider, {"requests": 0, "tokens": 0})
            counts[field] = int(counts.get(field, 0)) + amount
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Only today is kept, so the file never grows.
            self.path.write_text(json.dumps({today: entry}), encoding="utf-8")

    def _today_entry(self) -> dict[str, dict[str, int]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        entry = data.get(_today()) if isinstance(data, dict) else None
        # Files from before providers were tracked held a plain request count.
        return entry if isinstance(entry, dict) else {}


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
        key = self._key(visitor, action)
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

    def release(self, visitor: str, action: str) -> None:
        """Give back the most recent use, for example when a question was refused and nothing ran."""
        key = self._key(visitor, action)
        with self._lock:
            data = self._read()
            if data.get(key):
                data[key] = sorted(data[key])[:-1]
                self.path.write_text(json.dumps(data), encoding="utf-8")

    @staticmethod
    def _key(visitor: str, action: str) -> str:
        return f"{action}:{hashlib.sha256(visitor.encode()).hexdigest()[:16]}"

    def _read(self) -> dict[str, list[float]]:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
