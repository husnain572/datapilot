"""Checks an uploaded CSV before we accept it. Nothing in the file is trusted.

A valid file is rewritten as clean UTF-8, comma-separated text, so everything
after this step (profiling, the sandbox) only ever sees one simple format.
"""

import csv
import io
from dataclasses import dataclass


class CsvValidationError(ValueError):
    """The file was rejected. The message is safe to show to the user."""


@dataclass
class ValidatedCsv:
    text: str
    row_count: int
    columns: list[str]


def _decode(raw: bytes) -> str:
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError as error:
            raise CsvValidationError("The file's text encoding is damaged.") from error

    # A NUL byte never appears in real CSV text, but it is common in binary files.
    if b"\x00" in raw:
        raise CsvValidationError("This looks like a binary file, not a CSV.")

    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise CsvValidationError("Couldn't read the file's text. Please save it as UTF-8.")


def _detect_delimiter(text: str) -> str:
    sample = text[:64 * 1024]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _clean_header(raw_header: list[str]) -> list[str]:
    columns = [name.strip() for name in raw_header]
    if any(not name for name in columns):
        raise CsvValidationError("Every column needs a name, but at least one is blank.")

    seen = set()
    for name in columns:
        if name in seen:
            raise CsvValidationError(f"The column name '{name[:100]}' appears more than once.")
        seen.add(name)

    if any(len(name) > 200 for name in columns):
        raise CsvValidationError("Column names can be at most 200 characters.")
    return columns


def validate_csv(raw: bytes, max_rows: int, max_columns: int) -> ValidatedCsv:
    """Return a clean copy of the CSV, or raise CsvValidationError saying what's wrong."""
    if not raw.strip():
        raise CsvValidationError("The file is empty.")

    text = _decode(raw)
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=_detect_delimiter(text), strict=True)
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")

    try:
        raw_header = next(reader, None)
        if not raw_header:
            raise CsvValidationError("The first line must contain the column names.")

        columns = _clean_header(raw_header)
        if len(columns) > max_columns:
            raise CsvValidationError(f"The file has more than {max_columns} columns.")
        writer.writerow(columns)

        row_count = 0
        for row in reader:
            if not row:  # A completely blank line.
                continue
            if len(row) != len(columns):
                raise CsvValidationError(
                    f"Line {reader.line_num} has {len(row)} values, "
                    f"but the header has {len(columns)} columns."
                )
            row_count += 1
            if row_count > max_rows:
                raise CsvValidationError(f"The file has more than {max_rows:,} rows.")
            writer.writerow(row)
    except csv.Error as error:
        raise CsvValidationError(f"The CSV is malformed near line {reader.line_num}.") from error

    if row_count == 0:
        raise CsvValidationError("The file has column names but no data rows.")
    return ValidatedCsv(text=output.getvalue(), row_count=row_count, columns=columns)
