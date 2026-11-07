"""Stores each analysis as one JSON file, named with a random ID."""

import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from pydantic import ValidationError

from backend.schemas.analysis import AnalysisRecord

# Longer than any analysis can take, so a "running" analysis this old was cut off by a restart.
STALE_AFTER = timedelta(minutes=15)

# A saved, real analysis of the sample data. It's shown instantly and uses no AI requests.
EXAMPLE_ID = UUID("00000000-0000-4000-8000-00000000da7a")
EXAMPLE_FILE = Path(__file__).parents[2] / "sample_data" / "example_analysis.json"


def load_example() -> AnalysisRecord | None:
    try:
        return AnalysisRecord.model_validate_json(EXAMPLE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


class AnalysisStore:
    def __init__(self, data_dir: Path, ttl_hours: int) -> None:
        self.root = data_dir / "analyses"
        self.ttl = timedelta(hours=ttl_hours)

    def save(self, record: AnalysisRecord) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        record.updated_at = datetime.now(timezone.utc)
        # Replacing a file is all-or-nothing, so readers never see half-written data.
        temp = self.root / f".{record.analysis_id}.{uuid.uuid4().hex}.tmp"
        temp.write_text(record.model_dump_json(), encoding="utf-8")
        os.replace(temp, self._path(record.analysis_id))

    def get(self, analysis_id: UUID) -> AnalysisRecord | None:
        if analysis_id == EXAMPLE_ID:
            return load_example()
        try:
            record = AnalysisRecord.model_validate_json(self._path(analysis_id).read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            return None
        now = datetime.now(timezone.utc)
        if now - record.created_at > self.ttl:
            return None
        if record.status in ("queued", "running") and now - record.updated_at > STALE_AFTER:
            record.status = "failed"
            record.message = "This analysis was interrupted. Please ask again."
        return record

    def purge_expired(self) -> int:
        if not self.root.is_dir():
            return 0
        cutoff = datetime.now(timezone.utc).timestamp() - self.ttl.total_seconds()
        removed = 0
        for path in self.root.iterdir():
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _path(self, analysis_id: UUID) -> Path:
        return self.root / f"{analysis_id}.json"
