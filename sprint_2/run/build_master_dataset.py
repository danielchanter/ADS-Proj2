
"""
Build a single property-level Victorian rental master table.

Input:
    vic_rentals_all.csv

Output:
    output/vic_property_master.csv
    output/sa2_master.csv
    output/sa2_yearly.csv       one row per SA2-year, for forecasting
    output/vic_property_rent_ratios.csv   target-derived columns, keyed on listing_id
    output/source_coverage.csv

Core enrichments:
  * ABS ASGS 2021 SA2 geography
  * ABS 2021 General Community Profile (selected useful fields)
  * ABS SEIFA 2021 (all four index scores)
  * ABS Regional Population 2025, plus the yearly SA2 series 2001-2025
  * ABS Personal Income, SA2, 2016-17 to 2022-23 (three releases stacked);
    2022-23 is joined to listings, every year goes to sa2_yearly.csv
  * Victoria in Future 2023 SA2 projections to 2036
  * Victorian School Locations 2025
  * Metro train stations accessibility dataset
  * Optional OpenStreetMap amenity counts/distances using Overpass
  * SQM Research weekly postcode rent index, joined as-of each listing date
    (run sprint_2/run/fetch_sqm_rents.py first to produce it)
  * Crime Statistics Victoria LGA Criminal Incidents, joined as-of each listing
    date at suburb level (rate per 1,000 residents)

Install:
    pip install pandas geopandas shapely requests pyogrio openpyxl scipy

Usage:
    python sprint_2/run/build_master_dataset.py   # or run.py, which wraps it

Notes:
  * No OpenRouteService API key is required because no destination/commute target
    was specified. Add route-time features only once a meaningful destination
    (e.g. Melbourne CBD, nearest university, workplace) is defined.
  * OSM enrichment is optional because Overpass can be slow/rate-limited.
"""

from __future__ import annotations

import argparse
import calendar
import io
import json
import re
import time
from pathlib import Path
from urllib.parse import unquote

import numpy as np
import pandas as pd
import geopandas as gpd
import requests
from scipy.spatial import cKDTree

# ---------------------------------------------------------------------
# VERIFIED PUBLIC SOURCES
# ---------------------------------------------------------------------

SA2_URL = (
    "https://geo.abs.gov.au/arcgis/rest/services/ASGS2021/SA2/"
    "FeatureServer/0/query"
)

GCP_URL = (
    "https://services1.arcgis.com/vHnIGBHHqDR6y0CR/ArcGIS/rest/services/"
    "2021_ABS_General_Community_Profile/FeatureServer/4/query"
)

# Layer 5 of the same service is the Census GCP on Suburbs and Localities (SAL).
# It is the population denominator for the suburb-level crime counts, which the
# Crime Statistics Agency publishes as raw incidents with no denominator.
GCP_SAL_URL = (
    "https://services1.arcgis.com/vHnIGBHHqDR6y0CR/ArcGIS/rest/services/"
    "2021_ABS_General_Community_Profile/FeatureServer/5/query"
)

SEIFA_URL = (
    "https://geo.abs.gov.au/arcgis/rest/services/Hosted/"
    "ABS_Socio_Economic_Indexes_for_Areas_SEIFA_by_2021_SA2/"
    "FeatureServer/0/query"
)

REGIONAL_POP_URL = (
    "https://geo.abs.gov.au/arcgis/rest/services/Hosted/"
    "SA2_Regional_Population_2025/FeatureServer/3/query"
)

VIF_URL = (
    "https://www.planning.vic.gov.au/__data/assets/excel_doc/0028/691660/"
    "VIF2023_SA2_Pop_Hhold_Dwelling_Projections_to_2036_Release_2.xlsx"
)

# Population estimates by SA2, 30 June 2001 to 2025, on ASGS 2021 boundaries.
# The ArcGIS layer above only carries the latest two years; this is the history.
REGIONAL_POP_HISTORY_URL = (
    "https://www.abs.gov.au/statistics/people/population/regional-population/"
    "2024-25/32180DS0003_2001-25.xlsx"
)

# Personal Income in Australia, Table 1. Each release covers five financial
# years. All three below use ASGS 2021 SA2 codes, so stacking them extends the
# series back to 2016-17; the 2019-20 release and earlier use 2016 boundaries.
# Newest first: where releases overlap, the newest (revised) figure is kept.
_PERSONAL_INCOME_BASE = (
    "https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/"
    "personal-income-australia/"
)
_PERSONAL_INCOME_TABLE = (
    "Table%201%20-%20Total%20income%2C%20earners%20and%20summary%20statistics%20"
    "by%20geography%2C%20{first}%20to%20{release}.xlsx"
)
PERSONAL_INCOME_RELEASES = [
    (release, _PERSONAL_INCOME_BASE + release + "/" +
     _PERSONAL_INCOME_TABLE.format(first=first, release=release))
    for release, first in [
        ("2022-23", "2018-19"),
        ("2021-22", "2017-18"),
        ("2020-21", "2016-17"),
    ]
]
PERSONAL_INCOME_SNAPSHOT_YEAR = "2022-23"

# Personal Income Table 1 measures, in workbook order, and their column names.
PERSONAL_INCOME_MEASURES = {
    "earners": "income_earners",
    "median age": "income_earners_median_age",
    "sum": "income_sum",
    "median": "income_median",
    "mean": "income_mean",
}

SCHOOLS_API = (
    "https://discover.data.vic.gov.au/api/3/action/datastore_search"
)
SCHOOLS_RESOURCE_ID = "d26bf015-a1e5-48dd-a1d6-8edd4b0a511b"

# Data Melbourne export. If the endpoint changes, the script simply skips station enrichment.
STATIONS_URLS = [
    "https://data.melbourne.vic.gov.au/api/explore/v2.1/catalog/datasets/"
    "metro-train-stations-with-accessibility-information/exports/csv",
    "https://data.melbourne.vic.gov.au/api/v2/catalog/datasets/"
    "metro-train-stations-with-accessibility-information/exports/csv",
]

# Preferred statewide transport-stop source. The resource URL is discovered
# dynamically from DataVic so the script is resilient to storage URL changes.
PUBLIC_TRANSPORT_DATASET_ID = "public-transport-lines-and-stops"
DATAVIC_PACKAGE_SHOW = "https://discover.data.vic.gov.au/api/3/action/package_show"

