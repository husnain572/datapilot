"""Endpoints for uploading, viewing, and deleting datasets."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response, UploadFile, status

from backend.config import Settings, get_settings
from backend.schemas.datasets import DatasetInfo, DatasetPreview
from backend.services.csv_validation import CsvValidationError, validate_csv
from backend.services.profiling import load_dataframe, profile_dataframe, records
from backend.storage.datasets import DatasetStore, clean_display_name

router = APIRouter(prefix="/api/v1/datasets", tags=["datasets"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_store(settings: SettingsDep) -> DatasetStore:
    return DatasetStore(settings.data_dir, settings.dataset_ttl_hours)


StoreDep = Annotated[DatasetStore, Depends(get_store)]


def _require_dataset(store: DatasetStore, dataset_id: UUID) -> DatasetInfo:
    info = store.get(dataset_id)
    if info is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found. It may have expired.")
    return info


@router.post("", status_code=status.HTTP_201_CREATED)
def upload_dataset(file: UploadFile, settings: SettingsDep, store: StoreDep) -> DatasetInfo:
    """Upload a CSV. It's validated and profiled, and stored for a limited time."""
    filename = clean_display_name(file.filename)
    if not filename.lower().endswith(".csv"):
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Please upload a .csv file.")

    # Read one byte past the limit, which tells us the file is too big without reading it all.
    raw = file.file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        limit_mb = settings.max_upload_bytes // (1024 * 1024)
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE, f"The file is larger than the {limit_mb} MB limit."
        )

    try:
        validated = validate_csv(raw, settings.max_rows, settings.max_columns)
    except CsvValidationError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(error)) from error

    store.purge_expired()
    profile = profile_dataframe(load_dataframe(validated.text))
    return store.save(filename, validated.text, profile)


@router.get("/{dataset_id}")
def get_dataset(dataset_id: UUID, store: StoreDep) -> DatasetInfo:
    """Get a dataset's details and profile."""
    return _require_dataset(store, dataset_id)


@router.get("/{dataset_id}/preview")
def preview_dataset(
    dataset_id: UUID,
    store: StoreDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> DatasetPreview:
    """Get the first rows of a dataset."""
    info = _require_dataset(store, dataset_id)
    df = load_dataframe(store.read_csv(dataset_id))
    return DatasetPreview(
        dataset_id=dataset_id,
        total_rows=info.profile.row_count,
        columns=[str(name) for name in df.columns],
        rows=records(df.head(limit)),
    )


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(dataset_id: UUID, store: StoreDep) -> Response:
    """Permanently delete a dataset."""
    if not store.delete(dataset_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Dataset not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
