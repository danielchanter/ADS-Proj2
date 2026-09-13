
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
from scipy.spatial import cKDTree
from shapely.geometry import Point

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

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

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

    xls = pd.ExcelFile(path)
    frames = []
    for s in xls.sheet_names:
        raw = pd.read_excel(path, sheet_name=s, header=None)
        # Find a row containing SA2/code and projection years.
        header_i = None
        for i in range(min(len(raw), 30)):
            # Convert every cell safely to text before searching.
            vals = [str(x).lower() if pd.notna(x) else "" for x in raw.iloc[i].tolist()]
            if any("sa2" in x for x in vals) and any("2036" in x for x in vals):
                header_i = i
                break
        if header_i is None:
            continue
        d = pd.read_excel(path, sheet_name=s, header=header_i)
        frames.append(d)

    if not frames:
        print("WARNING: VIF workbook layout not recognised; VIF columns skipped.")
        return pd.DataFrame(columns=["sa2_code_2021"])

    d = pd.concat(frames, ignore_index=True, sort=False)
    code = next((c for c in d.columns if "sa2" in str(c).lower() and "code" in str(c).lower()), None)
    if code is None:
        print("WARNING: VIF SA2 code column not found.")
        return pd.DataFrame(columns=["sa2_code_2021"])

    d["sa2_code_2021"] = normalize_code(d[code])
    d = d[d["sa2_code_2021"].str.startswith("2")].copy()

    def year_col(year):
        for c in d.columns:
            if str(year) == str(c).strip() or str(year) in str(c):
                return c
        return None

    c2026, c2031, c2036 = year_col(2026), year_col(2031), year_col(2036)
    out = d[["sa2_code_2021"]].drop_duplicates().copy()

    # Prefer total population/dwelling/household fields if repeated years exist.
    # We select numeric year-labelled columns only if there is one obvious set.
    for year, col in [(2026,c2026),(2031,c2031),(2036,c2036)]:
        if col:
            out[f"vif_population_{year}"] = pd.to_numeric(d[col], errors="coerce").groupby(d["sa2_code_2021"]).transform("max")
    out = out.groupby("sa2_code_2021", as_index=False).max(numeric_only=True)

    if "vif_population_2026" in out and "vif_population_2036" in out:
        out["vif_population_growth_pct_2026_36"] = 100 * (
            out["vif_population_2036"] / out["vif_population_2026"] - 1
        )
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

    xls = pd.ExcelFile(path)
    for s in xls.sheet_names:
        raw = pd.read_excel(path, sheet_name=s, header=None)
        text = " ".join(raw.head(15).astype(str).fillna("").values.ravel()).lower()
        if "sa2" not in text and "statistical area level 2" not in text:
            continue

        # Locate a row that appears to be the header.
        for i in range(min(len(raw), 25)):
            vals = [str(x).lower() for x in raw.iloc[i].tolist()]
            if any("sa2" in x for x in vals) and any("median" in x for x in vals):
                d = pd.read_excel(path, sheet_name=s, header=i)
                code = next((c for c in d.columns if "sa2" in str(c).lower() and "code" in str(c).lower()), None)
                if code is None:
                    continue
                d["sa2_code_2021"] = normalize_code(d[code])
                d = d[d["sa2_code_2021"].str.startswith("2")].copy()

                med = next((c for c in d.columns if "median" in str(c).lower() and "income" in str(c).lower()), None)
                mean = next((c for c in d.columns if "mean" in str(c).lower() and "income" in str(c).lower()), None)
                earners = next((c for c in d.columns if "earner" in str(c).lower()), None)
                cols = ["sa2_code_2021"]
                rename = {}
                for c, new in [
                    (med, "tax_median_total_personal_income_2022_23"),
                    (mean, "tax_mean_total_personal_income_2022_23"),
                    (earners, "tax_income_earners_2022_23"),
                ]:
                    if c:
                        cols.append(c); rename[c] = new
                return d[cols].rename(columns=rename).drop_duplicates("sa2_code_2021")
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
    return d.rename(columns={lat:"school_lat", lon:"school_lon"})

def load_stations():
    for url in STATIONS_URLS:
        try:
            r = requests.get(url, params={"delimiter": ","}, timeout=60)
            r.raise_for_status()
            d = pd.read_csv(io.StringIO(r.text), sep=";|" if False else ",")
            lat = next((c for c in d.columns if "lat" in c.lower()), None)
            lon = next((c for c in d.columns if "lon" in c.lower()), None)
            if lat and lon:
                return d.rename(columns={lat:"station_lat", lon:"station_lon"})
        except Exception:
            continue
    print("WARNING: Metro station export endpoint unavailable; station access skipped.")
    return pd.DataFrame(columns=["station_lat","station_lon"])

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
    r = requests.post(OVERPASS_URL, data={"data": q}, timeout=180)
    r.raise_for_status()
    return r.json()["elements"]

def load_osm_amenities(sa2, cache_dir):
    cache = cache_dir / "osm_victoria_amenities.json"
    if cache.exists():
        elems = json.loads(cache.read_text())
    else:
        # Use Victoria SA2 extent, slightly padded.
        minx, miny, maxx, maxy = sa2.total_bounds
        elems = overpass_bbox_query(miny, minx, maxy, maxx)
        cache.write_text(json.dumps(elems))

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
# MAIN
# ---------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    default_input = Path(__file__).resolve().parent.parent / "data" / "raw" / "vic_rentals_all.csv"
    ap.add_argument("--input", default=str(default_input))
    default_output = Path(__file__).resolve().parent.parent / "data" / "processed"
    ap.add_argument("--output-dir", default=str(default_output))
    ap.add_argument("--with-osm", action="store_true",
                    help="Add OSM amenity counts and nearest-distance features.")
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

    sa2_master = sa2.drop(columns="geometry").copy()
    coverage = []
    for name, d in source_frames:
        if "sa2_code_2021" not in d.columns:
            continue
        before = len(sa2_master)
        matched = sa2_master["sa2_code_2021"].isin(d["sa2_code_2021"]).sum()
        coverage.append({"source":name, "victorian_sa2_matches":int(matched), "victorian_sa2_total":int(before)})
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
        joined = nearest_features(joined, stations, "station_lat","station_lon","train_station")

    # 6. Optional OSM --------------------------------------------------
    if args.with_osm:
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
        "vif_population_2036",
        "vif_population_growth_pct_2026_36",
        "area_km2",
        "has_carspace",
        "has_structured_features",
    ]
    master = master.drop(columns=[c for c in drop_cols if c in master.columns], errors="ignore")

    # 8. Outputs -------------------------------------------------------
    master.to_csv(outdir / "vic_property_master.csv", index=False)
    sa2_master.to_csv(outdir / "sa2_master.csv", index=False)
    pd.DataFrame(coverage).to_csv(outdir / "source_coverage.csv", index=False)

    print(f"Wrote {len(master):,} listings to {outdir/'vic_property_master.csv'}")
    print(f"Spatial SA2 match rate: {master['sa2_code_2021'].notna().mean():.1%}")
    print(f"Columns: {len(master.columns)}")

if __name__ == "__main__":
    main()
