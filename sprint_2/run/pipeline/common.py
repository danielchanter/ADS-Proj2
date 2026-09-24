"""Paths, HTTP helpers, key normalisation and the source-coverage audit."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import requests

# ---------------------------------------------------------------------
# PATHS
# ---------------------------------------------------------------------

RUN_DIR = Path(__file__).resolve().parent.parent
SPRINT_DIR = RUN_DIR.parent
REPO_DIR = SPRINT_DIR.parent

# The supplied Domain snapshot. fetch_sqm_rents.py reads its postcodes from it.
DOMAIN_CSV = REPO_DIR / "domain" / "Data" / "vic_rentals_all.csv"
OUTPUT_DIR = SPRINT_DIR / "data" / "processed"
EXTERNAL_DIR = REPO_DIR / "data" / "external"

# crimestatistics.vic.gov.au and SQM answer 403 to the default requests user agent.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)
BROWSER_HEADERS = {"User-Agent": BROWSER_UA}

# Sent to the open-data APIs that ask callers to identify themselves.
RESEARCH_UA = "ADS-Proj2-Victorian-Rental-Research/1.0"

# ---------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------

def get_json(url, params=None, timeout=120):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

def cached_download(url, path: Path, timeout=180):
    if not path.exists():
        r = requests.get(url, headers=BROWSER_HEADERS, timeout=timeout)
        r.raise_for_status()
        path.write_bytes(r.content)
    return path

def arcgis_geojson(url, where="1=1", out_fields="*", return_geometry=True):
    params = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": str(return_geometry).lower(),
        "f": "geojson",
        "outSR": 4326,
    }
    r = requests.get(url, params=params, timeout=180)
    r.raise_for_status()
    data = r.json()
    if return_geometry:
        return gpd.GeoDataFrame.from_features(data["features"], crs="EPSG:4326")
    return pd.DataFrame([f["properties"] for f in data["features"]])

def arcgis_query_paged(url, where="1=1", out_fields="*", order_by=None, page_size=2000):
    """
    Attribute query that follows the service's paging instead of silently stopping
    at maxRecordCount. The SAL layer holds 2,944 Victorian suburbs against a 2,000
    record limit, so a single request would drop a fifth of the state.
    """
    rows = []
    offset = 0
    while True:
        params = {
            "where": where,
            "outFields": out_fields,
            "returnGeometry": "false",
            "f": "json",
            "resultOffset": offset,
            "resultRecordCount": page_size,
        }
        if order_by:
            params["orderByFields"] = order_by
        r = requests.get(url, params=params, timeout=180)
        r.raise_for_status()
        data = r.json()
        features = data.get("features", [])
        rows.extend(f["attributes"] for f in features)
        if len(features) < page_size:
            break
        offset += len(features)
    return pd.DataFrame(rows)

# ---------------------------------------------------------------------
# KEYS AND COLUMNS
# ---------------------------------------------------------------------

def normalize_code(series):
    return series.astype(str).str.replace(r"\.0$", "", regex=True).str.strip()

def suburb_key(series):
    """
    Suburb names to a common form so Domain, CSA and ABS SAL can be matched.

    The trailing parenthetical is dropped because ABS disambiguates repeated
    names with it ("Abbotsford (Vic.)", "Hillside (Melton - Vic.)") while Domain
    and CSA do not.
    """
    s = series.astype(str).str.upper().str.strip()
    s = s.str.replace(r"\s*\([^)]*\)\s*$", "", regex=True)
    return s.str.replace(r"[^A-Z0-9]+", " ", regex=True).str.strip()

def area_key(series):
    """Same normalisation for LGA names ('Colac-Otway' -> 'COLAC OTWAY')."""
    s = series.astype(str).str.upper().str.strip()
    return s.str.replace(r"[^A-Z0-9]+", " ", regex=True).str.strip()

def pick_col(df, candidates):
    lut = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lut:
            return lut[c.lower()]
    return None

def numeric(series):
    return pd.to_numeric(series, errors="coerce")

# ---------------------------------------------------------------------
# SOURCE COVERAGE
# ---------------------------------------------------------------------

def record_source(coverage, name, status, detail="", matches=np.nan, total=np.nan):
    """Append one row to the source_coverage.csv audit."""
    coverage.append({
        "source": name,
        "victorian_sa2_matches": matches,
        "victorian_sa2_total": total,
        "status": status,
        "detail": detail,
    })
