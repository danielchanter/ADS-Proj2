
"""
Build a single property-level Victorian rental master table.

Input:
    vic_rentals_all.csv

Output:
    output/vic_property_master.csv
    output/sa2_master.csv
    output/source_coverage.csv

Core enrichments:
  * ABS ASGS 2021 SA2 geography
  * ABS 2021 General Community Profile (selected useful fields)
  * ABS SEIFA 2021 (all four indexes)
  * ABS Regional Population 2025
  * ABS Personal Income 2022-23 (SA2, if sheet layout is recognised)
  * Victoria in Future 2023 SA2 projections to 2036
  * Victorian School Locations 2025
  * Metro train stations accessibility dataset
  * Optional OpenStreetMap amenity counts/distances using Overpass

Install:
    pip install pandas geopandas shapely requests pyogrio openpyxl scipy

Usage:
    python build_vic_property_master.py --input vic_rentals_all.csv --output-dir output

Notes:
  * No OpenRouteService API key is required because no destination/commute target
    was specified. Add route-time features only once a meaningful destination
    (e.g. Melbourne CBD, nearest university, workplace) is defined.
  * OSM enrichment is optional because Overpass can be slow/rate-limited.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import re
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import requests
from scipy.cluster.hierarchy import fcluster, linkage
from scipy.spatial import cKDTree
from shapely.geometry import Point

# A core SA2 source joining fewer than this share of Victorian SA2s is
# reported as FAILED in source_coverage.csv (a few SA2s legitimately have no
# population, e.g. "No usual address", so 100% is not expected).
MIN_SA2_MATCH_RATE = 0.90

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

PERSONAL_INCOME_URL = (
    "https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/"
    "personal-income-australia/2022-23/"
    "Table%201%20-%20Total%20income%2C%20earners%20and%20summary%20statistics%20"
    "by%20geography%2C%202018-19%20to%202022-23.xlsx"
)

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
    "https://overpass.nchc.org.tw/api/interpreter",
]

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

def normalize_code(series):
    return series.astype(str).str.replace(r"\.0$", "", regex=True).str.strip()

def pick_col(df, candidates):
    lut = {c.lower(): c for c in df.columns}
    for c in candidates:
        if c.lower() in lut:
            return lut[c.lower()]
    return None

def numeric(series):
    return pd.to_numeric(series, errors="coerce")

def haversine_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1 = np.radians(lat1)
    p2 = np.radians(lat2)
    dlat = np.radians(lat2 - lat1)
    dlon = np.radians(lon2 - lon1)
    a = np.sin(dlat/2)**2 + np.cos(p1)*np.cos(p2)*np.sin(dlon/2)**2
    return 2*r*np.arcsin(np.sqrt(a))

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
        out_fields=(
            "sa2_code_2021,sa2_name_2021,sa3_code_2021,sa3_name_2021,"
            "sa4_code_2021,sa4_name_2021,gccsa_code_2021,gccsa_name_2021,"
            "state_code_2021,state_name_2021,area_albers_sqkm"
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

    keep = [
        "sa2_code_2021","Tot_P_P","Median_age_persons",
        "Median_tot_prsnl_inc_weekly","Median_tot_hhd_inc_weekly",
        "Median_rent_weekly","Median_mortgage_repay_monthly",
        "Average_household_size","age_0_14_pct","age_25_44_pct",
        "age_65_plus_pct","born_overseas_pct","non_english_home_pct"
    ]
    return d[keep].rename(columns={
        "Tot_P_P":"census_population_2021",
        "Median_age_persons":"census_median_age",
        "Median_tot_prsnl_inc_weekly":"census_median_personal_income_weekly",
        "Median_tot_hhd_inc_weekly":"census_median_household_income_weekly",
        "Median_rent_weekly":"census_median_rent_weekly",
        "Median_mortgage_repay_monthly":"census_median_mortgage_monthly",
        "Average_household_size":"census_avg_household_size",
    })

def load_seifa():
    fields = (
        "sa2_code_2021,sa2_name_2021,"
        "irsad_score,irsad_aus_decile,irsad_aus_percentile,"
        "irsd_score,irsd_aus_decile,irsd_aus_percentile,"
        "ier_score,ier_aus_decile,ier_aus_percentile,"
        "ieo_score,ieo_aus_decile,ieo_aus_percentile"
    )
    d = arcgis_geojson(SEIFA_URL, out_fields=fields, return_geometry=False)
    d["sa2_code_2021"] = normalize_code(d["sa2_code_2021"])
    d = d[d["sa2_code_2021"].str.startswith("2")].copy()
    return d.drop(columns=["sa2_name_2021"], errors="ignore")

def load_regional_population():
    fields = (
        "sa2_code_2021,sa2_name_2021,erp_2024,erp_2025,"
        "erp_change_number_2024_25,erp_change_per_cent_2024_25,"
        "area_km2,pop_density_2025_people_per_km2,"
        "births_2024_25,deaths_2024_25,natural_increase_2024_25,"
        "net_internal_migration_2024_25,net_overseas_migration_2024_25"
    )
    d = arcgis_geojson(
        REGIONAL_POP_URL,
        where="state_code_2021='2'",
        out_fields=fields,
        return_geometry=False,
    )
    d["sa2_code_2021"] = normalize_code(d["sa2_code_2021"])
    return d.drop(columns=["sa2_name_2021"], errors="ignore").rename(columns={
        "erp_change_per_cent_2024_25":"population_growth_pct_2024_25",
        "pop_density_2025_people_per_km2":"population_density_2025",
    })

def load_vif(cache_dir: Path):
    path = cache_dir / "vif_sa2.xlsx"
    if not path.exists():
        r = requests.get(VIF_URL, timeout=180)
        r.raise_for_status()
        path.write_bytes(r.content)

    # Only the Total_Population sheet is used. Reading every sheet pulled in the
    # Contents/Explanatory Notes sheets, whose title column ("...to_2036_(Release_2)")
    # was substring-matched as the 2036 column, leaving 2036 and growth all-NaN.
    sheet = "Total_Population"
    if sheet not in pd.ExcelFile(path).sheet_names:
        print(f"WARNING: VIF sheet {sheet!r} not found; VIF columns skipped.")
        return pd.DataFrame(columns=["sa2_code_2021"])

    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    header_i = next(
        (i for i in range(min(len(raw), 30))
         if (pd.to_numeric(raw.iloc[i], errors="coerce") == 2036).any()),
        None,
    )
    if header_i is None:
        print("WARNING: VIF workbook layout not recognised; VIF columns skipped.")
        return pd.DataFrame(columns=["sa2_code_2021"])

    d = pd.read_excel(path, sheet_name=sheet, header=header_i)
    d.columns = [str(c).strip() for c in d.columns]
    code = next((c for c in d.columns if "sa2" in c.lower() and "code" in c.lower()), None)
    if code is None:
        print("WARNING: VIF SA2 code column not found.")
        return pd.DataFrame(columns=["sa2_code_2021"])

    d["sa2_code_2021"] = normalize_code(d[code])
    d = d[d["sa2_code_2021"].str.fullmatch(r"2\d{8}")].copy()

    out = d[["sa2_code_2021"]].drop_duplicates().copy()
    for year in ("2021", "2026", "2031", "2036"):
        if year in d.columns:
            out[f"vif_population_{year}"] = pd.to_numeric(d[year], errors="coerce").to_numpy()

    def growth(a, b):
        # Zero-population SA2s (e.g. national parks) give NaN, not inf.
        return 100 * (out[f"vif_population_{b}"] / out[f"vif_population_{a}"].replace(0, np.nan) - 1)

    if {"vif_population_2021", "vif_population_2026"} <= set(out.columns):
        out["vif_population_growth_pct_2021_26"] = growth("2021", "2026")
    if {"vif_population_2026", "vif_population_2036"} <= set(out.columns):
        out["vif_population_growth_pct_2026_36"] = growth("2026", "2036")
    return out

def load_personal_income(cache_dir: Path):
    """
    Best-effort extraction of SA2 total personal income summary statistics.
    ABS workbook layouts can change; failure here does not stop the pipeline.
    """
    path = cache_dir / "personal_income.xlsx"
    if not path.exists():
        r = requests.get(PERSONAL_INCOME_URL, timeout=180)
        r.raise_for_status()
        path.write_bytes(r.content)

    # The SA2 sheet (Table 1.4) has a two-row header: measure names ("Median ($)")
    # sit one row above the row holding "SA2", "SA2 NAME" and the years, so the
    # two rows are combined. All five years (2018-19 to 2022-23) are kept so the
    # income series can be used over time, not just as a single cross-section.
    measures = {
        "Earners (persons)": "tax_income_earners",
        "Median ($)": "tax_median_total_personal_income",
        "Mean ($)": "tax_mean_total_personal_income",
    }
    xls = pd.ExcelFile(path)
    for s in xls.sheet_names:
        raw = pd.read_excel(path, sheet_name=s, header=None)
        hits = raw.index[raw[0].astype(str).str.strip().eq("SA2")]
        if len(hits) == 0 or hits[0] == 0:
            continue
        hdr = hits[0]

        measure = raw.iloc[hdr - 1].ffill()
        year = raw.iloc[hdr].astype(str).str.strip()
        body = raw.iloc[hdr + 1:]

        out = pd.DataFrame({"sa2_code_2021": normalize_code(body[0])})
        for c in range(2, raw.shape[1]):
            name = measures.get(str(measure[c]).strip())
            if name and re.fullmatch(r"\d{4}-\d{2}", year[c]):
                out[f"{name}_{year[c].replace('-', '_')}"] = pd.to_numeric(body[c], errors="coerce")

        out = out[out["sa2_code_2021"].str.fullmatch(r"2\d{8}")]
        if len(out.columns) > 1 and len(out):
            return out.drop_duplicates("sa2_code_2021")
    print("WARNING: Personal Income SA2 sheet layout not recognised; skipped.")
    return pd.DataFrame(columns=["sa2_code_2021"])

def load_schools():
    params = {"resource_id": SCHOOLS_RESOURCE_ID, "limit": 5000}
    data = get_json(SCHOOLS_API, params=params)
    d = pd.DataFrame(data["result"]["records"])

    lat = next((c for c in d.columns if c.lower() in {"latitude","lat","y"} or "latitude" in c.lower()), None)
    lon = next((c for c in d.columns if c.lower() in {"longitude","lon","lng","x"} or "longitude" in c.lower()), None)
    if not lat or not lon:
        print("WARNING: School coordinates not found; school access skipped.")
        return pd.DataFrame(columns=["school_lat","school_lon"])
    # School_Status: O = open, C = closed.
    if "School_Status" in d.columns:
        d = d[d["School_Status"].astype(str).str.upper().eq("O")]
    return d.rename(columns={lat:"school_lat", lon:"school_lon"})

NON_STATION_WORDS = r"\b(?:bus|tram|coach|replacement|car park|park & ride|taxi|bike)\b"

def collapse_to_stations(stops):
    """
    The METRO/REGIONAL TRAIN stop points include station-precinct facilities
    (Park & Ride, Bike & Ride, Taxi Zone, "Decision point", lifts), rail
    replacement bus stops, street names such as "Station St", and several
    platform-level points per station. Keep points named "... Station" and
    reduce them to one point per station (mean location), so that per-SA2
    counts are numbers of stations rather than numbers of stop points.
    """
    name = stops["station_name"].fillna("").str.strip()
    is_station = name.str.contains(r"\bStation$", case=False) & ~name.str.contains(
        NON_STATION_WORDS, case=False
    )
    s = stops[is_station].copy()
    s["station_key"] = (
        s["station_name"].str.strip()
        .str.replace(r"\s+(?:Railway\s+)?Station$", "", case=False, regex=True)
        .str.lower()
    )
    # Same-named stations in different towns stay separate: within each name,
    # points closer than ~5 km (0.05 deg) chain into one station.
    s["cell"] = 0
    for key, idx in s.groupby("station_key").groups.items():
        if len(idx) > 1:
            pts = s.loc[idx, ["station_lat", "station_lon"]].to_numpy()
            s.loc[idx, "cell"] = fcluster(linkage(pts, "single"), 0.05, "distance")
    return (
        s.groupby(["station_key", "cell"], as_index=False)
        .agg(
            station_name=("station_name", "first"),
            station_mode=("station_mode", "first"),
            station_lat=("station_lat", "mean"),
            station_lon=("station_lon", "mean"),
        )
        .drop(columns=["cell"])
    )

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
                    n_stops = len(d)
                    d = collapse_to_stations(d)
                    print(
                        f"Loaded {len(d):,} train stations from "
                        f"{n_stops:,} train-mode stop points in statewide Public Transport Stops."
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
    "gp_clinic": [('amenity','clinic'),('healthcare','doctor')],
    "library": [('amenity','library')],
    "park": [('leisure','park')],
    "shopping_centre": [('shop','mall')],
}

def overpass_bbox_query(south, west, north, east):
    clauses = []
    for label, tags in OSM_AMENITIES.items():
        for key, val in tags:
            clauses.append(f'nwr["{key}"="{val}"]({south},{west},{north},{east});')

    q = "[out:json][timeout:120];(" + "".join(clauses) + ");out center tags;"

    headers = {
        "User-Agent": "ADS-Proj2-Victorian-Rental-Research/1.0",
        "Accept": "application/json",
    }

    errors = []

    for url in OVERPASS_URLS:
        # First try POST, which is standard for larger Overpass queries.
        try:
            r = requests.post(
                url,
                data={"data": q},
                headers=headers,
                timeout=180
            )
            if r.ok:
                return r.json().get("elements", [])
            errors.append(f"{url} POST -> HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"{url} POST -> {type(e).__name__}: {e}")

        # Some public mirrors are happier with GET.
        try:
            r = requests.get(
                url,
                params={"data": q},
                headers=headers,
                timeout=180
            )
            if r.ok:
                return r.json().get("elements", [])
            errors.append(f"{url} GET -> HTTP {r.status_code}")
        except Exception as e:
            errors.append(f"{url} GET -> {type(e).__name__}: {e}")

    print("WARNING: OpenStreetMap Overpass enrichment unavailable; skipped.")
    for err in errors:
        print("  ", err)
    return []

def load_osm_amenities(sa2, cache_dir):
    cache = cache_dir / "osm_victoria_amenities.json"
    if cache.exists():
        try:
            elems = json.loads(cache.read_text())
        except Exception:
            elems = []
    else:
        # Use Victoria SA2 extent. Overpass is an optional enrichment:
        # network/API failure must never stop the master-table build.
        minx, miny, maxx, maxy = sa2.total_bounds
        elems = overpass_bbox_query(miny, minx, maxy, maxx)
        if elems:
            cache.write_text(json.dumps(elems))

    if not elems:
        return pd.DataFrame(
            columns=["amenity_type", "amenity_lat", "amenity_lon"]
        )

    rows = []
    for e in elems:
        lat = e.get("lat", e.get("center", {}).get("lat"))
        lon = e.get("lon", e.get("center", {}).get("lon"))
        if lat is None or lon is None:
            continue
        tags = e.get("tags", {})
        cat = None
        for label, pairs in OSM_AMENITIES.items():
            if any(tags.get(k) == v for k,v in pairs):
                cat = label
                break
        if cat:
            rows.append({"amenity_type":cat,"amenity_lat":lat,"amenity_lon":lon})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------
# ADDITIONAL SOURCE REGISTRY / BEST-EFFORT ENRICHMENT
# ---------------------------------------------------------------------

# These sources are recorded explicitly even when their public delivery format
# changes. source_coverage.csv records whether each enrichment succeeded.
EXTRA_SOURCE_PAGES = {
    "DFFH Rental Report": "https://www.dffh.vic.gov.au/publications/rental-report",
    "Victorian Property Sales Report": "https://discover.data.vic.gov.au/dataset/victorian-property-sales-report-median-house-by-suburb-time-series",
    "Crime Statistics Victoria": "https://www.crimestatistics.vic.gov.au/crime-statistics/latest-victorian-crime-data/download-data",
    "School Zones": "https://discover.data.vic.gov.au/dataset/?q=school+zones",
    "PTV Timetable API": "https://discover.data.vic.gov.au/dataset/ptv-timetable-api",
    "Vicmap Features of Interest": "https://discover.data.vic.gov.au/dataset/vicmap-features-of-interest-rest-api",
    "Digital Atlas of Australia": "https://digital.atlas.gov.au/",
    "ABS Building Approvals": "https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia/latest-release",
    "ABS CPI": "https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia/latest-release",
    "ASGS 2026": "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/latest-release",
}

DATAVIC_CKAN = "https://discover.data.vic.gov.au/api/3/action/package_search"

def datavic_find_resources(query):
    """Discover current DataVic machine-readable resources instead of hard-coding fragile URLs."""
    try:
        data = get_json(DATAVIC_CKAN, params={"q": query, "rows": 20}, timeout=60)
        results = data.get("result", {}).get("results", [])
        resources = []
        for pkg in results:
            for r in pkg.get("resources", []):
                url = r.get("url")
                fmt = str(r.get("format", "")).lower()
                if url:
                    resources.append({
                        "package": pkg.get("title", ""),
                        "name": r.get("name", ""),
                        "format": fmt,
                        "url": url,
                    })
        return resources
    except Exception as e:
        print(f"WARNING: DataVic discovery failed for {query}: {e}")
        return []

def record_extra_source_status(coverage, name, status, detail=""):
    coverage.append({
        "source": name,
        "victorian_sa2_matches": np.nan,
        "victorian_sa2_total": np.nan,
        "status": status,
        "detail": detail,
    })

def add_market_benchmark_features(master):
    """
    Create leakage-safe benchmark variables from already joined official area data.
    DFFH/property-sales files are discovered below and retained as source metadata;
    they are not silently joined when only suburb-level time series are available,
    because a fuzzy suburb-to-SA2 join can misassign observations.
    """
    if "census_median_rent_weekly" in master.columns:
        master["rent_vs_2021_census_median"] = (
            master["weekly_rent"] /
            pd.to_numeric(master["census_median_rent_weekly"], errors="coerce")
        )
    return master

def discover_extra_sources(coverage, cache_dir):
    """
    Discover the remaining requested public sources and save a manifest.
    This keeps the pipeline reproducible even when DataVic changes resource URLs.
    """
    queries = {
        "Victorian Property Sales Report": "Victorian Property Sales Report median house suburb time series",
        "School Zones": "school zones",
        "PTV Timetable": "PTV timetable API",
        "Vicmap Features of Interest": "Vicmap Features of Interest REST API",
    }
    manifest = []
    for name, q in queries.items():
        resources = datavic_find_resources(q)
        if resources:
            record_extra_source_status(coverage, name, "discovered",
                                       f"{len(resources)} current DataVic resources found")
            for r in resources:
                manifest.append({"source": name, **r})
        else:
            record_extra_source_status(coverage, name, "not_discovered",
                                       "No current machine-readable DataVic resource found")

    # Sources that are intentionally represented as contextual/forecast sources.
    record_extra_source_status(
        coverage, "DFFH Rental Report", "contextual",
        "Official signed-rent benchmark; avoid using same-period suburb median as a direct predictor of listing rent."
    )
    record_extra_source_status(
        coverage, "Crime Statistics Victoria", "contextual",
        "Available at suburb/postcode/LGA; requires a chosen offence period and denominator before a defensible SA2 feature can be built."
    )
    record_extra_source_status(
        coverage, "ABS CPI Melbourne rents", "contextual",
        "Melbourne-wide quarterly time-series covariate; useful for temporal forecasting, not cross-sectional SA2 differentiation."
    )
    record_extra_source_status(
        coverage, "ASGS 2026 correspondence", "contextual",
        "Needed when producing future outputs on 2026 geography; current Census/SEIFA joins remain on ASGS 2021."
    )
    record_extra_source_status(
        coverage, "Digital Atlas of Australia", "discovered",
        "Source registered for future attribute selection; do not ingest arbitrary attributes without defining the modelling variable."
    )
    record_extra_source_status(
        coverage, "ABS Building Approvals", "available",
        "ABS publishes SA2 small-area approvals; registered as a forecast/supply covariate."
    )

    pd.DataFrame(manifest).to_csv(cache_dir.parent / "extra_source_manifest.csv", index=False)

# ---------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    default_input = Path(__file__).resolve().parent.parent / "data" / "raw" / "vic_rentals_all.csv"
    ap.add_argument("--input", default=str(default_input))
    default_output = Path(__file__).resolve().parent.parent / "data" / "processed"
    ap.add_argument("--output-dir", default=str(default_output))
    ap.add_argument("--no-osm", action="store_true",
                    help="Skip OpenStreetMap amenity enrichment (OSM is included by default).")
    args = ap.parse_args()

    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    cache = outdir / "_cache"
    cache.mkdir(exist_ok=True)

    # 1. Property table ------------------------------------------------
    p = pd.read_csv(args.input)

    # Drop scraper/admin/low-information fields.
    keep = [
        "listing_id","suburb","postcode","weekly_rent","bond","available_date",
        "date_listed","days_listed","bedrooms","bathrooms","carspaces",
        "property_type","address","lat","lon","primary_type","secondary_type",
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

    # Useful engineered listing variables.
    p["rent_per_bedroom"] = p["weekly_rent"] / p["bedrooms"].replace(0, np.nan)
    p["has_carspace"] = (p["carspaces"].fillna(0) > 0).astype("int8")
    p["has_structured_features"] = p["structured_features"].notna().astype("int8")

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
        "sa3_code_2021","sa3_name_2021",
        "sa4_code_2021","sa4_name_2021",
        "gccsa_code_2021","gccsa_name_2021"
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
    source_frames = [
        ("ABS Census 2021 GCP", load_gcp()),
        ("ABS SEIFA 2021", load_seifa()),
        ("ABS Regional Population 2025", load_regional_population()),
        ("ABS Personal Income 2022-23", load_personal_income(cache)),
        ("Victoria in Future 2023", load_vif(cache)),
    ]

    sa2_master = sa2.drop(columns=["geometry", "shape"], errors="ignore").copy()
    coverage = []
    for name, d in source_frames:
        if "sa2_code_2021" not in d.columns:
            continue
        before = len(sa2_master)
        matched = int(sa2_master["sa2_code_2021"].isin(d["sa2_code_2021"]).sum())
        # A source that joins almost nothing must not be reported as joined.
        ok = matched >= MIN_SA2_MATCH_RATE * before
        coverage.append({
            "source": name,
            "victorian_sa2_matches": matched,
            "victorian_sa2_total": int(before),
            "status": "joined" if ok else "FAILED",
            "detail": f"{matched}/{before} SA2s matched ({matched / before:.1%})"
        })
        if not ok:
            print(f"WARNING: {name} matched only {matched}/{before} SA2s; its columns will be empty.")
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
    record_extra_source_status(
        coverage, "Victorian school locations",
        "joined" if not schools.empty else "FAILED",
        f"{len(schools):,} open schools; nearest_school_km per listing, school_count per SA2",
    )

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
    record_extra_source_status(
        coverage, "Victorian train stations",
        "joined" if not stations.empty else "FAILED",
        f"{len(stations):,} stations; nearest_train_station_km per listing, train_station_count per SA2",
    )

    # 6. Optional OSM --------------------------------------------------
    if args.no_osm:
        record_extra_source_status(coverage, "OpenStreetMap amenities", "skipped", "--no-osm")
    else:
        osm = load_osm_amenities(sa2, cache)
        record_extra_source_status(
            coverage, "OpenStreetMap amenities",
            "joined" if not osm.empty else "FAILED",
            f"{len(osm):,} amenity points" if not osm.empty else "Overpass unavailable; OSM columns absent",
        )
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
            # SA2s with no amenity of any kind are absent from `counts`: that is 0, not missing.
            osm_count_cols = [c for c in counts.columns if c != "sa2_code_2021"]
            sa2_master[osm_count_cols] = sa2_master[osm_count_cols].fillna(0)

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

    # Register/discover the remaining requested public sources.
    discover_extra_sources(coverage, cache)
    master = add_market_benchmark_features(master)

    # Simple affordability signal (listing rent vs Census median household income).
    if "census_median_household_income_weekly" in master:
        master["rent_to_area_median_hh_income_pct"] = (
            100 * master["weekly_rent"] /
            pd.to_numeric(master["census_median_household_income_weekly"], errors="coerce")
        )

    # Avoid duplicate area-name columns if any.
    master = master.loc[:, ~master.columns.duplicated()].copy()

    # Final cleanup: remove obsolete/redundant columns.
    drop_cols = [
        "sa2_name_2021_area",
        "sa3_code_2021_area",
        "sa3_name_2021_area",
        "sa4_code_2021_area",
        "sa4_name_2021_area",
        "gccsa_code_2021_area",
        "gccsa_name_2021_area",
        "area_albers_sqkm_area",
        "state_code_2021",
        "state_name_2021",
        "shape",
        "area_km2",
        "has_carspace",
        "has_structured_features",
    ]
    master = master.drop(columns=[c for c in drop_cols if c in master.columns], errors="ignore")

    # A column that is entirely empty means a loader silently matched nothing.
    for label, frame in [("sa2_master", sa2_master), ("vic_property_master", master)]:
        empty = [c for c in frame.columns if frame[c].isna().all()]
        if empty:
            print(f"WARNING: all-empty columns in {label}: {', '.join(empty)}")
            record_extra_source_status(coverage, f"{label} column check", "FAILED",
                                       f"all-empty columns: {', '.join(empty)}")

    # 8. Outputs -------------------------------------------------------
    master.to_csv(outdir / "vic_property_master.csv", index=False)
    sa2_master.to_csv(outdir / "sa2_master.csv", index=False)
    pd.DataFrame(coverage).to_csv(outdir / "source_coverage.csv", index=False)

    print(f"Wrote {len(master):,} listings to {outdir/'vic_property_master.csv'}")
    print(f"Spatial SA2 match rate: {master['sa2_code_2021'].notna().mean():.1%}")
    print(f"Columns: {len(master.columns)}")

if __name__ == "__main__":
    main()
