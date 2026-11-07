"""Turns supported data files into CSV text, so every format then gets the same checks.

Excel and Parquet files are compressed, so a small upload can expand into a huge amount
of data. The size of the expanded data is checked before anything is loaded.
"""

import io
import json
import logging
import zipfile
from dataclasses import dataclass

import pandas as pd

from backend.services.csv_validation import CsvValidationError

logger = logging.getLogger(__name__)

# Extension -> name shown to people.
SUPPORTED_FORMATS = {
    ".csv": "CSV",
    ".tsv": "TSV",
    ".txt": "Text",
    ".xlsx": "Excel",
    ".json": "JSON",
    ".parquet": "Parquet",
}
MAX_EXPANDED_BYTES = 200 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_ZIP_ENTRIES = 2000
MAX_CELLS = 20_000_000


@dataclass
class ConvertedFile:
    raw: bytes  # CSV text, ready for validate_csv.
    source_format: str
    note: str | None = None


def file_extension(filename: str) -> str:
    dot = filename.rfind(".")
    return filename[dot:].lower() if dot != -1 else ""


def _check_cells(rows: int, columns: int, max_rows: int, max_columns: int) -> None:
    if columns > max_columns:
        raise CsvValidationError(f"The file has more than {max_columns} columns.")
    if rows > max_rows:
        raise CsvValidationError(f"The file has more than {max_rows:,} rows.")
    if rows * columns > MAX_CELLS:
        raise CsvValidationError("The file has too many cells to analyze.")


def _check_zip(raw: bytes) -> None:
    """Refuse compressed files that would expand to an unsafe size (a "zip bomb")."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as error:
        raise CsvValidationError("This isn't a valid .xlsx file. Password-protected files aren't supported.") from error
    entries = archive.infolist()
    if len(entries) > MAX_ZIP_ENTRIES:
        raise CsvValidationError("The Excel file has an unusual structure and can't be read safely.")
    if sum(entry.file_size for entry in entries) > MAX_EXPANDED_BYTES:
        raise CsvValidationError("The Excel file expands to more data than we can process.")
    for entry in entries:
        if entry.compress_size and entry.file_size / entry.compress_size > MAX_COMPRESSION_RATIO:
            raise CsvValidationError("The Excel file is compressed in an unsafe way and can't be read.")


def _from_excel(raw: bytes, max_rows: int, max_columns: int) -> ConvertedFile:
    _check_zip(raw)
    try:
        workbook = pd.ExcelFile(io.BytesIO(raw), engine="openpyxl")
        sheets = workbook.sheet_names
        # Read one row past the limit, so we know if there are too many without reading them all.
        frame = pd.read_excel(workbook, sheet_name=sheets[0], nrows=max_rows + 1)
    except CsvValidationError:
        raise
    except Exception as error:  # noqa: BLE001 - any failure here means a damaged or unusual file.
        logger.info("Couldn't read an Excel upload: %s", type(error).__name__)
        raise CsvValidationError("Couldn't read this Excel file. Please check it opens in Excel.") from error
    note = f"Sheet '{sheets[0]}'" + (f" (the first of {len(sheets)} sheets)" if len(sheets) > 1 else "")
    return ConvertedFile(_to_csv(frame, max_rows, max_columns), "Excel", note[:200])


def _from_json(raw: bytes, max_rows: int, max_columns: int) -> ConvertedFile:
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise CsvValidationError("This isn't valid JSON.") from error
    if isinstance(data, dict) and data and all(isinstance(value, list) for value in data.values()):
        lengths = {len(value) for value in data.values()}
        if len(lengths) != 1:
            raise CsvValidationError("Every list in the JSON object must have the same length.")
        frame = pd.DataFrame(data)
    elif isinstance(data, list) and data and all(isinstance(item, dict) for item in data):
        _check_cells(len(data), 0, max_rows, max_columns)
        # Nested objects become columns such as "address.city".
        frame = pd.json_normalize(data, max_level=2)
    else:
        raise CsvValidationError(
            'JSON must be a list of records, like [{"product": "Pen", "price": 2}], '
            'or an object of equal-length lists, like {"product": ["Pen"], "price": [2]}.'
        )
    return ConvertedFile(_to_csv(frame, max_rows, max_columns), "JSON")


def _from_parquet(raw: bytes, max_rows: int, max_columns: int) -> ConvertedFile:
    import pyarrow.parquet as parquet

    try:
        source = parquet.ParquetFile(io.BytesIO(raw))
        metadata = source.metadata
        # Check the declared size before decompressing anything.
        _check_cells(metadata.num_rows, metadata.num_columns, max_rows, max_columns)
        frame = source.read().to_pandas()
    except CsvValidationError:
        raise
    except Exception as error:  # noqa: BLE001 - any failure here means a damaged or unusual file.
        logger.info("Couldn't read a Parquet upload: %s", type(error).__name__)
        raise CsvValidationError("Couldn't read this Parquet file.") from error
    return ConvertedFile(_to_csv(frame, max_rows, max_columns), "Parquet")


def _to_csv(frame: pd.DataFrame, max_rows: int, max_columns: int) -> bytes:
    _check_cells(len(frame), len(frame.columns), max_rows, max_columns)
    if frame.empty:
        raise CsvValidationError("The file has no data rows.")
    frame.columns = [str(column) for column in frame.columns]
    return frame.to_csv(index=False).encode("utf-8")


def convert_to_csv(filename: str, raw: bytes, max_rows: int, max_columns: int) -> ConvertedFile:
    """Return CSV text for any supported format. CSV-like files are passed through unchanged."""
    extension = file_extension(filename)
    if extension not in SUPPORTED_FORMATS:
        raise ValueError(f"Unsupported extension: {extension}")
    if extension == ".xlsx":
        return _from_excel(raw, max_rows, max_columns)
    if extension == ".json":
        return _from_json(raw, max_rows, max_columns)
    if extension == ".parquet":
        return _from_parquet(raw, max_rows, max_columns)
    return ConvertedFile(raw, SUPPORTED_FORMATS[extension])