OVERPASS_URLS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
]


# SQM Research Weekly Rents Index, produced by sprint_2/run/fetch_sqm_rents.py.
# Weekly advertised rents per postcode, 2009-08 onwards. This is the only source
# here with a usable rent time series, so it supplies the market-level benchmark
# the Domain snapshot cannot.
SQM_RENTS_CSV = (
    Path(__file__).resolve().parent.parent.parent / "data" / "external" / "sqm_weekly_rents.csv"
)
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

# crimestatistics.vic.gov.au answers 403 to the default requests user agent.
CSA_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# Short column-safe names for the six ANZSOC-style offence divisions CSA uses.
CSA_OFFENCE_DIVISIONS = {
    "A": "person",
    "B": "property",
    "C": "drug",
    "D": "public_order",
    "E": "justice",
    "F": "other",
}


# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------

def get_json(url, params=None, timeout=120):
    r = requests.get(url, params=params, timeout=timeout)
    r.raise_for_status()
    return r.json()

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

def nearest_features(properties, features, lat_col, lon_col, prefix):
    """
    Fast nearest straight-line distance. For metro-scale access variables this is
    generally adequate as a first-pass model feature. Route time should be added
    later with ORS if a specific destination/task requires network routing.
    """
    out = properties.copy()
    valid = features[[lat_col, lon_col]].dropna().copy()
    if valid.empty:
        out[f"nearest_{prefix}_km"] = np.nan
        return out

    # KD tree in radians using 3D unit-sphere coordinates.
    def sphere_xyz(lat, lon):
        lat = np.radians(lat)
        lon = np.radians(lon)
        return np.c_[np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)]

    fxyz = sphere_xyz(valid[lat_col].to_numpy(), valid[lon_col].to_numpy())
    tree = cKDTree(fxyz)

    mask = out["lat"].notna() & out["lon"].notna()
    pxyz = sphere_xyz(out.loc[mask, "lat"].to_numpy(), out.loc[mask, "lon"].to_numpy())
    dist_chord, idx = tree.query(pxyz, k=1)
    # chord -> central angle -> km
    angle = 2 * np.arcsin(np.clip(dist_chord / 2, 0, 1))
    out.loc[mask, f"nearest_{prefix}_km"] = 6371.0088 * angle
    return out

def count_points_in_sa2(points_df, sa2_gdf, lat_col, lon_col, name):
    points = points_df.dropna(subset=[lat_col, lon_col]).copy()
    if points.empty:
        sa2_gdf[f"{name}_count"] = 0
        return sa2_gdf
    pg = gpd.GeoDataFrame(
        points,
        geometry=gpd.points_from_xy(points[lon_col], points[lat_col]),
        crs="EPSG:4326",
    )
    j = gpd.sjoin(
        pg,
        sa2_gdf[["sa2_code_2021", "geometry"]],
        how="left",
        predicate="within",
    )
    counts = j.groupby("sa2_code_2021").size().rename(f"{name}_count")
    return sa2_gdf.merge(counts, on="sa2_code_2021", how="left").fillna({f"{name}_count": 0})

# ---------------------------------------------------------------------
# SOURCE LOADERS
# ---------------------------------------------------------------------

def load_sa2():
    # State code 2 = Victoria.
    g = arcgis_geojson(
        SA2_URL,
        where="state_code_2021='2'",
        # Names only above SA2: each SA3/SA4/GCCSA code maps 1:1 onto its name.
        out_fields=(
            "sa2_code_2021,sa2_name_2021,sa3_name_2021,sa4_name_2021,"
            "gccsa_name_2021,area_albers_sqkm"
        ),
    )
    g["sa2_code_2021"] = normalize_code(g["sa2_code_2021"])
    return g

def load_gcp():
    # The public SA2 service contains selected General Community Profile variables.
    fields = [
        "SA2_CODE_2021","SA2_NAME_2021","Tot_P_P",
        "Age_0_4_yr_P","Age_5_14_yr_P","Age_15_19_yr_P","Age_20_24_yr_P",
        "Age_25_34_yr_P","Age_35_44_yr_P","Age_45_54_yr_P","Age_55_64_yr_P",
        "Age_65_74_yr_P","Age_75_84_yr_P","Age_85ov_P",
        "Birthplace_Australia_P","Birthplace_Elsewhere_P",
        "Lang_used_home_Eng_only_P","Lang_used_home_Oth_Lang_P",
        "Median_age_persons","Median_mortgage_repay_monthly",
        "Median_tot_prsnl_inc_weekly","Median_rent_weekly",
        "Median_tot_fam_inc_weekly","Median_tot_hhd_inc_weekly",
        "Average_household_size",
    ]
    d = arcgis_geojson(GCP_URL, out_fields=",".join(fields), return_geometry=False)
    d = d.rename(columns={"SA2_CODE_2021":"sa2_code_2021","SA2_NAME_2021":"sa2_name_2021_gcp"})
    d["sa2_code_2021"] = normalize_code(d["sa2_code_2021"])
    # Retain Victoria only through SA2 code prefix.
    d = d[d["sa2_code_2021"].str.startswith("2")].copy()

    tot = numeric(d["Tot_P_P"]).replace(0, np.nan)
    d["age_0_14_pct"] = 100*(numeric(d["Age_0_4_yr_P"]) + numeric(d["Age_5_14_yr_P"])) / tot
    d["age_25_44_pct"] = 100*(numeric(d["Age_25_34_yr_P"]) + numeric(d["Age_35_44_yr_P"])) / tot
    d["age_65_plus_pct"] = 100*(numeric(d["Age_65_74_yr_P"]) + numeric(d["Age_75_84_yr_P"]) + numeric(d["Age_85ov_P"])) / tot
    d["born_overseas_pct"] = 100*numeric(d["Birthplace_Elsewhere_P"]) / (
        numeric(d["Birthplace_Australia_P"]) + numeric(d["Birthplace_Elsewhere_P"])
    ).replace(0, np.nan)
    d["non_english_home_pct"] = 100*numeric(d["Lang_used_home_Oth_Lang_P"]) / (
        numeric(d["Lang_used_home_Eng_only_P"]) + numeric(d["Lang_used_home_Oth_Lang_P"])
    ).replace(0, np.nan)

    # Tot_P_P is only the denominator above: erp_2025 is the current population.
    keep = [
        "sa2_code_2021","Median_age_persons",
        "Median_tot_prsnl_inc_weekly","Median_tot_hhd_inc_weekly",
        "Median_rent_weekly","Median_mortgage_repay_monthly",
        "Average_household_size","age_0_14_pct","age_25_44_pct",
        "age_65_plus_pct","born_overseas_pct","non_english_home_pct"
    ]
    return d[keep].rename(columns={
        "Median_age_persons":"census_median_age",
        "Median_tot_prsnl_inc_weekly":"census_median_personal_income_weekly",
        "Median_tot_hhd_inc_weekly":"census_median_household_income_weekly",
        "Median_rent_weekly":"census_median_rent_weekly",
        "Median_mortgage_repay_monthly":"census_median_mortgage_monthly",
        "Average_household_size":"census_avg_household_size",
    })

