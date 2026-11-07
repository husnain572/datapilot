"""Tests uploads in each supported format, including damaged and hostile files."""

import io
import json
import zipfile

import openpyxl
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.config import Settings, get_settings
from backend.main import app
from backend.services.csv_validation import CsvValidationError, validate_csv
from backend.services.file_formats import MAX_COMPRESSION_RATIO, convert_to_csv

ROWS = [["2025-01-15", "Pen", 2, 1.5], ["2025-02-20", "Book", 3, 10.0]]
COLUMNS = ["order_date", "product", "quantity", "unit_price"]


def excel(rows=ROWS, columns=COLUMNS, extra_sheets=()) -> bytes:
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sales"
    sheet.append(columns)
    for row in rows:
        sheet.append(row)
    for name in extra_sheets:
        book.create_sheet(name).append(["ignored"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def parquet(frame: pd.DataFrame) -> bytes:
    buffer = io.BytesIO()
    frame.to_parquet(buffer, index=False)
    return buffer.getvalue()


def convert(name: str, raw: bytes, max_rows: int = 100, max_columns: int = 10):
    converted = convert_to_csv(name, raw, max_rows, max_columns)
    return converted, validate_csv(converted.raw, max_rows, max_columns)


def test_excel_becomes_validated_csv():
    converted, validated = convert("sales.xlsx", excel())

    assert converted.source_format == "Excel"
    assert converted.note == "Sheet 'Sales'"
    assert validated.columns == COLUMNS
    assert validated.row_count == 2
    assert "Pen,2,1.5" in validated.text


def test_excel_with_several_sheets_says_which_one_was_used():
    converted, _ = convert("sales.xlsx", excel(extra_sheets=["Notes", "Old"]))

    assert converted.note == "Sheet 'Sales' (the first of 3 sheets)"


def test_excel_dates_stay_recognizable_as_dates():
    from datetime import date

    _, validated = convert("sales.xlsx", excel(rows=[[date(2025, 3, 1), "Pen", 1, 2.0]]))

    assert "2025-03-01" in validated.text


@pytest.mark.parametrize("data", [
    [{"product": "Pen", "quantity": 2}, {"product": "Book", "quantity": 3}],
    {"product": ["Pen", "Book"], "quantity": [2, 3]},
], ids=["list-of-records", "object-of-lists"])
def test_json_layouts_become_csv(data):
    converted, validated = convert("sales.json", json.dumps(data).encode())

    assert converted.source_format == "JSON"
    assert validated.columns == ["product", "quantity"]
    assert validated.row_count == 2


def test_nested_json_is_flattened_into_columns():
    data = [{"product": "Pen", "store": {"city": "Lahore"}}]

    _, validated = convert("sales.json", json.dumps(data).encode())

    assert validated.columns == ["product", "store.city"]


def test_parquet_becomes_csv():
    frame = pd.DataFrame(ROWS, columns=COLUMNS)

    converted, validated = convert("sales.parquet", parquet(frame))

    assert converted.source_format == "Parquet"
    assert validated.columns == COLUMNS
    assert validated.row_count == 2


@pytest.mark.parametrize("name", ["sales.tsv", "sales.txt"])
def test_text_formats_use_the_csv_checks(name):
    _, validated = convert(name, b"a\tb\n1\t2\n")

    assert validated.text == "a,b\n1,2\n"


@pytest.mark.parametrize(
    ("name", "raw", "expected"),
    [
        ("sales.xlsx", b"not a zip file", "isn't a valid .xlsx"),
        ("sales.xlsx", b"PK\x03\x04garbage", "isn't a valid .xlsx"),
        ("sales.json", b"{not json", "isn't valid JSON"),
        ("sales.json", b'"just text"', "list of records"),
        ("sales.json", b"[1, 2, 3]", "list of records"),
        ("sales.json", b"[]", "list of records"),
        ("sales.json", b'{"a": [1, 2], "b": [1]}', "same length"),
        ("sales.json", b"[" * 100000, "isn't valid JSON"),
        ("sales.parquet", b"PAR1 not really parquet PAR1", "Couldn't read this Parquet"),
    ],
    ids=["xlsx-not-zip", "xlsx-damaged", "json-broken", "json-string", "json-numbers", "json-empty",
         "json-uneven", "json-deeply-nested", "parquet-damaged"],
)
def test_damaged_files_get_a_clear_message(name, raw, expected):
    with pytest.raises(CsvValidationError, match=expected):
        convert(name, raw)


def test_excel_zip_bomb_is_refused_before_loading():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", b"\0" * (50 * 1024 * 1024))

    with pytest.raises(CsvValidationError, match="unsafe way"):
        convert("bomb.xlsx", buffer.getvalue())

    assert MAX_COMPRESSION_RATIO == 200


def test_excel_with_xml_entity_attack_is_refused():
    """A "billion laughs" attack: tiny XML that expands enormously if entities are processed."""
    attack = (
        b'<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        b'<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>'
        b'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">&lol2;</workbook>'
    )
    original = excel()
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for item in source.infolist():
            data = attack if item.filename == "xl/workbook.xml" else source.read(item.filename)
            target.writestr(item, data)

    with pytest.raises(CsvValidationError, match="Couldn't read this Excel"):
        convert("attack.xlsx", buffer.getvalue())


@pytest.mark.parametrize("name", ["big.xlsx", "big.parquet", "big.json"])
def test_too_many_rows_in_any_format(name):
    rows = [["2025-01-01", "Pen", 1, 1.0]] * 6
    if name.endswith(".xlsx"):
        raw = excel(rows=rows)
    elif name.endswith(".parquet"):
        raw = parquet(pd.DataFrame(rows, columns=COLUMNS))
    else:
        raw = json.dumps([dict(zip(COLUMNS, row)) for row in rows]).encode()

    with pytest.raises(CsvValidationError, match="more than 5 rows"):
        convert(name, raw, max_rows=5)


def test_too_many_columns_in_excel():
    with pytest.raises(CsvValidationError, match="more than 3 columns"):
        convert("wide.xlsx", excel(), max_columns=3)


def test_excel_formula_text_is_kept_as_data_and_never_run():
    _, validated = convert("f.xlsx", excel(rows=[["2025-01-01", "=1+1", 1, 1.0]]))

    assert validated.row_count == 1


# --- Through the API ---


@pytest.fixture
def client(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path)
    app.dependency_overrides[get_settings] = lambda: settings
    yield TestClient(app)
    app.dependency_overrides.clear()


def upload(client, name, raw):
    return client.post("/api/v1/datasets", files={"file": (name, raw, "application/octet-stream")})


def test_api_accepts_excel_and_reports_the_source(client):
    response = upload(client, "sales.xlsx", excel())

    assert response.status_code == 201
    body = response.json()
    assert body["source_format"] == "Excel"
    assert body["source_note"] == "Sheet 'Sales'"
    assert body["profile"]["row_count"] == 2
    kinds = {c["name"]: c["kind"] for c in body["profile"]["columns"]}
    assert kinds["quantity"] == "numeric"


def test_api_stores_converted_data_as_csv_for_the_sandbox(client, tmp_path):
    dataset_id = upload(client, "sales.json", json.dumps([{"a": 1}, {"a": 2}]).encode()).json()["dataset_id"]

    assert (tmp_path / "datasets" / dataset_id / "data.csv").read_text() == "a\n1\n2\n"


@pytest.mark.parametrize("name", ["data.xls", "data.pdf", "data.exe", "data", "data.xlsm"])
def test_api_rejects_unsupported_types_with_the_list(client, name):
    response = upload(client, name, b"anything")

    assert response.status_code == 415
    assert ".xlsx" in response.json()["detail"]


def test_api_reports_damaged_excel_clearly(client):
    response = upload(client, "sales.xlsx", b"not a spreadsheet")

    assert response.status_code == 422
    assert "valid .xlsx" in response.json()["detail"]


def test_old_csv_uploads_still_work_the_same(client):
    response = upload(client, "sales.csv", b"a,b\n1,2\n")

    assert response.status_code == 201
    assert response.json()["source_format"] == "CSV"
