"""
Crime Statistics Victoria suburb incident rates, joined as-of each listing's
date. Rates use the ABS Census 2021 suburb (SAL) population as denominator.
"""

from __future__ import annotations

import calendar
import re
from pathlib import Path
from urllib.parse import unquote

import numpy as np
import pandas as pd
import requests

from .common import (
    BROWSER_HEADERS, arcgis_query_paged, area_key, cached_download,
    normalize_code, numeric, pick_col, record_source, suburb_key,
)

SOURCE_NAME = "Crime Statistics Victoria"

# Layer 5 of the ABS General Community Profile service is the Census GCP on
# Suburbs and Localities (SAL).
# It is the population denominator for the suburb-level crime counts, which the
# Crime Statistics Agency publishes as raw incidents with no denominator.
GCP_SAL_URL = (
    "https://services1.arcgis.com/vHnIGBHHqDR6y0CR/ArcGIS/rest/services/"
    "2021_ABS_General_Community_Profile/FeatureServer/5/query"
)

# Crime Statistics Victoria, "LGA Criminal Incidents" workbook. Only Table 03 is
# read: incidents by postcode, suburb, offence division and year.
#
# The file URL carries the release month and a Drupal revision suffix, so it
# changes every quarter. It is discovered from the download page and the URL
# below is only the fallback for when that page cannot be read.
CSA_DOWNLOAD_PAGE = (
    "https://www.crimestatistics.vic.gov.au/crime-statistics/"
    "latest-victorian-crime-data/download-data"
)
CSA_LGA_INCIDENTS_FALLBACK_URL = (
    "https://files.crimestatistics.vic.gov.au/2026-06/"
    "Data_Tables_LGA_Criminal_Incidents_Year_Ending_March_2026_0.xlsx"
)

# Short column-safe names for the six ANZSOC-style offence divisions CSA uses.
CSA_OFFENCE_DIVISIONS = {
    "A": "person",
    "B": "property",
    "C": "drug",
    "D": "public_order",
    "E": "justice",
    "F": "other",
}

def csa_workbook_url():
    """
    Current "LGA Criminal Incidents" workbook URL, read off the CSA download page.

    CSA republishes quarterly under a new folder with a new filename suffix, so
    a hard-coded URL goes stale within months.
    """
    try:
        r = requests.get(CSA_DOWNLOAD_PAGE, headers=BROWSER_HEADERS, timeout=120)
        r.raise_for_status()
        links = re.findall(
            r'href="(https://files\.crimestatistics\.vic\.gov\.au/[^"]+\.xlsx)"', r.text
        )
        for url in links:
            name = unquote(url.rsplit("/", 1)[-1]).lower()
            if "indigenous" in name:
                continue
            if "lga" in name and "criminal_incidents" in name:
                return url
    except Exception as e:
        print(f"WARNING: CSA download page lookup failed ({e}); using fallback URL.")
    return CSA_LGA_INCIDENTS_FALLBACK_URL

def csa_period_end(years, endings):
    """
    Convert a CSA (year, month-ending) label to the date the period closed.

    Year 2025 ending "March" covers 2024-04-01 to 2025-03-31. The as-of join
    needs that closing date, so a listing is never given a period that had not
    finished when it was advertised.
    """
    months = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
    month = endings.astype(str).str.strip().str.lower().map(months)
    year = pd.to_numeric(years, errors="coerce")
    ok = month.notna() & year.notna()
    out = pd.Series(pd.NaT, index=year.index, dtype="datetime64[ns]")
    if ok.any():
        starts = pd.to_datetime(
            {"year": year[ok].astype(int), "month": month[ok].astype(int), "day": 1}
        )
        out.loc[ok] = (starts + pd.offsets.MonthEnd(0)).to_numpy()
    return out

