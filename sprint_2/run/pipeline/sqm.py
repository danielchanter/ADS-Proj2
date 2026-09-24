"""SQM Research weekly postcode rent index, joined as-of each listing's date."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .common import EXTERNAL_DIR, record_source

SOURCE_NAME = "SQM Research Weekly Rents Index"

# SQM Research Weekly Rents Index, produced by sprint_2/run/fetch_sqm_rents.py.
# Weekly advertised rents per postcode, 2009-08 onwards. This is the only source
# here with a usable rent time series, so it supplies the market-level benchmark
# the Domain snapshot cannot.
SQM_RENTS_CSV = EXTERNAL_DIR / "sqm_weekly_rents.csv"
# SQM publishes these five series per postcode.
SQM_VALUE_COLS = ["houses_all", "houses_3", "units_all", "units_2", "combined"]

# SQM publishes separate house and unit indexes. Map Domain's property types onto
# whichever is the right comparison; anything ambiguous falls back to the combined
# index rather than being forced into one or the other.
SQM_SERIES_BY_PROPERTY_TYPE = {
    "House": "houses_all",
    "Acreage / Semi-Rural": "houses_all",
    "New House & Land": "houses_all",
    "Townhouse": "houses_all",
    "Semi-Detached": "houses_all",
    "Terrace": "houses_all",
    "Duplex": "houses_all",
    "Villa": "houses_all",
    "Farm": "houses_all",
    "Apartment / Unit / Flat": "units_all",
    "Studio": "units_all",
    "New Apartments / Off the Plan": "units_all",
    "Block of Units": "units_all",
}
SQM_SERIES_BY_PRIMARY_TYPE = {
    "House": "houses_all",
    "Townhouse/Villa": "houses_all",
    "Apartment": "units_all",
    "New Developments": "units_all",
}

def load_sqm_weekly_rents(path: Path) -> pd.DataFrame:
    """
    Weekly advertised-rent index per postcode, as fetched by fetch_sqm_rents.py.

    SQM writes 0 when a series had no advertised stock that week (7% of units_2,
    3% of units_all), so zeros are read as missing rather than as a $0 rent.
    """
    if not path.exists():
        return pd.DataFrame()

    d = pd.read_csv(path)
    missing = {"postcode", "date"} - set(d.columns)
    if missing:
        return pd.DataFrame()

    d["postcode"] = pd.to_numeric(d["postcode"], errors="coerce")
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    for c in SQM_VALUE_COLS:
        if c in d.columns:
            d[c] = pd.to_numeric(d[c], errors="coerce").replace(0, np.nan)

    d = d.dropna(subset=["postcode", "date"]).copy()
    d["postcode"] = d["postcode"].astype("int64")
    return d.sort_values(["postcode", "date"]).reset_index(drop=True)

def add_sqm_yoy(sqm: pd.DataFrame) -> pd.DataFrame:
    """
    Year-on-year change of each SQM series, matched to the week 52 weeks earlier.

    Matched by date rather than by row position, because thin rural postcodes drop
    out of the index for months at a time and a positional shift would silently
    compare weeks that are years apart.
    """
    cols = [c for c in SQM_VALUE_COLS if c in sqm.columns]
    lag = sqm[["postcode", "date"] + cols].copy()
    lag["date"] = lag["date"] + pd.Timedelta(days=364)
    lag = lag.rename(columns={c: f"{c}__prev" for c in cols})

    merged = pd.merge_asof(
        sqm.sort_values("date"),
        lag.sort_values("date"),
        on="date",
        by="postcode",
        direction="nearest",
        tolerance=pd.Timedelta(days=10),
    )

    for c in cols:
        prev = merged[f"{c}__prev"]
        merged[f"{c}_yoy_pct"] = 100 * (merged[c] / prev - 1)

    merged = merged.drop(columns=[f"{c}__prev" for c in cols])
    return merged.sort_values(["postcode", "date"]).reset_index(drop=True)

def sqm_series_choice(master: pd.DataFrame) -> pd.Series:
    """Which SQM series each listing should be compared against."""
    choice = pd.Series("combined", index=master.index, dtype="object")

    if "primary_type" in master.columns:
        mapped = master["primary_type"].map(SQM_SERIES_BY_PRIMARY_TYPE)
        choice = choice.where(mapped.isna(), mapped)

    # property_type is the more granular of the two, so it wins where both apply.
    if "property_type" in master.columns:
        mapped = master["property_type"].map(SQM_SERIES_BY_PROPERTY_TYPE)
        choice = choice.where(mapped.isna(), mapped)

    return choice

def add_sqm_market_features(master, sqm, coverage, tolerance_days=31,
                            source_path: Path = SQM_RENTS_CSV):
    """
    Attach the postcode rent index as it stood on each listing's date_listed.

    An as-of (backward) join on (postcode, date_listed) rather than a plain
    postcode merge: the index runs weekly from 2009 and listings span 2008-2025,
    so a listing must see the market as it was when it was advertised, not the
    postcode's whole-history average.

    tolerance_days caps how stale a matched week may be. It matters: while the
    median gap between weeks is 7 days in every postcode, 131 of 425 postcodes
    have at least one dropout longer than 60 days, and the worst runs 8 years.
    Without the cap a 2025 listing in postcode 3591 would be handed a 2017 rent.
    """
    if sqm.empty:
        if Path(source_path).exists():
            detail = f"{source_path} has no usable postcode/date rows"
        else:
            detail = f"{source_path} not found; run sprint_2/run/fetch_sqm_rents.py first"
        record_source(coverage, SOURCE_NAME, "missing", detail)
        return master
    if "date_listed" not in master.columns or "postcode" not in master.columns:
        record_source(
            coverage, SOURCE_NAME, "skipped",
            "listings lack postcode/date_listed, so no as-of join is possible",
        )
        return master

    sqm = add_sqm_yoy(sqm)
    value_cols = [c for c in sqm.columns if c not in ("postcode", "date")]

    left = pd.DataFrame({
        "_row": np.arange(len(master)),
        "postcode": pd.to_numeric(master["postcode"], errors="coerce"),
        "date_listed": pd.to_datetime(master["date_listed"], errors="coerce"),
    })
    left = left.dropna(subset=["postcode", "date_listed"]).copy()
    left["postcode"] = left["postcode"].astype("int64")

    matched = pd.merge_asof(
        left.sort_values("date_listed"),
        sqm.rename(columns={"date": "sqm_week"}).sort_values("sqm_week"),
        left_on="date_listed",
        right_on="sqm_week",
        by="postcode",
        direction="backward",
        tolerance=pd.Timedelta(days=tolerance_days),
    )

    matched["sqm_week_lag_days"] = (
        matched["date_listed"] - matched["sqm_week"]
    ).dt.days

    out = (
        matched.set_index("_row")[["sqm_week", "sqm_week_lag_days"] + value_cols]
        .reindex(np.arange(len(master)))
    )
    out.columns = [
        c if c.startswith("sqm_") else f"sqm_{c}" for c in out.columns
    ]
    out.index = master.index
    master = pd.concat([master, out], axis=1)

    # Type-matched benchmark: the house index for houses, the unit index for units.
    series = sqm_series_choice(master)
    master["sqm_market_series"] = series
    lookup = {name: master[f"sqm_{name}"] for name in SQM_VALUE_COLS
              if f"sqm_{name}" in master.columns}
    master["sqm_market_rent"] = np.select(
        [series == name for name in lookup],
        [lookup[name] for name in lookup],
        default=np.nan,
    )
    master["sqm_market_rent_yoy_pct"] = np.select(
        [series == name for name in lookup],
        [master.get(f"sqm_{name}_yoy_pct", pd.Series(np.nan, index=master.index))
         for name in lookup],
        default=np.nan,
    )

    # SQM's bedroom-specific series are only defined for 3-bed houses and 2-bed
    # units, so this is a tighter comparable where it exists and null elsewhere.
    if "bedrooms" in master.columns:
        beds = pd.to_numeric(master["bedrooms"], errors="coerce")
        master["sqm_market_rent_bedroom_matched"] = np.select(
            [(series == "houses_all") & (beds == 3),
             (series == "units_all") & (beds == 2)],
            [master.get("sqm_houses_3", np.nan),
             master.get("sqm_units_2", np.nan)],
            default=np.nan,
        )

    # The five raw series and their YoY changes are only inputs to the type-matched
    # columns above: for any one listing, four of the five are the wrong property
    # type. sqm_week is date_listed minus the lag, and the series label is a
    # restatement of property_type.
    raw_cols = ["sqm_week", "sqm_market_series"]
    raw_cols += [f"sqm_{c}" for c in value_cols]
    master = master.drop(columns=[c for c in raw_cols if c in master.columns])

    n_matched = int(master["sqm_market_rent"].notna().sum())
    record_source(
        coverage, SOURCE_NAME, "joined",
        f"as-of join on (postcode, date_listed), tolerance {tolerance_days}d; "
        f"{n_matched:,}/{len(master):,} listings matched a market rent",
    )
    return master