def load_seifa():
    # Scores only: the national deciles and percentiles are rank transforms of
    # them (r >= 0.95) and add nothing a model can use.
    fields = "sa2_code_2021,irsad_score,irsd_score,ier_score,ieo_score"
    d = arcgis_geojson(SEIFA_URL, out_fields=fields, return_geometry=False)
    d["sa2_code_2021"] = normalize_code(d["sa2_code_2021"])
    return d[d["sa2_code_2021"].str.startswith("2")].copy()

def load_regional_population():
    # erp_2024, the change in persons, and births/deaths/natural increase all
    # restate erp_2025 and its growth rate; the full ERP history is in sa2_yearly.
    fields = (
        "sa2_code_2021,erp_2025,erp_change_per_cent_2024_25,"
        "pop_density_2025_people_per_km2,"
        "net_internal_migration_2024_25,net_overseas_migration_2024_25"
    )
    d = arcgis_geojson(
        REGIONAL_POP_URL,
        where="state_code_2021='2'",
        out_fields=fields,
        return_geometry=False,
    )
    d["sa2_code_2021"] = normalize_code(d["sa2_code_2021"])
    return d.rename(columns={
        "erp_change_per_cent_2024_25":"population_growth_pct_2024_25",
        "pop_density_2025_people_per_km2":"population_density_2025",
    })

def load_vif(cache_dir: Path):
    """
    Projected population growth per SA2 from the VIF 2023 Total_Population sheet.

    Only that sheet is read. The workbook's other sheets (dwellings, households
    and their breakdowns) share the same year headers, so reading them together
    and matching on "2036" mixes household counts into the population figures.
    """
    path = cache_dir / "vif_sa2.xlsx"
    if not path.exists():
        r = requests.get(VIF_URL, timeout=180)
        r.raise_for_status()
        path.write_bytes(r.content)

    raw = pd.read_excel(path, sheet_name="Total_Population", header=None)
    # The header row holds the SA2 code label and the projection years.
    header_i = next(
        (i for i in range(min(len(raw), 30))
         if any("sa2" in str(x).lower() for x in raw.iloc[i])
         and any(str(x).startswith("2036") for x in raw.iloc[i])),
        None,
    )
    if header_i is None:
        print("WARNING: VIF workbook layout not recognised; VIF columns skipped.")
        return pd.DataFrame(columns=["sa2_code_2021"])

    d = pd.read_excel(path, sheet_name="Total_Population", header=header_i)
    d.columns = [str(c).strip().removesuffix(".0") for c in d.columns]
    code = next(c for c in d.columns if "sa2" in c.lower() and "code" in c.lower())
    d["sa2_code_2021"] = normalize_code(d[code])
    d = d[d["sa2_code_2021"].str.fullmatch(r"2\d{8}")].copy()

    # Only the projected change leaves this function: the projected levels are
    # near-copies of erp_2025. 2026-31 matches the five-year horizon the brief
    # asks about; 2026-36 correlates with it at 0.99 so adds nothing.
    out = d[["sa2_code_2021"]].copy()
    start = numeric(d["2026"]).replace(0, np.nan)
    out["vif_population_growth_pct_2026_31"] = 100 * (numeric(d["2031"]) / start - 1)
    return out

def cached_download(url, path: Path, timeout=180):
    if not path.exists():
        r = requests.get(url, headers=CSA_HEADERS, timeout=timeout)
        r.raise_for_status()
        path.write_bytes(r.content)
    return path

def load_regional_population_yearly(cache_dir: Path):
    """
    Estimated resident population per Victorian SA2 at 30 June, 2001 onwards,
    in long format (one row per SA2-year) with the year-on-year growth.

    Header rows in the workbook: the years sit on one row and the SA2 code
    label on the row below, so both are read and combined by position.
    """
    path = cached_download(REGIONAL_POP_HISTORY_URL, cache_dir / "regional_population_2001_25.xlsx")
    raw = pd.read_excel(path, sheet_name="Table 1", header=None)

    label_row = next(
        i for i in range(min(len(raw), 15))
        if any(str(x).strip().lower() == "sa2 code" for x in raw.iloc[i])
    )
    labels = [str(x).strip().lower() for x in raw.iloc[label_row]]
    years = raw.iloc[label_row - 1]
    code_col = labels.index("sa2 code")
    year_cols = {
        j: int(y) for j, y in years.items()
        if re.fullmatch(r"\d{4}", str(y).strip().removesuffix(".0"))
    }

    body = raw.iloc[label_row + 1:]
    codes = normalize_code(body[code_col])
    body = body[codes.str.fullmatch(r"2\d{8}")]

    wide = body[list(year_cols)].rename(columns=year_cols)
    wide.insert(0, "sa2_code_2021", normalize_code(body[code_col]))
    d = wide.melt(id_vars="sa2_code_2021", var_name="year", value_name="erp")
    d["year"] = d["year"].astype(int)
    d["erp"] = pd.to_numeric(d["erp"], errors="coerce")
    d = d.sort_values(["sa2_code_2021", "year"]).reset_index(drop=True)
    d["erp_growth_pct"] = 100 * d.groupby("sa2_code_2021")["erp"].pct_change()
    return d

