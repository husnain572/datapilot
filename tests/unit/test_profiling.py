"""Tests the dataset profile against answers worked out by hand."""

import json

from backend.services.profiling import load_dataframe, profile_dataframe, records

# Hand-checked facts about this table:
#   5 rows, 4 columns, 2 blank cells (one price, one region).
#   Rows 1 and 2 are identical, so there is 1 duplicate.
#   price values are 10, 10, 20, 30: sum 70, mean 17.5, median 15, min 10, max 30.
CSV = """order_date,product,price,region
2025-01-15,Pen,10,North
2025-01-15,Pen,10,North
2025-02-20,Book,20,
2025-03-05,Pen,,South
2025-03-10,Lamp,30,South
"""


def profile():
    return profile_dataframe(load_dataframe(CSV))


def column(name):
    return next(c for c in profile().columns if c.name == name)


def test_counts_rows_columns_missing_and_duplicates():
    result = profile()

    assert result.row_count == 5
    assert result.column_count == 4
    assert result.missing_cell_count == 2
    assert result.duplicate_row_count == 1


def test_numeric_statistics_match_hand_calculation():
    price = column("price")

    assert price.kind == "numeric"
    assert price.missing_count == 1
    assert price.missing_percent == 20.0
    assert price.numeric.sum == 70
    assert price.numeric.mean == 17.5
    assert price.numeric.median == 15
    assert price.numeric.min == 10
    assert price.numeric.max == 30


def test_detects_dates_and_their_range():
    order_date = column("order_date")

    assert order_date.kind == "datetime"
    assert order_date.earliest.startswith("2025-01-15")
    assert order_date.latest.startswith("2025-03-10")


def test_summarizes_categories_most_common_first():
    product = column("product")

    assert product.kind == "categorical"
    assert product.unique_count == 3
    assert product.top_values[0].value == "Pen"
    assert product.top_values[0].count == 3


def test_text_with_some_numbers_is_not_mistaken_for_dates():
    df = load_dataframe("code\nA1\nB2\nC3\n")

    assert profile_dataframe(df).columns[0].kind == "categorical"


def test_completely_empty_column():
    df = load_dataframe("a,b\n1,\n2,\n")

    assert profile_dataframe(df).columns[1].kind == "empty"


def test_infinity_and_missing_values_become_valid_json():
    df = load_dataframe("a,b\ninf,x\n1,\n-inf,y\n")

    result = profile_dataframe(df)

    # Python's json would otherwise write Infinity or NaN, which isn't valid JSON.
    json.dumps(result.model_dump(), allow_nan=False)
    assert result.columns[0].numeric.max == 1
    assert records(df)[1]["b"] is None