def load_crime_table(cache_dir: Path, xlsx_path: Path | None = None):
    """Download (once) and parse Table 03 (incidents by postcode/suburb/division)."""
    path = Path(xlsx_path) if xlsx_path else cache_dir / "csa_lga_criminal_incidents.xlsx"
    if not path.exists():
        cached_download(csa_workbook_url(), path, timeout=300)

    xls = pd.ExcelFile(path)
    if "Table 03" not in xls.sheet_names:
        raise ValueError(f"workbook has sheets {xls.sheet_names}; expected 'Table 03'")
    return xls.parse("Table 03")

def load_sal_population():
    """Census 2021 usual-resident population per Victorian suburb (SAL)."""
    d = arcgis_query_paged(
        GCP_SAL_URL,
        where="SAL_CODE_2021 LIKE '2%'",
        out_fields="SAL_CODE_2021,SAL_NAME_2021,Tot_P_P",
        order_by="SAL_CODE_2021",
    )
    if d.empty:
        return d

    d["sal_code"] = normalize_code(d["SAL_CODE_2021"])
    d["crime_sal_name"] = d["SAL_NAME_2021"].astype(str).str.strip()
    d["suburb_key"] = suburb_key(d["SAL_NAME_2021"])

    # "Hillside (Melton - Vic.)" -> MELTON. Used to tell repeated suburb names apart.
    hint = d["SAL_NAME_2021"].astype(str).str.extract(r"\(([^)]*)\)\s*$")[0].fillna("")
    hint = hint.str.replace(r"\s*-\s*Vic\.?\s*$", "", regex=True)
    d["sal_lga_hint"] = area_key(hint)

    d["crime_suburb_population"] = numeric(d["Tot_P_P"]).replace(0, np.nan)
    return d[["sal_code", "crime_sal_name", "suburb_key", "sal_lga_hint",
              "crime_suburb_population"]]

def resolve_suburb_to_sal(t03, sal):
    """
    Map each (postcode, suburb) in the crime data onto one ABS suburb (SAL).

    Fifteen Victorian suburb names are reused, so the name alone is not a key.
    Duplicates are split by postcode and by the LGA holding most of the suburb's
    incidents, matched against the LGA that ABS encodes in the SAL name
    ("Hillside (Melton - Vic.)"). Both tests are needed:
      * Ascot sits in both Ballarat (3364) and Greater Bendigo (3551), so only
        the postcode separates them.
      * Hillside 3037 straddles a boundary and is filed partly under Brimbank,
        so only the larger share lands on the ABS label, Melton.

    If neither test settles it, the more populous candidate wins and the row is
    flagged, since the alternative is dropping a real suburb.
    """
    dominant = (
        t03.groupby(["postcode", "suburb_key", "lga_key"], as_index=False)["incidents"]
        .sum()
        .sort_values("incidents", ascending=False)
        .drop_duplicates(["postcode", "suburb_key"])
        .drop(columns="incidents")
    )

    cand = dominant.merge(sal, on="suburb_key", how="inner")
    cand["_candidates"] = cand.groupby(["postcode", "suburb_key"])["sal_code"].transform("size")
    cand["_hint_match"] = (cand["sal_lga_hint"] == cand["lga_key"]).astype(int)

    # Preference order: LGA hint agrees, then the more populous candidate.
    cand = cand.sort_values(
        ["postcode", "suburb_key", "_hint_match", "crime_suburb_population"],
        ascending=[True, True, False, False],
    )
    pairs = cand.drop_duplicates(["postcode", "suburb_key"]).copy()

    # A name with several candidates that the hint did not resolve stays flagged.
    pairs["crime_sal_ambiguous"] = (
        (pairs["_candidates"] > 1) & (pairs["_hint_match"] == 0)
    ).astype("int8")

    return pairs[["postcode", "suburb_key", "sal_code", "crime_sal_name",
                  "crime_suburb_population", "crime_sal_ambiguous"]]