def read_personal_income_sa2(path: Path, release: str):
    """
    Table 1.4 (SA2) of one Personal Income release, as one row per SA2 and
    financial year.

    The header spans two rows: the measure name (Earners, Median ($), ...) is
    written once above its block of five year columns, and the years are on
    the row below. The measure is forward-filled across its block.
    """
    raw = pd.read_excel(path, sheet_name="Table 1.4", header=None)
    year_row = next(
        i for i in range(min(len(raw), 15))
        if str(raw.iat[i, 0]).strip().upper() == "SA2"
    )
    measures = raw.iloc[year_row - 1].ffill()
    years = raw.iloc[year_row]

    body = raw.iloc[year_row + 1:]
    codes = normalize_code(body[0])
    body = body[codes.str.fullmatch(r"2\d{8}")]

    frames = []
    for j in range(2, raw.shape[1]):
        label = str(measures[j]).strip().lower()
        key = next((k for k in PERSONAL_INCOME_MEASURES if label.startswith(k)), None)
        if key is None or not re.fullmatch(r"\d{4}-\d{2}", str(years[j]).strip()):
            continue
        frames.append(pd.DataFrame({
            "sa2_code_2021": normalize_code(body[0]).to_numpy(),
            "financial_year": str(years[j]).strip(),
            "measure": PERSONAL_INCOME_MEASURES[key],
            # Values are text with thousands separators; "np" (not published)
            # becomes NaN.
            "value": pd.to_numeric(
                body[j].astype(str).str.replace(",", "", regex=False), errors="coerce"
            ).to_numpy(),
        }))

    d = pd.concat(frames, ignore_index=True).pivot_table(
        index=["sa2_code_2021", "financial_year"], columns="measure",
        values="value", aggfunc="first", dropna=False,
    ).reset_index()
    d.columns.name = None
    d["income_release"] = release
    return d

def rescale_to_newer(older: pd.DataFrame, newer: pd.DataFrame):
    """
    Put an older release on the same basis as the newer ones before stacking.

    ABS revised the series in the 2021-22 release: for the same years, the
    2020-21 release has ~8% different earner counts and ~4% different medians,
    while 2021-22 and 2022-23 agree exactly. Each SA2's older figures are
    multiplied by the median newer/older ratio over the years both cover, so
    the years only the older release has do not add a false step to the series.
    Median age is left as is. Releases that already agree get a ratio of 1.
    """
    scaled = [m for m in PERSONAL_INCOME_MEASURES.values() if m != "income_earners_median_age"]
    both = older.merge(newer, on=["sa2_code_2021", "financial_year"], suffixes=("", "_new"))
    ratios = pd.DataFrame({"sa2_code_2021": both["sa2_code_2021"]})
    for m in scaled:
        ratios[m] = both[f"{m}_new"] / both[m]
    ratios = ratios.replace([np.inf, -np.inf], np.nan).groupby("sa2_code_2021").median()

    out = older.set_index("sa2_code_2021")
    factor = ratios.reindex(out.index)
    if (factor.fillna(1) - 1).abs().to_numpy().max() > 1e-9:
        out[scaled] = out[scaled] * factor[scaled]
        out["income_release"] = out["income_release"] + " (rescaled)"
    return out.reset_index()

def load_personal_income_yearly(cache_dir: Path):
    """
    SA2 personal income from every release in PERSONAL_INCOME_RELEASES,
    one row per SA2 and financial year. `year` is the calendar year the
    financial year ends in, so 2022-23 lines up with ERP at 30 June 2023.
    """
    frames = []
    for release, url in PERSONAL_INCOME_RELEASES:
        path = cached_download(url, cache_dir / f"personal_income_{release}.xlsx")
        frames.append(read_personal_income_sa2(path, release))

    # Releases are newest first, so keep="first" keeps the revised figure.
    d = frames[0]
    for older in frames[1:]:
        d = pd.concat([d, rescale_to_newer(older, d)], ignore_index=True)
    d = d.drop_duplicates(["sa2_code_2021", "financial_year"], keep="first")
    d["year"] = d["financial_year"].str[:4].astype(int) + 1
    cols = ["sa2_code_2021", "year", "financial_year", *PERSONAL_INCOME_MEASURES.values(), "income_release"]
    return d[cols].sort_values(["sa2_code_2021", "year"]).reset_index(drop=True)

def personal_income_snapshot(income_yearly: pd.DataFrame, financial_year=PERSONAL_INCOME_SNAPSHOT_YEAR):
    """One financial year of SA2 income, in the wide shape the SA2 master uses."""
    if income_yearly.empty:
        return pd.DataFrame(columns=["sa2_code_2021"])
    suffix = financial_year.replace("-", "_")
    d = income_yearly[income_yearly["financial_year"] == financial_year]
    return d[["sa2_code_2021", "income_median", "income_mean", "income_earners"]].rename(columns={
        "income_median": f"tax_median_total_personal_income_{suffix}",
        "income_mean": f"tax_mean_total_personal_income_{suffix}",
        "income_earners": f"tax_income_earners_{suffix}",
    })

def build_sa2_yearly(population_yearly: pd.DataFrame, income_yearly: pd.DataFrame):
    """
    Time-varying SA2 covariates for the forecasting model, one row per SA2-year.
    Kept apart from the listing table, which is a single snapshot.
    """
    frames = [d for d in (population_yearly, income_yearly) if not d.empty]
    if not frames:
        return pd.DataFrame(columns=["sa2_code_2021", "year"])
    out = frames[0]
    for d in frames[1:]:
        out = out.merge(d, on=["sa2_code_2021", "year"], how="outer")
    return out.sort_values(["sa2_code_2021", "year"]).reset_index(drop=True)

def load_schools():
    params = {"resource_id": SCHOOLS_RESOURCE_ID, "limit": 5000}
    data = get_json(SCHOOLS_API, params=params)
    d = pd.DataFrame(data["result"]["records"])

    lat = next((c for c in d.columns if c.lower() in {"latitude","lat","y"} or "latitude" in c.lower()), None)
    lon = next((c for c in d.columns if c.lower() in {"longitude","lon","lng","x"} or "longitude" in c.lower()), None)
    if not lat or not lon:
        print("WARNING: School coordinates not found; school access skipped.")
        return pd.DataFrame(columns=["school_lat","school_lon"])
    return d.rename(columns={lat:"school_lat", lon:"school_lon"})

