"""Tests that bad uploads are rejected with a clear reason."""

import pytest

from backend.services.csv_validation import CsvValidationError, validate_csv


def check(raw: bytes, max_rows: int = 100, max_columns: int = 10):
    return validate_csv(raw, max_rows, max_columns)


def test_accepts_a_simple_csv():
    result = check(b"a,b\n1,2\n3,4\n")

    assert result.columns == ["a", "b"]
    assert result.row_count == 2
    assert result.text == "a,b\n1,2\n3,4\n"


@pytest.mark.parametrize(
    "raw",
    [
        "a,b\n1,2\n".encode("utf-8-sig"),  # With the invisible marker Excel adds.
        "a,b\n1,2\n".encode("utf-16"),
        b"a;b\n1;2\n",
        b"a\tb\n1\t2\n",
        b"a,b\r\n1,2\r\n",
    ],
    ids=["utf8-bom", "utf16", "semicolons", "tabs", "windows-line-endings"],
)
def test_normalizes_different_formats_to_plain_csv(raw):
    assert check(raw).text == "a,b\n1,2\n"


def test_reads_cp1252_text():
    assert "café" in check("name,x\ncafé,1\n".encode("cp1252")).text


def test_keeps_commas_inside_quoted_values():
    result = check(b'name,x\n"Smith, Jo",1\n')

    assert result.row_count == 1
    assert '"Smith, Jo"' in result.text


def test_skips_blank_lines():
    assert check(b"a,b\n1,2\n\n3,4\n").row_count == 2


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (b"", "empty"),
        (b"  \n \n", "empty"),
        (b"a,b\n", "no data rows"),
        (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR", "binary"),
        (b"a,b\n1,2\n3\n", "Line 3 has 1 values"),
        (b"a,b\n1,2,3\n", "Line 2 has 3 values"),
        (b"a,a\n1,2\n", "appears more than once"),
        (b"a, a \n1,2\n", "appears more than once"),
        (b"a,,c\n1,2,3\n", "blank"),
        (b'a,b\n"unclosed,2\n', "malformed"),
    ],
    ids=[
        "empty",
        "whitespace-only",
        "header-only",
        "binary",
        "too-few-values",
        "too-many-values",
        "duplicate-column",
        "duplicate-after-trimming",
        "blank-column-name",
        "unclosed-quote",
    ],
)
def test_rejects_bad_files(raw, expected):
    with pytest.raises(CsvValidationError, match=expected):
        check(raw)


def test_rejects_too_many_rows():
    with pytest.raises(CsvValidationError, match="more than 2 rows"):
        check(b"a\n1\n2\n3\n", max_rows=2)


def test_allows_exactly_the_row_limit():
    assert check(b"a\n1\n2\n", max_rows=2).row_count == 2


def test_rejects_too_many_columns():
    with pytest.raises(CsvValidationError, match="more than 2 columns"):
        check(b"a,b,c\n1,2,3\n", max_columns=2)


def test_rejects_a_huge_single_value():
    raw = b"a,b\n" + b"x" * 200_000 + b",1\n"

    with pytest.raises(CsvValidationError, match="malformed"):
        check(raw)
