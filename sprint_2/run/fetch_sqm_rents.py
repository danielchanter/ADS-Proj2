"""
Fetch the SQM Research Weekly Rents Index for Victorian postcodes.

Why this source:
    The supplied Domain dataset is a single snapshot scraped 2025-09-09, so it
    carries no usable rent time series. SQM publishes a weekly advertised-rent
    index per postcode from 2009 to the current week, which both covers the
    2025-2026 gap and overlaps the Domain snapshot, so the two can be anchored
    to each other.

Coverage:
    Every Victorian postal area in the ABS mesh-block correspondence (694), so
    each suburb in suburb_postcode_sa2.csv can be given its postcode's series.
    The coverage CSV records, per postcode, the span of its series or the
    reason it has none.

Output:
    data/external/sqm_weekly_rents.csv    long format, one row per postcode-week
    data/external/sqm_postcode_coverage.csv

Usage:
    python sprint_2/run/fetch_sqm_rents.py                  # every Victorian postcode, ~25 min
    python sprint_2/run/fetch_sqm_rents.py --missing-only   # postcodes absent from the CSV
    python sprint_2/run/fetch_sqm_rents.py --postcodes 3168 3000 --delay 2

Notes:
    The series is embedded as a JSON array in the page HTML, so no browser or
    API key is needed. Be polite: the default delay is 1.5s between postcodes.
    SQM's terms allow personal/reference use; cite them and do not redistribute.
    A run updates the CSV in place: postcodes it fetches are replaced, and every
    other postcode keeps the series it already had.
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

from pipeline.common import BROWSER_UA, EXTERNAL_DIR, OUTPUT_DIR
from pipeline.correspondence import load_vic_mesh_blocks
from pipeline.sqm import SQM_VALUE_COLS

URL = "https://sqmresearch.com.au/weekly-rents.php?postcode={pc}&t=1"
SERIES_RE = re.compile(r'(\[\{"date":.*?\}\])', re.S)

RENT_COLS = ["postcode", "date", *SQM_VALUE_COLS]
COVERAGE_COLS = ["postcode", "n_weeks", "first_date", "last_date", "status"]


def fetch_postcode(postcode, timeout=40):
    """Return the weekly rent series for one postcode, or None if absent."""
    req = urllib.request.Request(URL.format(pc=postcode), headers={"User-Agent": BROWSER_UA})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        html = resp.read().decode("utf-8", "ignore")

    match = SERIES_RE.search(html)
    if not match:
        return None

    rows = json.loads(match.group(1))
    return rows or None


def victorian_postcodes():
    """
    Every Victorian postal area (ABS POA), read from the mesh-block table the
    pipeline caches. The first call downloads the ABS allocation files (about
    90 MB) when the cache is empty.
    """
    cache = OUTPUT_DIR / "_cache"
    cache.mkdir(parents=True, exist_ok=True)
    mesh_blocks = load_vic_mesh_blocks(cache)
    return sorted(int(pc) for pc in mesh_blocks["postcode"].dropna().unique())


def load_earlier(path, columns):
    """The CSV an earlier run wrote, or an empty frame with the same columns."""
    if path.exists():
        return pd.read_csv(path)
    return pd.DataFrame(columns=columns)


def coverage_table(data, no_series, earlier_coverage, targets):
    """
    One row per postcode: the span of its series, or the reason it has none.

    `no_series` holds this run's postcodes that returned nothing. Postcodes
    outside `targets` carry their earlier no-series row forward, so the table
    always describes every postcode tried so far.
    """
    span = data.groupby("postcode")["date"].agg(
        n_weeks="size", first_date="min", last_date="max"
    ).reset_index()
    for c in ["first_date", "last_date"]:
        span[c] = span[c].dt.strftime("%Y-%m-%d")
    span["status"] = "ok"

    carried = earlier_coverage[
        earlier_coverage["status"].ne("ok") & ~earlier_coverage["postcode"].isin(targets)
    ]
    table = pd.concat([span, pd.DataFrame(no_series, columns=COVERAGE_COLS), carried])
    # span comes first, so a postcode with a series on disk is reported as ok.
    table = table.drop_duplicates("postcode", keep="first")
    return table[COVERAGE_COLS].sort_values("postcode").reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--postcodes",
        nargs="*",
        type=int,
        help="postcodes to fetch (default: every Victorian postal area)",
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="fetch only the target postcodes that the rents CSV does not hold yet",
    )
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between requests")
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--out-dir", type=Path, default=EXTERNAL_DIR)
    args = parser.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    rents_path = args.out_dir / "sqm_weekly_rents.csv"
    cover_path = args.out_dir / "sqm_postcode_coverage.csv"

    earlier = load_earlier(rents_path, RENT_COLS)
    earlier["date"] = pd.to_datetime(earlier["date"])
    earlier_coverage = load_earlier(cover_path, COVERAGE_COLS)

    targets = args.postcodes or victorian_postcodes()
    if args.missing_only:
        held = set(earlier["postcode"])
        targets = [pc for pc in targets if pc not in held]
        if not targets:
            print(f"Every target postcode is already in {rents_path}")
            return 0

    frames = []
    no_series = []

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
            status = f"{len(frame):4d} wks  {frame['date'].min()} -> {frame['date'].max()}"
        else:
            no_series.append(
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

    if not frames and earlier.empty:
        print("No data fetched.", file=sys.stderr)
        return 1

    fetched = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=RENT_COLS)
    fetched["date"] = pd.to_datetime(fetched["date"])
    for col in SQM_VALUE_COLS:
        fetched[col] = pd.to_numeric(fetched[col], errors="coerce")

    # Postcodes absent from this run's results keep the series already on disk.
    kept = earlier[~earlier["postcode"].isin(fetched["postcode"])]
    data = pd.concat([kept, fetched], ignore_index=True)[RENT_COLS]
    data["postcode"] = data["postcode"].astype(int)
    data = data.sort_values(["postcode", "date"]).reset_index(drop=True)

    coverage = coverage_table(data, no_series, earlier_coverage, targets)
    data.to_csv(rents_path, index=False)
    coverage.to_csv(cover_path, index=False)

    print(f"\nFetched {len(frames)}/{len(targets)} postcodes this run")
    print(f"{len(data):,} rows across {data['postcode'].nunique()} postcodes "
          f"({int(coverage['status'].ne('ok').sum())} with no series)")
    print(f"date range: {data['date'].min().date()} -> {data['date'].max().date()}")
    print(rents_path)
    print(cover_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