def load_stations():
    """
    Preferred source: Department of Transport and Planning statewide
    Public Transport Stops GeoJSON.

    We dynamically discover the current GeoJSON URL from DataVic's CKAN API,
    then keep METRO TRAIN and REGIONAL TRAIN stop points. This is more complete
    and more stable than the older City of Melbourne station-export endpoint.

    Fallback: older Metro Train Stations with Accessibility Information export.
    """
    # 1. Discover statewide GeoJSON resource.
    try:
        pkg = get_json(
            DATAVIC_PACKAGE_SHOW,
            params={"id": PUBLIC_TRANSPORT_DATASET_ID},
            timeout=60,
        )
        resources = pkg.get("result", {}).get("resources", [])

        stop_resources = []
        for r in resources:
            fmt = str(r.get("format", "")).lower()
            name = str(r.get("name", "")).lower()
            url = r.get("url")
            if url and "stop" in name and ("geojson" in fmt or "geo+json" in str(r.get("mimetype", "")).lower()):
                stop_resources.append(url)

        for url in stop_resources:
            try:
                r = requests.get(
                    url,
                    headers={
                        "User-Agent": "ADS-Proj2-Victorian-Rental-Research/1.0",
                        "Accept": "application/geo+json, application/json",
                    },
                    timeout=180,
                )
                r.raise_for_status()
                gj = r.json()

                rows = []
                for feat in gj.get("features", []):
                    props = feat.get("properties", {}) or {}
                    geom = feat.get("geometry", {}) or {}
                    coords = geom.get("coordinates")

                    mode = str(
                        props.get("MODE", props.get("mode", ""))
                    ).upper().strip()

                    if mode not in {"METRO TRAIN", "REGIONAL TRAIN"}:
                        continue

                    if geom.get("type") != "Point" or not coords or len(coords) < 2:
                        continue

                    lon, lat = coords[0], coords[1]

                    rows.append({
                        "station_id": props.get("STOP_ID", props.get("stop_id")),
                        "station_name": props.get("STOP_NAME", props.get("stop_name")),
                        "station_mode": mode,
                        "station_lat": pd.to_numeric(lat, errors="coerce"),
                        "station_lon": pd.to_numeric(lon, errors="coerce"),
                    })

                d = pd.DataFrame(rows)
                if not d.empty:
                    d = d.dropna(subset=["station_lat", "station_lon"])
                    # Platform-level feeds may contain several records at the same
                    # location/name. Deduplicate before nearest-distance calculation.
                    d = d.drop_duplicates(
                        subset=["station_name", "station_lat", "station_lon"]
                    )
                    print(
                        f"Loaded {len(d):,} train station/stop points "
                        "from statewide Public Transport Stops."
                    )
                    return d
            except Exception as e:
                print(f"WARNING: statewide station resource failed: {e}")

    except Exception as e:
        print(f"WARNING: DataVic station-resource discovery failed: {e}")

    # 2. Fallback to old City of Melbourne export.
    for url in STATIONS_URLS:
        try:
            r = requests.get(url, params={"delimiter": ","}, timeout=60)
            r.raise_for_status()
            d = pd.read_csv(io.StringIO(r.text))
            lat = next((c for c in d.columns if "lat" in c.lower()), None)
            lon = next((c for c in d.columns if "lon" in c.lower()), None)
            if lat and lon:
                print("Loaded fallback Metro station accessibility dataset.")
                return d.rename(columns={lat:"station_lat", lon:"station_lon"})
        except Exception:
            continue

    print("WARNING: Train station datasets unavailable; station access skipped.")
    return pd.DataFrame(
        columns=[
            "station_id", "station_name", "station_mode",
            "station_lat", "station_lon"
        ]
    )

# ---------------------------------------------------------------------
# OPTIONAL OSM AMENITIES
# ---------------------------------------------------------------------

OSM_AMENITIES = {
    "supermarket": [('shop','supermarket')],
    "cafe": [('amenity','cafe')],
    "restaurant": [('amenity','restaurant')],
    "bar_pub": [('amenity','bar'),('amenity','pub')],
    "gym": [('leisure','fitness_centre')],
    "childcare": [('amenity','childcare')],
    "hospital": [('amenity','hospital')],
    # amenity=doctors is OSM's primary GP tag; the secondary healthcare=doctor
    # tag is on nearly the same features and Overpass times out on it statewide.
    "gp_clinic": [('amenity','clinic'),('amenity','doctors')],
    "library": [('amenity','library')],
    "park": [('leisure','park')],
    "shopping_centre": [('shop','mall')],
}

def overpass_bbox_query(tags, south, west, north, east):
    """
    One amenity category across the bounding box, or None if every mirror failed.

    Categories are queried one at a time: a single statewide query for all of
    them is heavy enough that the public servers reject or time it out.
    """
    clauses = "".join(
        f'nwr["{key}"="{val}"]({south},{west},{north},{east});' for key, val in tags
    )
    q = "[out:json][timeout:180];(" + clauses + ");out center tags;"

    # overpass-api.de answers "Accept: application/json" with 406/504; the JSON
    # body comes back fine under a wildcard.
    headers = {
        "User-Agent": "ADS-Proj2-Victorian-Rental-Research/1.0",
        "Accept": "*/*",
    }

    errors = []
    # Two passes: 429/504 from these servers usually just means busy right now.
    for attempt in range(2):
        if attempt:
            time.sleep(30)
        for url in OVERPASS_URLS:
            try:
                r = requests.post(url, data={"data": q}, headers=headers, timeout=240)
                if r.ok:
                    return r.json().get("elements", [])
                errors.append(f"{url} -> HTTP {r.status_code}")
            except Exception as e:
                errors.append(f"{url} -> {type(e).__name__}: {e}")

    for err in errors:
        print("  ", err)
    return None

def load_osm_amenities(sa2, cache_dir):
    """
    Amenity points for every OSM_AMENITIES category, cached per category so a
    failed category is retried on the next run without refetching the rest.
    """
    minx, miny, maxx, maxy = sa2.total_bounds
    rows = []
    for label, tags in OSM_AMENITIES.items():
        cache = cache_dir / f"osm_{label}.json"
        elems = None
        if cache.exists():
            try:
                elems = json.loads(cache.read_text())
            except Exception:
                elems = None
        if elems is None:
            print(f"Fetching OSM {label}...")
            elems = overpass_bbox_query(tags, miny, minx, maxy, maxx)
            if elems is None:
                # Overpass is optional: a failed category must not stop the build.
                print(f"WARNING: OSM {label} unavailable; skipped.")
                continue
            cache.write_text(json.dumps(elems))

        for e in elems:
            lat = e.get("lat", e.get("center", {}).get("lat"))
            lon = e.get("lon", e.get("center", {}).get("lon"))
            if lat is not None and lon is not None:
                rows.append({"amenity_type": label, "amenity_lat": lat, "amenity_lon": lon})

    return pd.DataFrame(rows, columns=["amenity_type", "amenity_lat", "amenity_lon"])


