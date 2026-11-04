"""Creates sample_data/sales.csv, a made-up sales dataset for the demo.

The random seed is fixed, so running this again produces the identical file.
It includes some missing values and duplicate rows on purpose, for DataPilot to find.

Run with: uv run python scripts/generate_sales_data.py
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

OUTPUT = Path(__file__).parents[1] / "sample_data" / "sales.csv"
COLUMNS = ["order_date", "product", "quantity", "unit_price", "region"]
ROW_COUNT = 1000
MISSING_VALUE_COUNT = 30
DUPLICATE_ROW_COUNT = 10

# Product name, list price, and how often it sells relative to the others.
PRODUCTS = [
    ("Laptop", 899.00, 2),
    ("Monitor", 249.00, 4),
    ("Office Chair", 179.00, 3),
    ("Standing Desk", 429.00, 2),
    ("Keyboard", 59.00, 6),
    ("Mouse", 29.00, 7),
    ("Webcam", 79.00, 4),
    ("Headset", 119.00, 5),
    ("Docking Station", 189.00, 3),
    ("USB-C Cable", 15.00, 8),
]
REGIONS = ["North", "South", "East", "West"]
REGION_WEIGHTS = [4, 2, 3, 3]


def generate_rows(rng: random.Random) -> list[list[str]]:
    start = date(2025, 1, 1)
    rows = []
    for _ in range(ROW_COUNT):
        product, price, _weight = rng.choices(PRODUCTS, weights=[p[2] for p in PRODUCTS])[0]
        # Sales gently rise through the year.
        day = min(rng.randrange(365), rng.randrange(365)) if rng.random() < 0.2 else rng.randrange(365)
        discount = rng.choice([1.0, 1.0, 1.0, 0.95, 0.9])
        rows.append(
            [
                (start + timedelta(days=364 - day)).isoformat(),
                product,
                str(rng.randint(1, 8)),
                f"{price * discount:.2f}",
                rng.choices(REGIONS, weights=REGION_WEIGHTS)[0],
            ]
        )
    rows.sort(key=lambda row: row[0])
    return rows


def main() -> None:
    rng = random.Random(42)
    rows = generate_rows(rng)

    # Blank out values in columns other than the date, which keeps rows sorted.
    for row_index in rng.sample(range(len(rows)), MISSING_VALUE_COUNT):
        rows[row_index][rng.randrange(1, len(COLUMNS))] = ""

    # Repeat some rows that have no blanks, as if they were entered twice.
    complete = [row for row in rows if all(row)]
    rows.extend(list(row) for row in rng.sample(complete, DUPLICATE_ROW_COUNT))
    rows.sort(key=lambda row: row[0])

    OUTPUT.parent.mkdir(exist_ok=True)
    with OUTPUT.open("w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file, lineterminator="\n")
        writer.writerow(COLUMNS)
        writer.writerows(rows)
    print(f"Wrote {len(rows)} rows to {OUTPUT}")


if __name__ == "__main__":
    main()
