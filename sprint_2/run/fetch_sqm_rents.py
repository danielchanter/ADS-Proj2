"""
Fetch the SQM Research Weekly Rents Index for Victorian postcodes.

Why this source:
    The supplied Domain dataset is a single snapshot scraped 2025-09-09, so it
    carries no usable rent time series. SQM publishes a weekly advertised-rent
    index per postcode from 2009 to the current week, which both covers the
    2025-2026 gap and overlaps the Domain snapshot, so the two can be anchored
    to each other.

Output:
    data/external/sqm_weekly_rents.csv    long format, one row per postcode-week
    data/external/sqm_postcode_coverage.csv

Usage:
    python sprint_2/run/fetch_sqm_rents.py
    python sprint_2/run/fetch_sqm_rents.py --postcodes 3168 3000 --delay 2

Notes:
    The series is embedded as a JSON array in the page HTML, so no browser or
    API key is needed. Be polite: the default delay is 1.5s between postcodes.
    SQM's terms allow personal/reference use; cite them and do not redistribute.
"""

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
DOMAIN_CSV = REPO / "domain" / "Data" / "vic_rentals_all.csv"
OUT_DIR = REPO / "data" / "external"

URL = "https://sqmresearch.com.au/weekly-rents.php?postcode={pc}&t=1"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
SERIES_RE = re.compile(r'(\[\{"date":.*?\}\])', re.S)

# SQM publishes these five series per postcode.
VALUE_COLS = ["houses_all", "houses_3", "units_all", "units_2", "combined"]


def fetch_postcode(postcode, timeout=40):
    """Return the weekly rent series for one postcode, or None if absent."""
    req = urllib.request.Request(URL.format(pc=postcode), headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", "ignore")

    match = SERIES_RE.search(html)
    if not match:
        return None

    rows = json.loads(match.group(1))
    return rows or None


def domain_postcodes():
    """Postcodes present in the supplied Domain snapshot."""
    df = pd.read_csv(DOMAIN_CSV, low_memory=False, usecols=["postcode"])
    codes = df["postcode"].dropna().astype(int).unique()
    return sorted(int(c) for c in codes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--postcodes",
        nargs="*",
        type=int,
        help="postcodes to fetch (default: every postcode in the Domain data)",
    )
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    targets = args.postcodes or domain_postcodes()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    coverage = []

    for i, pc in enumerate(targets, 1):
        rows = None
        error = ""
        for attempt in range(args.retries + 1):
            try:
                rows = fetch_postcode(pc)
                break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                error = str(exc)
                if attempt < args.retries:
                    time.sleep(args.delay * (attempt + 2))

        if rows:
            frame = pd.DataFrame(rows)
            frame.insert(0, "postcode", pc)
            frames.append(frame)
            coverage.append(
                {
                    "postcode": pc,
                    "n_weeks": len(frame),
                    "first_date": frame["date"].min(),
                    "last_date": frame["date"].max(),
                    "status": "ok",
                }
            )
            status = f"{len(frame):4d} wks  {frame['date'].min()} -> {frame['date'].max()}"
        else:
            coverage.append(
                {
                    "postcode": pc,
                    "n_weeks": 0,
                    "first_date": "",
                    "last_date": "",
                    "status": error or "no series on page",
                }
            )
            status = f"no data ({error or 'empty'})"

        print(f"[{i:3d}/{len(targets)}] {pc}  {status}", flush=True)
        if i < len(targets):
            time.sleep(args.delay)

    if not frames:
        print("No data fetched.", file=sys.stderr)
        return 1

    data = pd.concat(frames, ignore_index=True)
    data["date"] = pd.to_datetime(data["date"])
    for col in VALUE_COLS:
        data[col] = pd.to_numeric(data[col], errors="coerce")
    data = data.sort_values(["postcode", "date"]).reset_index(drop=True)

    rents_path = args.out_dir / "sqm_weekly_rents.csv"
    cover_path = args.out_dir / "sqm_postcode_coverage.csv"
    data.to_csv(rents_path, index=False)
    pd.DataFrame(coverage).to_csv(cover_path, index=False)

    ok = sum(c["status"] == "ok" for c in coverage)
    print(f"\n{len(data):,} rows across {ok}/{len(targets)} postcodes")
    print(f"date range: {data['date'].min().date()} -> {data['date'].max().date()}")
    print(rents_path)
    print(cover_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