def build_suburb_crime(t03, pairs, divisions):
    """
    Suburb-by-period incident rates.

    Counts are summed onto the SAL rather than kept per postcode, because the
    ABS denominator covers the whole suburb while CSA splits a few suburbs
    across two postcodes (Beaumaris: 3,070 incidents under 3193, 24 under 3192).
    Dividing one postcode's slice by the whole suburb's population would invent
    a low-crime suburb.
    """
    rows = t03.merge(
        pairs[["postcode", "suburb_key", "sal_code"]],
        on=["postcode", "suburb_key"],
        how="inner",
    )

    wide = (
        rows.pivot_table(
            index=["sal_code", "period_end"],
            columns="division",
            values="incidents",
            aggfunc="sum",
            fill_value=0,
        )
        .reset_index()
    )
    wide.columns.name = None

    # A suburb missing from a period had no incidents, rather than unknown ones.
    # Reinstating those zeros keeps the year-on-year columns well defined.
    grid = pd.MultiIndex.from_product(
        [sorted(wide["sal_code"].unique()), sorted(rows["period_end"].unique())],
        names=["sal_code", "period_end"],
    ).to_frame(index=False)
    wide = grid.merge(wide, on=["sal_code", "period_end"], how="left")

    count_cols = [c for c in divisions if c in wide.columns]
    wide[count_cols] = wide[count_cols].fillna(0)
    wide["incidents_total"] = wide[count_cols].sum(axis=1)

    # One 2021 Census denominator for every period, since ABS publishes no
    # yearly suburb population. Year-on-year moves are therefore incident moves,
    # and fast-growing suburbs read slightly high in later periods.
    pop = pairs.drop_duplicates("sal_code")[["sal_code", "crime_suburb_population"]]
    wide = wide.merge(pop, on="sal_code", how="left")

    per_1k = 1000 / wide["crime_suburb_population"]
    wide["crime_suburb_rate_per_1k"] = wide["incidents_total"] * per_1k
    for c in count_cols:
        wide[f"crime_suburb_{c}_rate_per_1k"] = wide[c] * per_1k

    # Incidents are counted where they happen, but the denominator is residents,
    # so workplace-heavy suburbs blow up: Dandenong South has 695 incidents and
    # 125 residents (5,560 per 1,000). The same effect is real signal in a CBD,
    # so no cutoff is applied here. crime_suburb_population is kept so modelling
    # can filter, weight or log-transform as it sees fit.

    wide = wide.sort_values(["sal_code", "period_end"])
    # One row per suburb-year, so shift(1)/shift(3) are the 1- and 3-year lags.
    rate = wide["crime_suburb_rate_per_1k"]
    grp = wide.groupby("sal_code")["crime_suburb_rate_per_1k"]
    wide["crime_suburb_rate_yoy_pct"] = 100 * (rate / grp.shift(1) - 1)
    wide["crime_suburb_rate_3yr_change_pct"] = 100 * (rate / grp.shift(3) - 1)

    keep = ["sal_code", "period_end", "incidents_total", "crime_suburb_population",
            "crime_suburb_rate_per_1k",
            "crime_suburb_rate_yoy_pct", "crime_suburb_rate_3yr_change_pct"]
    keep += [f"crime_suburb_{c}_rate_per_1k" for c in count_cols]
    out = wide[keep].rename(columns={"incidents_total": "crime_suburb_incidents"})
    return out.replace([np.inf, -np.inf], np.nan)

def build_crime_reference(cache_dir: Path, xlsx_path=None):
    """Parse the CSA workbook into the suburb-level table the join needs."""
    t03 = load_crime_table(cache_dir, xlsx_path)

    t03 = t03.rename(columns={
        pick_col(t03, ["Suburb/Town Name"]): "suburb_raw",
        pick_col(t03, ["Incidents Recorded"]): "incidents",
    })
    t03["suburb_key"] = suburb_key(t03["suburb_raw"])
    t03["lga_key"] = area_key(t03["Local Government Area"])
    t03["postcode"] = pd.to_numeric(t03["Postcode"], errors="coerce")
    t03["incidents"] = numeric(t03["incidents"]).fillna(0)
    t03["period_end"] = csa_period_end(t03["Year"], t03["Year ending"])

    # "A Crimes against the person" -> person.
    letter = t03["Offence Division"].astype(str).str.strip().str[0].str.upper()
    t03["division"] = letter.map(CSA_OFFENCE_DIVISIONS)

    t03 = t03.dropna(subset=["postcode", "period_end", "division"]).copy()
    t03["postcode"] = t03["postcode"].astype("int64")

    sal = load_sal_population()
    if sal.empty:
        raise ValueError("ABS SAL population layer returned no Victorian suburbs")

    pairs = resolve_suburb_to_sal(t03, sal)
    divisions = sorted(set(CSA_OFFENCE_DIVISIONS.values()))

    return {
        "suburb": build_suburb_crime(t03, pairs, divisions),
        "pairs": pairs,
        "latest_period": t03["period_end"].max(),
    }

