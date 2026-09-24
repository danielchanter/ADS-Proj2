"""Land size of each listing's lot, from Vicmap Property polygons."""

from __future__ import annotations

import json
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

from .common import RESEARCH_UA, record_source

SOURCE_NAME = "Vicmap Property land size"

# Property Map Polygons (PROPERTY_MP): one polygon per rateable property in
# Victoria. A property can span several parcels (a house on two lots), so this
# is the land that is let with the dwelling, not a single title parcel.
VICMAP_PROPERTY_URL = (
    "https://services-ap1.arcgis.com/P744lA0wf4LlBZ84/ArcGIS/rest/services/"
    "Vicmap_Property/FeatureServer/0/query"
)

# Listings sent per request, as one multipoint. A strata tower returns a polygon
# per unit, so large batches overrun the 2,000-feature page for little gain.
BATCH_POINTS = 25
MIN_INTERVAL_S = 0.2

# Areas are measured in VicGrid GDA2020, an equal-area-enough projection for the
# whole state. The service's Shape__Area is in Web Mercator, about 1.6x too big
# at Melbourne's latitude.
AREA_CRS = "EPSG:7899"

# Strata units are stacked in Vicmap as identical polygons, one per unit, each
# the size of the whole building's lot. Two or more containing polygons within
# this fraction of the smallest one mean the lot is shared.
SAME_LOT_TOLERANCE = 0.01

LAND_COLS = ["land_m2", "land_lot_shared"]

# Listing coordinates are rounded to 5 dp (~1 m), so listings in the same
# building share one lookup and one cache entry.
COORD_DP = 5

def coord_key(lat, lon):
    return f"{lat:.{COORD_DP}f},{lon:.{COORD_DP}f}"

def query_polygons(points):
    """
    Every property polygon containing any of the (lon, lat) points, following
    the service's paging. Returns a GeoDataFrame in EPSG:4326.
    """
    data = {
        "geometry": json.dumps({"points": points, "spatialReference": {"wkid": 4326}}),
        "geometryType": "esriGeometryMultipoint",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
        "outFields": "prop_pfi",
        "orderByFields": "OBJECTID",
        "outSR": 4326,
        "f": "geojson",
    }
    features = []
    offset = 0
    while True:
        r = requests.post(
            VICMAP_PROPERTY_URL,
            data={**data, "resultOffset": offset},
            headers={"User-Agent": RESEARCH_UA},
            timeout=180,
        )
        r.raise_for_status()
        body = r.json()
        if "error" in body:
            raise RuntimeError(body["error"])
        page = body.get("features", [])
        features.extend(page)
        if not body.get("properties", {}).get("exceededTransferLimit") or not page:
            break
        offset += len(page)
    if not features:
        return gpd.GeoDataFrame({"prop_pfi": []}, geometry=[], crs="EPSG:4326")
    return gpd.GeoDataFrame.from_features(features, crs="EPSG:4326")

def lot_for_points(points, polygons):
    """
    Coordinate key -> {"land_m2", "land_lot_shared"} for each point, or None when
    no property polygon contains it (a point geocoded onto a road or reserve).

    A point inside a subdivided lot also sits inside the larger parent parcel it
    was cut from, so the listing's own lot is the smallest polygon around it.
    """
    pts = gpd.GeoDataFrame(
        {"key": [coord_key(lat, lon) for lon, lat in points]},
        geometry=gpd.points_from_xy([p[0] for p in points], [p[1] for p in points]),
        crs="EPSG:4326",
    )
    polygons = polygons.drop_duplicates("prop_pfi")
    polygons = polygons.assign(area=polygons.to_crs(AREA_CRS).area)
    hits = gpd.sjoin(pts, polygons, how="left", predicate="within")

    out = {}
    for key, g in hits.groupby("key"):
        areas = g["area"].dropna()
        if areas.empty:
            out[key] = None
            continue
        smallest = areas.min()
        shared = (areas <= smallest * (1 + SAME_LOT_TOLERANCE)).sum() > 1
        out[key] = {
            # A unit has no land of its own, so a shared lot gets no land size.
            "land_m2": None if shared else round(float(smallest), 1),
            "land_lot_shared": int(shared),
        }
    return out

def load_cache(path: Path):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            return {}
    return {}

def add_land_features(master, coverage, cache_dir):
    """
    land_m2, the area of the lot the listing sits on, and land_lot_shared, 1 when
    that lot is shared with other dwellings (apartments, strata townhouses).

    land_m2 is left empty on shared lots: the whole building's lot says nothing
    about one unit. Listings that fall outside every property polygon, usually
    because they were geocoded onto the street, get nulls in both columns.
    """
    cache_path = cache_dir / "vicmap_property_land.json"
    cache = load_cache(cache_path)

    has_coords = master["lat"].notna() & master["lon"].notna()
    keys = pd.Series(pd.NA, index=master.index, dtype="object")
    keys[has_coords] = [
        coord_key(lat, lon)
        for lat, lon in zip(master.loc[has_coords, "lat"], master.loc[has_coords, "lon"])
    ]

    todo = {}
    for k, lat, lon in zip(keys[has_coords], master.loc[has_coords, "lat"], master.loc[has_coords, "lon"]):
        if k not in cache:
            todo[k] = [round(lon, COORD_DP), round(lat, COORD_DP)]
    batches = [list(todo.values())[i:i + BATCH_POINTS] for i in range(0, len(todo), BATCH_POINTS)]
    if batches:
        print(f"Looking up land size for {len(todo):,} listing locations "
              f"in {len(batches)} Vicmap requests...")

    failures = []
    for i, points in enumerate(batches, 1):
        time.sleep(MIN_INTERVAL_S)
        try:
            polygons = query_polygons(points)
        except Exception as e:
            # A failed batch is not cached, so the next run retries it.
            failures.append(f"{type(e).__name__}: {e}")
            print(f"WARNING: Vicmap batch {i} failed: {e}")
            continue
        cache.update(lot_for_points(points, polygons))
        cache_path.write_text(json.dumps(cache))
        if i % 50 == 0:
            print(f"  {i}/{len(batches)} Vicmap requests")

    looked_up = keys.map(lambda k: cache.get(k) if isinstance(k, str) else None)
    for c in LAND_COLS:
        master[c] = looked_up.map(lambda r: r.get(c) if isinstance(r, dict) else np.nan).astype(float)

    n_land = int(master["land_m2"].notna().sum())
    n_shared = int((master["land_lot_shared"] == 1).sum())
    n_none = int(master["land_lot_shared"].isna().sum())
    record_source(
        coverage, SOURCE_NAME, "partial" if failures else "joined",
        f"{n_land:,}/{len(master):,} listings on their own lot have land_m2; "
        f"{n_shared:,} on a shared (strata) lot; {n_none:,} with no lot found"
        + (f"; {len(failures)} requests failed, rerun to retry them" if failures else ""),
    )
    return master
