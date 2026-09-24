"""
Point datasets measured from each listing: schools, train stations and
OpenStreetMap amenities. Each loader returns one row per point with numeric
lat/lon columns.
"""

from __future__ import annotations

import io
import json
import time

import pandas as pd
import requests

from .common import RESEARCH_UA, get_json
from .geo import locate_in_sa2, nearest_features

# ---------------------------------------------------------------------
# SOURCES
# ---------------------------------------------------------------------

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

def with_numeric_coords(d, lat_col, lon_col):
    d[lat_col] = pd.to_numeric(d[lat_col], errors="coerce")
    d[lon_col] = pd.to_numeric(d[lon_col], errors="coerce")
    return d

# ---------------------------------------------------------------------
# SCHOOLS
# ---------------------------------------------------------------------

def load_schools():
    params = {"resource_id": SCHOOLS_RESOURCE_ID, "limit": 5000}
    data = get_json(SCHOOLS_API, params=params)
    d = pd.DataFrame(data["result"]["records"])

    lat = next((c for c in d.columns if c.lower() in {"latitude","lat","y"} or "latitude" in c.lower()), None)
    lon = next((c for c in d.columns if c.lower() in {"longitude","lon","lng","x"} or "longitude" in c.lower()), None)
    if not lat or not lon:
        print("WARNING: School coordinates not found; school access skipped.")
        return pd.DataFrame(columns=["school_lat","school_lon"])
    d = d.rename(columns={lat:"school_lat", lon:"school_lon"})
    return with_numeric_coords(d, "school_lat", "school_lon")

# ---------------------------------------------------------------------
# TRAIN STATIONS
# ---------------------------------------------------------------------

STATION_COLUMNS = ["station_id", "station_name", "station_mode", "station_lat", "station_lon"]
TRAIN_MODES = {"METRO TRAIN", "REGIONAL TRAIN"}

# Train-mode records in the stops feed are not all stations: about two thirds are
# station furniture (Park & Ride, Lift, Decision point, Taxi Zone...) and rail
# replacement bus stops. Real stations are named "X Station" or
# "X Railway Station", optionally followed by "(Locality)".
STATION_NAME_RE = r"^(.*?)\s+(?:Railway\s+)?Station(?:\s*\(.*\))?$"

def one_point_per_station(d):
    """
    Collapse the stops feed to one point per station.

    A station has a record per platform and entrance (Southern Cross has 36),
    and one served by both metro and V/Line appears under each mode. They sit
    within 250 m of each other, so each station is placed at their mean.
    """
    name = d["station_name"].astype(str).str.extract(STATION_NAME_RE, expand=False).str.strip()
    d = d.assign(station_name=name).dropna(subset=["station_name"])
    return (
        d.groupby("station_name", as_index=False)
         .agg(
             station_id=("station_id", "first"),
             # METRO sorts before REGIONAL, so a shared station counts as metro.
             station_mode=("station_mode", "min"),
             station_lat=("station_lat", "mean"),
             station_lon=("station_lon", "mean"),
         )
    )

def statewide_stop_urls():
    """
    GeoJSON stop resources in the DataVic Public Transport package.

    The URL is discovered from DataVic's CKAN API on every run, so the script
    is resilient to storage URL changes.
    """
    pkg = get_json(
        DATAVIC_PACKAGE_SHOW,
        params={"id": PUBLIC_TRANSPORT_DATASET_ID},
        timeout=60,
    )
    urls = []
    for r in pkg.get("result", {}).get("resources", []):
        fmt = str(r.get("format", "")).lower()
        mime = str(r.get("mimetype", "")).lower()
        name = str(r.get("name", "")).lower()
        url = r.get("url")
        if url and "stop" in name and ("geojson" in fmt or "geo+json" in mime):
            urls.append(url)
    return urls

def read_statewide_stations(url):
    """Train stations in one statewide stops GeoJSON, one point per station."""
    r = requests.get(
        url,
        headers={
            "User-Agent": RESEARCH_UA,
            "Accept": "application/geo+json, application/json",
        },
        timeout=180,
    )
    r.raise_for_status()

    rows = []
    for feat in r.json().get("features", []):
        props = feat.get("properties", {}) or {}
        geom = feat.get("geometry", {}) or {}
        coords = geom.get("coordinates")

        mode = str(props.get("MODE", props.get("mode", ""))).upper().strip()
        if mode not in TRAIN_MODES:
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
    if d.empty:
        return d
    return one_point_per_station(d.dropna(subset=["station_lat", "station_lon"]))

def read_fallback_stations(url):
    """The older Metro Train Stations with Accessibility Information export."""
    r = requests.get(url, params={"delimiter": ","}, timeout=60)
    r.raise_for_status()
    d = pd.read_csv(io.StringIO(r.text))
    lat = next((c for c in d.columns if "lat" in c.lower()), None)
    lon = next((c for c in d.columns if "lon" in c.lower()), None)
    if not (lat and lon):
        return pd.DataFrame()
    d = d.rename(columns={lat:"station_lat", lon:"station_lon"})
    return with_numeric_coords(d, "station_lat", "station_lon")

def load_stations():
    """
    Preferred source: Department of Transport and Planning statewide
    Public Transport Stops GeoJSON, keeping METRO TRAIN and REGIONAL TRAIN
    stop points. This is more complete and more stable than the older City of
    Melbourne station-export endpoint, which is only the fallback.
    """
    try:
        for url in statewide_stop_urls():
            try:
                d = read_statewide_stations(url)
            except Exception as e:
                print(f"WARNING: statewide station resource failed: {e}")
                continue
            if not d.empty:
                print(f"Loaded {len(d):,} train stations from statewide Public Transport Stops.")
                return d
    except Exception as e:
        print(f"WARNING: DataVic station-resource discovery failed: {e}")

    for url in STATIONS_URLS:
        try:
            d = read_fallback_stations(url)
        except Exception:
            continue
        if not d.empty:
            print("Loaded fallback Metro station accessibility dataset.")
            return d

    print("WARNING: Train station datasets unavailable; station access skipped.")
    return pd.DataFrame(columns=STATION_COLUMNS)

# ---------------------------------------------------------------------
# OPENSTREETMAP AMENITIES
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
        "User-Agent": RESEARCH_UA,
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

def add_osm_access(listings, sa2_master, sa2, osm):
    """nearest_<category>_km per listing and osm_<category>_count per SA2."""
    located = locate_in_sa2(osm, sa2, "amenity_lat", "amenity_lon")
    counts = (
        located.dropna(subset=["sa2_code_2021"])
               .groupby(["sa2_code_2021","amenity_type"])
               .size().unstack(fill_value=0)
               .add_prefix("osm_").add_suffix("_count")
               .reset_index()
    )
    sa2_master = sa2_master.merge(counts, on="sa2_code_2021", how="left")
    # An SA2 with none of a category has zero of it, the same as school_count.
    count_cols = [c for c in counts.columns if c != "sa2_code_2021"]
    sa2_master[count_cols] = sa2_master[count_cols].fillna(0)

    for cat in sorted(osm["amenity_type"].unique()):
        sub = osm[osm["amenity_type"] == cat]
        listings = nearest_features(listings, sub, "amenity_lat", "amenity_lon", cat)
    return listings, sa2_master