def add_crime_features(master, crime, coverage, tolerance_days=370):
    """
    Attach Crime Statistics Victoria as it stood when each listing was advertised.

    A backward as-of join of the suburb rate onto the listing's ABS suburb.
    Backward rather than nearest avoids leakage - a nearest match would give a
    mid-2024 listing the year ending March 2025, half of which had not yet
    happened.

    tolerance_days defaults to 370: one reporting period plus slack. A listing
    advertised just before a period closes is legitimately ~364 days from the
    last published one, while a larger gap means the listing predates the data
    and should stay null rather than borrow a later number.
    """
    if "suburb" not in master.columns or "postcode" not in master.columns:
        record_source(
            coverage, SOURCE_NAME, "skipped",
            "listings lack suburb/postcode, so no crime join is possible",
        )
        return master
    if "date_listed" not in master.columns:
        record_source(
            coverage, SOURCE_NAME, "skipped",
            "listings lack date_listed, so no as-of join is possible",
        )
        return master

    left = pd.DataFrame({
        "_row": np.arange(len(master)),
        "suburb_key": suburb_key(master["suburb"]),
        "postcode": pd.to_numeric(master["postcode"], errors="coerce"),
        "date_listed": pd.to_datetime(master["date_listed"], errors="coerce").astype("datetime64[ns]"),
    }).dropna(subset=["postcode", "date_listed"])
    left["postcode"] = left["postcode"].astype("int64")

    left = left.merge(
        crime["pairs"][["postcode", "suburb_key", "sal_code", "crime_sal_ambiguous"]],
        on=["postcode", "suburb_key"],
        how="left",
    )

    # Suburb-only, so a listing CSA does not name by suburb gets nothing.
    suburb_matched = pd.merge_asof(
        left.dropna(subset=["sal_code"]).sort_values("date_listed"),
        crime["suburb"].sort_values("period_end"),
        left_on="date_listed",
        right_on="period_end",
        by="sal_code",
        direction="backward",
        tolerance=pd.Timedelta(days=tolerance_days),
    )

    suburb_cols = [c for c in crime["suburb"].columns
                   if c not in ("sal_code", "period_end")]

    out = pd.DataFrame(index=np.arange(len(master)))
    out = out.join(suburb_matched.set_index("_row")[suburb_cols])
    out = out.join(left.set_index("_row")[["crime_sal_ambiguous"]])

    out.index = master.index
    master = pd.concat([master, out], axis=1)
    # Incidents are rate x population, and the ambiguity flag is only useful as a
    # count in the coverage note below.
    n_ambiguous = int(master["crime_sal_ambiguous"].fillna(0).sum())
    master = master.drop(columns=["crime_suburb_incidents", "crime_sal_ambiguous"])

    n_suburb = int(left["sal_code"].notna().sum())
    n_rate = int(master["crime_suburb_rate_per_1k"].notna().sum())
    latest = pd.to_datetime(crime["latest_period"])
    record_source(
        coverage, SOURCE_NAME, "joined",
        f"as-of join on (suburb, date_listed), tolerance {tolerance_days}d, "
        f"data to year ending {latest:%b %Y}; "
        f"{n_suburb:,}/{len(master):,} listings matched an ABS suburb and "
        f"{n_rate:,} carry a suburb rate; {n_ambiguous:,} matched an ambiguous "
        f"suburb name to its more populous candidate",
    )
    return master
