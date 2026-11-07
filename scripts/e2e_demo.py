"""Runs the demo question through a running backend, and checks the answers independently.

The expected answers are calculated here with only Python's csv module, sharing no code
with the app. This uses real AI requests, which count toward the daily limit.

Run with: uv run python scripts/e2e_demo.py [backend URL]
"""

import csv
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import httpx

SAMPLE = Path(__file__).parents[1] / "sample_data" / "sales.csv"
QUESTION = (
    "Analyze sales performance. Calculate total revenue, identify the top five products by revenue, "
    "compare monthly revenue, detect missing values, and recommend three data-backed actions."
)


def expected_answers() -> dict:
    with SAMPLE.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    total, products, months, missing = 0.0, defaultdict(float), defaultdict(float), 0
    for row in rows:
        missing += sum(value == "" for value in row.values())
        if row["quantity"] and row["unit_price"]:
            revenue = float(row["quantity"]) * float(row["unit_price"])
            total += revenue
            if row["product"]:
                products[row["product"]] += revenue
            if row["order_date"]:
                months[row["order_date"][:7]] += revenue
    top_five = sorted(products.items(), key=lambda item: item[1], reverse=True)[:5]
    return {"total": total, "top_five": top_five, "months": dict(months), "missing": missing}


def close(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-6 * abs(b), 1e-6)


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
    headers = {"X-Visitor-Id": os.environ.get("VISITOR_ID", "e2e-test")}
    expected = expected_answers()
    with httpx.Client(base_url=base, timeout=60) as client:
        dataset = client.post(
            "/api/v1/datasets", files={"file": ("sales.csv", SAMPLE.read_bytes(), "text/csv")}
        ).raise_for_status().json()
        started = client.post(
            "/api/v1/analyses",
            json={"dataset_id": dataset["dataset_id"], "question": QUESTION},
            headers=headers,
        ).raise_for_status().json()
        record = started
        began, last = time.monotonic(), None
        while record["status"] in ("queued", "running") and time.monotonic() - began < 600:
            time.sleep(2)
            record = client.get(f"/api/v1/analyses/{started['analysis_id']}").json()
            if record["stage"] != last:
                last = record["stage"]
                print(f"[{time.monotonic() - began:5.0f}s] {last}", flush=True)
        report = client.get(f"/api/v1/analyses/{started['analysis_id']}/report")

    print(f"\nStatus: {record['status']} | AI requests: {record['ai_requests']} | Message: {record['message']}")
    for attempt in record["attempts"]:
        print(f"Attempt {attempt['attempt']}: {attempt['outcome']} | problem: {(attempt['problem'] or '')[:400]}")
    if record["code"]:
        print(f"\n--- Code ---\n{record['code']}\n")
    if not record["result"]:
        return 1

    for metric in record["result"]["metrics"]:
        print(f"Metric: {metric['name']} = {metric['value']}")
    for check in record["verifications"]:
        print(f"Check: {check['target']}: {check['status']} [{check['formula']}] {check['detail'] or ''}")
    print("Charts:", [(chart["name"], chart["kind"], len(chart["x"])) for chart in record["result"]["charts"]])
    print("Insights:", record["insights"])
    print("Removed claims:", record["removed_claims"])
    print("Report:", report.status_code, len(report.text), "characters")

    numbers = [float(m["value"]) for m in record["result"]["metrics"]]
    cells = [c for t in record["result"]["tables"] for r in t["rows"] for c in r]
    table_numbers = [float(c) for c in cells if isinstance(c, (int, float)) and not isinstance(c, bool)]
    everything = numbers + table_numbers
    outcomes = {
        "total revenue": any(close(n, expected["total"]) for n in numbers),
        "missing values": any(close(n, expected["missing"]) for n in everything),
        "top five products": all(
            name in cells and any(close(n, value) for n in table_numbers) for name, value in expected["top_five"]
        ),
        "every month": all(any(close(n, value) for n in table_numbers) for value in expected["months"].values()),
    }
    print(f"\nIndependent answers: total={expected['total']:.2f}, missing={expected['missing']}, top={expected['top_five'][0]}")
    for name, passed in outcomes.items():
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    return 0 if record["status"] == "completed" and all(outcomes.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
