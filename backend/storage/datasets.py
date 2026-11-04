"""Stores datasets on disk, each in its own folder named with a random ID.

The user's filename is never used as a path. It is only kept as a label to display,
so a malicious name like "../../etc/passwd" can't put a file somewhere it shouldn't go.
"""

import json
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

from backend.schemas.datasets import DatasetInfo, DatasetProfile

CSV_FILENAME = "data.csv"
INFO_FILENAME = "info.json"


def clean_display_name(filename: str | None) -> str:
    """Make an uploaded filename safe to show: no folders or control characters."""
    name = (filename or "dataset.csv").replace("\\", "/").rsplit("/", 1)[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:200] or "dataset.csv"


class DatasetStore:
    def __init__(self, data_dir: Path, ttl_hours: int) -> None:
        self.root = data_dir / "datasets"
        self.ttl = timedelta(hours=ttl_hours)

    def save(self, filename: str, csv_text: str, profile: DatasetProfile) -> DatasetInfo:
        dataset_id = uuid4()
        now = datetime.now(timezone.utc)
        data = csv_text.encode("utf-8")
        info = DatasetInfo(
            dataset_id=dataset_id,
            filename=filename,
            size_bytes=len(data),
            created_at=now,
            expires_at=now + self.ttl,
            profile=profile,
        )

        # Write to a temporary folder, then rename it. Renaming is all-or-nothing,
        # so a crash can't leave a half-saved dataset behind.
        self.root.mkdir(parents=True, exist_ok=True)
        temp_dir = self.root / f".incoming-{dataset_id}"
        temp_dir.mkdir(mode=0o700)
        try:
            (temp_dir / CSV_FILENAME).write_bytes(data)
            (temp_dir / INFO_FILENAME).write_text(info.model_dump_json(), encoding="utf-8")
            os.rename(temp_dir, self.root / str(dataset_id))
        except OSError:
            shutil.rmtree(temp_dir, ignore_errors=True)
            raise
        return info

    def get(self, dataset_id: UUID) -> DatasetInfo | None:
        """Return a dataset's details, or None if it doesn't exist or has expired."""
        info = self._read_info(self.root / str(dataset_id))
        if info is None or info.expires_at <= datetime.now(timezone.utc):
            return None
        return info

    def read_csv(self, dataset_id: UUID) -> str:
        return (self.root / str(dataset_id) / CSV_FILENAME).read_text(encoding="utf-8")

    def delete(self, dataset_id: UUID) -> bool:
        folder = self.root / str(dataset_id)
        if not folder.is_dir():
            return False
        shutil.rmtree(folder)
        return True

    def purge_expired(self) -> int:
        """Delete expired datasets and abandoned partial uploads. Returns how many."""
        if not self.root.is_dir():
            return 0
        now = datetime.now(timezone.utc)
        removed = 0
        for folder in self.root.iterdir():
            info = self._read_info(folder)
            if info is None:
                # Only a partial upload that's old enough, in case one is in progress now.
                modified = datetime.fromtimestamp(folder.stat().st_mtime, timezone.utc)
                expired = now - modified > self.ttl
            else:
                expired = info.expires_at <= now
            if expired:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        return removed

    @staticmethod
    def _read_info(folder: Path) -> DatasetInfo | None:
        try:
            return DatasetInfo.model_validate(json.loads((folder / INFO_FILENAME).read_text("utf-8")))
        except (OSError, ValueError):
            return None