# ---------------------------------------------------------------------
# SOURCE COVERAGE AND SQM RENT INDEX
# ---------------------------------------------------------------------

def record_extra_source_status(coverage, name, status, detail=""):
    coverage.append({
        "source": name,
        "victorian_sa2_matches": np.nan,
        "victorian_sa2_total": np.nan,
        "status": status,
        "detail": detail,
    })

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


def add_sqm_market_features(master, sqm, coverage, tolerance_days=31):
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
    source_name = "SQM Research Weekly Rents Index"

    if sqm.empty:
        record_extra_source_status(
            coverage, source_name, "missing",
            f"{SQM_RENTS_CSV} not found; run sprint_2/run/fetch_sqm_rents.py first",
        )
        return master
    if "date_listed" not in master.columns or "postcode" not in master.columns:
        record_extra_source_status(
            coverage, source_name, "skipped",
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
    record_extra_source_status(
        coverage, source_name, "joined",
        f"as-of join on (postcode, date_listed), tolerance {tolerance_days}d; "
        f"{n_matched:,}/{len(master):,} listings matched a market rent",
    )
    return master


# ---------------------------------------------------------------------
# CRIME STATISTICS VICTORIA
# ---------------------------------------------------------------------

def csa_workbook_url():
    """
    Current "LGA Criminal Incidents" workbook URL, read off the CSA download page.

    CSA republishes quarterly under a new folder with a new filename suffix, so
    a hard-coded URL goes stale within months.
    """
    try:
        r = requests.get(CSA_DOWNLOAD_PAGE, headers=CSA_HEADERS, timeout=120)
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
        url = csa_workbook_url()
        r = requests.get(url, headers=CSA_HEADERS, timeout=300)
        r.raise_for_status()
        path.write_bytes(r.content)

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
    source_name = "Crime Statistics Victoria"

    if "suburb" not in master.columns or "postcode" not in master.columns:
        record_extra_source_status(
            coverage, source_name, "skipped",
            "listings lack suburb/postcode, so no crime join is possible",
        )
        return master
    if "date_listed" not in master.columns:
        record_extra_source_status(
            coverage, source_name, "skipped",
            "listings lack date_listed, so no as-of join is possible",
        )
        return master

    left = pd.DataFrame({
        "_row": np.arange(len(master)),
        "suburb_key": suburb_key(master["suburb"]),
        "postcode": pd.to_numeric(master["postcode"], errors="coerce"),
        "date_listed": pd.to_datetime(master["date_listed"], errors="coerce"),
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
    record_extra_source_status(
        coverage, source_name, "joined",
        f"as-of join on (suburb, date_listed), tolerance {tolerance_days}d, "
        f"data to year ending {latest:%b %Y}; "
        f"{n_suburb:,}/{len(master):,} listings matched an ABS suburb and "
        f"{n_rate:,} carry a suburb rate; {n_ambiguous:,} matched an ambiguous "
        f"suburb name to its more populous candidate",
    )
    return master


# ---------------------------------------------------------------------
# LISTING FEATURES
# ---------------------------------------------------------------------

# Domain's structured_features holds 571 distinct free-text labels, many of them
# synonyms ("Balcony / Deck", "Balcony"; four spellings of split-system air con).
# Each flag below gathers the labels that mean the same thing. "Close to shops"
# and similar agent claims are left out: the measured distances cover them.
STRUCTURED_FEATURE_FLAGS = {
    "built_in_wardrobes": r"built.?in wardrobe",
    "heating": r"heating|fireplace|heater",
    "air_conditioning": r"air.?con|cooling",
    "dishwasher": r"dishwasher",
    "secure_parking": r"secure parking|garage|carport",
    "internal_laundry": r"internal laundry",
    "balcony": r"balcony|deck|terrace",
    "outdoor_space": r"garden|courtyard|outdoor entertain|fully fenced|backyard",
    "floorboards": r"floorboard|timber floor",
    "bath": r"^bath(?:tub)?$",
    "ensuite": r"ensuite",
    "study": r"study",
    "furnished": r"^(?:fully |partly |partially )?furnished",
    "pets_allowed": r"pets? allowed|pet friendly",
    "pool": r"swimming pool|^pool$|pool.?(?:in.?ground|above)",
    "gym": r"\bgym\b",
    "solar": r"solar",
    "security": r"intercom|alarm|security",
}

def add_structured_feature_flags(p):
    """
    One 0/1 column per STRUCTURED_FEATURE_FLAGS entry, replacing the raw text.

    A listing with no feature list gets <NA> in every flag rather than 0: the
    agent not filling the field says nothing about whether it has a dishwasher.
    """
    if "structured_features" not in p.columns:
        return p
    raw = p["structured_features"]
    tokens = raw.fillna("").astype(str).str.split(r",\s*").explode().str.strip().str.lower()
    for name, pattern in STRUCTURED_FEATURE_FLAGS.items():
        hit = tokens.str.contains(pattern, regex=True).groupby(level=0).any()
        p[f"feat_{name}"] = hit.astype("Int8").where(raw.notna())
    return p.drop(columns="structured_features")

# Columns computed from weekly_rent. They go to vic_property_rent_ratios.csv, not
# the master table, so they cannot end up as predictors of the rent they divide.
# bond is here too: it is set from the rent, almost always one month of it.
TARGET_DERIVED_COLS = [
    "bond",
    "rent_per_bedroom",
    "rent_vs_2021_census_median",
    "rent_vs_sqm_market",
    "rent_to_area_median_hh_income_pct",
]

# A Victorian bond is capped at one month's rent unless rent exceeds $900/week,
# which puts nearly all of them at 4.35 weeks. Outside 2-8 weeks is a data error.
BOND_WEEKS_RANGE = (2, 8)

def split_target_derived(master):
    """Return (master without rent-derived columns, those columns by listing_id)."""
    rent = pd.to_numeric(master["weekly_rent"], errors="coerce").replace(0, np.nan)

    def ratio_to(col):
        if col not in master.columns:
            return np.nan
        return rent / pd.to_numeric(master[col], errors="coerce").replace(0, np.nan)

    ratios = pd.DataFrame({"listing_id": master["listing_id"]})
    if "bond" in master.columns:
        weeks = pd.to_numeric(master["bond"], errors="coerce") / rent
        ratios["bond"] = master["bond"].where(weeks.between(*BOND_WEEKS_RANGE))
    if "bedrooms" in master.columns:
        ratios["rent_per_bedroom"] = ratio_to("bedrooms")
    ratios["rent_vs_2021_census_median"] = ratio_to("census_median_rent_weekly")
    ratios["rent_vs_sqm_market"] = ratio_to("sqm_market_rent")
    # Affordability for Q3: listing rent against the area's weekly household income.
    ratios["rent_to_area_median_hh_income_pct"] = 100 * ratio_to(
        "census_median_household_income_weekly"
    )

    master = master.drop(columns=[c for c in TARGET_DERIVED_COLS if c in master.columns])
    return master, ratios

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    # Same file fetch_sqm_rents.py reads its postcodes from.
    default_input = Path(__file__).resolve().parents[2] / "domain" / "Data" / "vic_rentals_all.csv"
    ap.add_argument("--input", default=str(default_input))
    default_output = Path(__file__).resolve().parent.parent / "data" / "processed"
    ap.add_argument("--output-dir", default=str(default_output))
    ap.add_argument("--no-osm", action="store_true",
                    help="Skip OpenStreetMap amenity enrichment (OSM is included by default).")
    ap.add_argument("--sqm-csv", default=str(SQM_RENTS_CSV),
                    help="SQM weekly rents CSV from fetch_sqm_rents.py.")
    ap.add_argument("--no-sqm", action="store_true",
                    help="Skip the SQM postcode rent-index join.")
    ap.add_argument("--sqm-tolerance-days", type=int, default=31,
                    help="Largest gap allowed between a listing date and the SQM week matched to it.")
    ap.add_argument("--no-crime", action="store_true",
                    help="Skip the Crime Statistics Victoria join.")
    ap.add_argument("--crime-xlsx", default=None,
                    help="Local CSA LGA Criminal Incidents workbook (default: download and cache).")
    ap.add_argument("--crime-tolerance-days", type=int, default=370,
                    help="Largest gap allowed between a listing date and the crime period matched to it.")
    args = ap.parse_args()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    cache = outdir / "_cache"
    cache.mkdir(exist_ok=True)

    # 1. Property table ------------------------------------------------
    p = pd.read_csv(args.input)

    # Drop scraper/admin/low-information fields. secondary_type is left out
    # because it equals property_type on every listing.
    keep = [
        "listing_id","suburb","postcode","weekly_rent","bond","available_date",
        "date_listed","days_listed","bedrooms","bathrooms","carspaces",
        "property_type","address","lat","lon","primary_type",
        "structured_features","url"
    ]
    p = p[[c for c in keep if c in p.columns]].copy()
    p["postcode"] = p["postcode"].astype("Int64")
    for c in ["weekly_rent","bond","days_listed","bedrooms","bathrooms","carspaces","lat","lon"]:
        if c in p:
            p[c] = pd.to_numeric(p[c], errors="coerce")
    for c in ["date_listed"]:
        if c in p:
            p[c] = pd.to_datetime(p[c], errors="coerce")

    p = add_structured_feature_flags(p)

    # 2. Geographic join ----------------------------------------------
    sa2 = load_sa2()
    prop_geo = gpd.GeoDataFrame(
        p,
        geometry=gpd.points_from_xy(p["lon"], p["lat"]),
        crs="EPSG:4326",
    )

    valid = prop_geo.geometry.notna() & prop_geo["lat"].notna() & prop_geo["lon"].notna()
    joined_valid = gpd.sjoin(
        prop_geo.loc[valid],
        sa2,
        how="left",
        predicate="within",
    ).drop(columns=["index_right"], errors="ignore")

    joined = prop_geo.copy()

    # Geographic identifiers/names are strings. Do NOT initialise these with np.nan,
    # because pandas will infer float64 and then reject SA2 codes such as "212021453".
    geo_string_cols = [
        "sa2_code_2021","sa2_name_2021",
        "sa3_name_2021","sa4_name_2021","gccsa_name_2021"
    ]
    geo_numeric_cols = ["area_albers_sqkm"]

    for c in geo_string_cols:
        joined[c] = pd.Series(pd.NA, index=joined.index, dtype="string")

    for c in geo_numeric_cols:
        joined[c] = np.nan

    # Assign each group separately so pandas preserves the correct dtypes.
    for c in geo_string_cols:
        joined.loc[joined_valid.index, c] = (
            joined_valid[c].astype("string").to_numpy()
        )

    for c in geo_numeric_cols:
        joined.loc[joined_valid.index, c] = pd.to_numeric(
            joined_valid[c], errors="coerce"
        ).to_numpy()

    joined["sa2_code_2021"] = normalize_code(joined["sa2_code_2021"])

    # 3. Area-level sources -------------------------------------------
    coverage = []

    # Yearly SA2 series, for the forecasting model. A failure here should not
    # stop the listing table from being built.
    yearly_sources = [
        ("ABS Regional Population 2001-2025 (yearly)", load_regional_population_yearly),
        ("ABS Personal Income 2016-17 to 2022-23 (yearly)", load_personal_income_yearly),
    ]
    yearly_frames = []
    for name, loader in yearly_sources:
        try:
            d = loader(cache)
            detail = f"{d['year'].min()}-{d['year'].max()}, {d['sa2_code_2021'].nunique()} SA2s"
            record_extra_source_status(coverage, name, "joined", f"sa2_yearly.csv: {detail}")
        except Exception as e:
            print(f"WARNING: {name} failed: {e}")
            record_extra_source_status(coverage, name, "failed", str(e))
            d = pd.DataFrame(columns=["sa2_code_2021", "year"])
        yearly_frames.append(d)
    population_yearly, income_yearly = yearly_frames
    sa2_yearly = build_sa2_yearly(population_yearly, income_yearly)

    source_frames = [
        ("ABS Census 2021 GCP", load_gcp()),
        ("ABS SEIFA 2021", load_seifa()),
        ("ABS Regional Population 2025", load_regional_population()),
        (f"ABS Personal Income {PERSONAL_INCOME_SNAPSHOT_YEAR}", personal_income_snapshot(income_yearly)),
        ("Victoria in Future 2023", load_vif(cache)),
    ]

    sa2_master = sa2.drop(columns="geometry").copy()
    for name, d in source_frames:
        if "sa2_code_2021" not in d.columns:
            continue
        before = len(sa2_master)
        matched = sa2_master["sa2_code_2021"].isin(d["sa2_code_2021"]).sum()
        coverage.append({
            "source": name,
            "victorian_sa2_matches": int(matched),
            "victorian_sa2_total": int(before),
            "status": "joined",
            "detail": "Joined to SA2 master"
        })
        sa2_master = sa2_master.merge(d, on="sa2_code_2021", how="left")

    # 4. Schools -------------------------------------------------------
    schools = load_schools()
    if not schools.empty:
        schools["school_lat"] = pd.to_numeric(schools["school_lat"], errors="coerce")
        schools["school_lon"] = pd.to_numeric(schools["school_lon"], errors="coerce")
        sa2_with_school_counts = count_points_in_sa2(
            schools, sa2[["sa2_code_2021","geometry"]].copy(),
            "school_lat","school_lon","school"
        )
        sa2_master = sa2_master.merge(
            sa2_with_school_counts[["sa2_code_2021","school_count"]],
            on="sa2_code_2021", how="left"
        )
        joined = nearest_features(joined, schools, "school_lat","school_lon","school")

    # 5. Stations ------------------------------------------------------
    stations = load_stations()
    if not stations.empty:
        stations["station_lat"] = pd.to_numeric(stations["station_lat"], errors="coerce")
        stations["station_lon"] = pd.to_numeric(stations["station_lon"], errors="coerce")

        # Listing-level nearest train station distance.
        joined = nearest_features(
            joined, stations,
            "station_lat", "station_lon",
            "train_station"
        )

        # Area-level number of train stops/stations within each SA2.
        sa2_with_station_counts = count_points_in_sa2(
            stations,
            sa2[["sa2_code_2021", "geometry"]].copy(),
            "station_lat",
            "station_lon",
            "train_station"
        )
        sa2_master = sa2_master.merge(
            sa2_with_station_counts[
                ["sa2_code_2021", "train_station_count"]
            ],
            on="sa2_code_2021",
            how="left",
        )

    # 6. Optional OSM --------------------------------------------------
    if not args.no_osm:
        osm = load_osm_amenities(sa2, cache)
        if not osm.empty:
            og = gpd.GeoDataFrame(
                osm,
                geometry=gpd.points_from_xy(osm["amenity_lon"], osm["amenity_lat"]),
                crs="EPSG:4326",
            )
            oj = gpd.sjoin(
                og,
                sa2[["sa2_code_2021","geometry"]],
                how="left",
                predicate="within",
            )
            counts = (
                oj.dropna(subset=["sa2_code_2021"])
                  .groupby(["sa2_code_2021","amenity_type"])
                  .size().unstack(fill_value=0)
                  .add_prefix("osm_").add_suffix("_count")
                  .reset_index()
            )
            sa2_master = sa2_master.merge(counts, on="sa2_code_2021", how="left")

            for cat in sorted(osm["amenity_type"].unique()):
                sub = osm[osm["amenity_type"] == cat]
                joined = nearest_features(
                    joined,
                    sub.rename(columns={"amenity_lat":"xlat","amenity_lon":"xlon"}),
                    "xlat","xlon",cat
                )

    # 7. Merge SA2 master back to listings ----------------------------
    master = joined.drop(columns="geometry").merge(
        sa2_master,
        on="sa2_code_2021",
        how="left",
        suffixes=("","_area"),
    )

    # 7b. SQM postcode rent index, as of each listing's date_listed ----
    if args.no_sqm:
        record_extra_source_status(
            coverage, "SQM Research Weekly Rents Index", "skipped", "--no-sqm"
        )
    else:
        master = add_sqm_market_features(
            master,
            load_sqm_weekly_rents(Path(args.sqm_csv)),
            coverage,
            tolerance_days=args.sqm_tolerance_days,
        )

    # 7c. Crime Statistics Victoria, as of each listing's date_listed ---
    if args.no_crime:
        record_extra_source_status(
            coverage, "Crime Statistics Victoria", "skipped", "--no-crime"
        )
    else:
        try:
            master = add_crime_features(
                master,
                build_crime_reference(cache, args.crime_xlsx),
                coverage,
                tolerance_days=args.crime_tolerance_days,
            )
        except Exception as e:
            print(f"WARNING: crime enrichment failed: {e}")
            record_extra_source_status(
                coverage, "Crime Statistics Victoria", "failed", str(e)
            )

    # Avoid duplicate area-name columns if any.
    master = master.loc[:, ~master.columns.duplicated()].copy()

    # SA2 attributes arrive twice, once from the spatial join and once with the
    # SA2 master; drop the second copy and the SA2 master's geometry column.
    master = master.drop(
        columns=[c for c in master.columns if c.endswith("_area")] + ["shape"],
        errors="ignore",
    )

    # Rent-derived columns leave the modelling table (see TARGET_DERIVED_COLS).
    master, rent_ratios = split_target_derived(master)

    # 8. Outputs -------------------------------------------------------
    master.to_csv(outdir / "vic_property_master.csv", index=False)
    sa2_master.to_csv(outdir / "sa2_master.csv", index=False)
    sa2_yearly.to_csv(outdir / "sa2_yearly.csv", index=False)
    rent_ratios.to_csv(outdir / "vic_property_rent_ratios.csv", index=False)
    pd.DataFrame(coverage).to_csv(outdir / "source_coverage.csv", index=False)

    print(f"Wrote {len(master):,} listings to {outdir/'vic_property_master.csv'}")
    print(f"Spatial SA2 match rate: {master['sa2_code_2021'].notna().mean():.1%}")
    if "sqm_market_rent" in master.columns:
        print(f"SQM market-rent match rate: {master['sqm_market_rent'].notna().mean():.1%}")
    if "crime_suburb_rate_per_1k" in master.columns:
        print(f"Crime suburb-rate match rate: {master['crime_suburb_rate_per_1k'].notna().mean():.1%}")
    print(f"Columns: {len(master.columns)}")
    print(f"Wrote {len(sa2_yearly):,} SA2-year rows to {outdir/'sa2_yearly.csv'}")

if __name__ == "__main__":
    main()
