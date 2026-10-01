"""
Public green space: the planning scheme's park and conservation zones, plus
OpenStreetMap park outlines for the growth-area parks the zones miss.
"""

from __future__ import annotations

import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
import shapely
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

from .access import OSM_AMENITIES, overpass_bbox_query
from .common import RESEARCH_UA, record_source

SOURCE_NAME = "Vicmap Planning and OSM green space"

PLAN_ZONE_URL = (
    "https://services-ap1.arcgis.com/P744lA0wf4LlBZ84/ArcGIS/rest/services/"
    "Vicmap_Planning/FeatureServer/3/query"
)

# Public Park and Recreation Zone: parks, reserves and sports grounds.
# Public Conservation and Resource Zone: national and state parks, foreshores
# and river corridors. Together they are the state's public green space.
GREEN_ZONES = {"PPRZ": "park", "PCRZ": "conservation"}

# Polygons are requested in VicGrid GDA2020, the equal-area CRS land.py measures
# in, and simplified to 1 m on the server: that changes the statewide area by
# less than 0.001% and cuts the download by three quarters.
AREA_CRS = "EPSG:7899"
MAX_OFFSET_M = 1

# Radius for green_space_1km_ha, about a 12-minute walk.
CATCHMENT_M = 1000

# ---------------------------------------------------------------------
# PLANNING ZONES
# ---------------------------------------------------------------------

def fetch_zone_polygons():
    """Every PPRZ and PCRZ polygon in Victoria, following the service's paging."""
    codes = ",".join(f"'{c}'" for c in GREEN_ZONES)
    params = {
        "where": f"zone_code IN ({codes})",
        "outFields": "pfi,zone_code",
        "orderByFields": "OBJECTID",
        "outSR": AREA_CRS.split(":")[1],
        "maxAllowableOffset": MAX_OFFSET_M,
        "geometryPrecision": 1,
        "f": "geojson",
    }
    features = []
    offset = 0
    while True:
        r = requests.get(
            PLAN_ZONE_URL,
            params={**params, "resultOffset": offset},
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
    return features

def load_cached_json(path: Path, fetch, label):
    """The cached download at path, fetched and saved first if missing or unreadable."""
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    print(f"Fetching {label}...")
    data = fetch()
    if data is not None:
        path.write_text(json.dumps(data))
    return data

def load_zones(cache_dir):
    features = load_cached_json(
        Path(cache_dir) / "vicmap_green_space.json", fetch_zone_polygons,
        "Vicmap Planning park and conservation zones",
    )
    zones = gpd.GeoDataFrame.from_features(features, crs=AREA_CRS)
    return zones.assign(kind=zones["zone_code"].map(GREEN_ZONES))[["kind", "geometry"]]

# ---------------------------------------------------------------------
# OSM PARKS
# ---------------------------------------------------------------------

def element_polygon(e):
    """A closed way or a multipolygon relation as a shapely geometry, else None."""
    if e["type"] == "way":
        coords = [(p["lon"], p["lat"]) for p in e.get("geometry", [])]
        if len(coords) >= 4 and coords[0] == coords[-1]:
            return Polygon(coords)
        return None
    if e["type"] == "relation" and e.get("tags", {}).get("type") == "multipolygon":
        rings = {"outer": [], "inner": []}
        for m in e.get("members", []):
            if m["type"] == "way" and m.get("geometry"):
                role = "inner" if m.get("role") == "inner" else "outer"
                rings[role].append(LineString([(p["lon"], p["lat"]) for p in m["geometry"]]))
        # Member ways are pieces of rings, so they are stitched back together.
        shape = unary_union(list(polygonize(rings["outer"])))
        if rings["inner"]:
            shape = shape.difference(unary_union(list(polygonize(rings["inner"]))))
        return None if shape.is_empty else shape
    return None

def load_osm_parks(sa2, cache_dir):
    """
    leisure=park outlines across Victoria, or an empty frame if Overpass is down.

    The planning zones miss most parks in the growth areas: a new estate's parks
    stay under its Urban Growth Zone until the council rezones them, so
    Truganina, Wollert and Craigieburn show no green space from the zones
    alone. OSM maps those parks.
    """
    minx, miny, maxx, maxy = sa2.total_bounds
    elems = load_cached_json(
        Path(cache_dir) / "osm_park_polygons.json",
        lambda: overpass_bbox_query(OSM_AMENITIES["park"], miny, minx, maxy, maxx, out="geom qt"),
        "OSM park outlines",
    )
    if not elems:
        return gpd.GeoDataFrame({"kind": []}, geometry=[], crs=AREA_CRS)
    shapes = [s for s in map(element_polygon, elems) if s is not None]
    parks = gpd.GeoDataFrame({"kind": "park"}, geometry=shapes, crs="EPSG:4326")
    return parks.to_crs(AREA_CRS)

# ---------------------------------------------------------------------
# COMBINED LAYER
# ---------------------------------------------------------------------

def load_green_space(sa2, cache_dir):
    """
    Green space clipped to Victoria's SA2s, with columns sa2_code_2021, kind
    ('park' or 'conservation') and geometry in AREA_CRS. No two rows overlap.

    Where an OSM park and a zone overlap they are one piece of land and are
    counted once; land in a conservation zone is counted as conservation even
    when OSM also calls it a park. Clipping to the SA2s removes the parts of
    coastal PCRZ that lie over the sea.
    """
    osm = load_osm_parks(sa2, cache_dir)
    green = pd.concat([load_zones(cache_dir), osm], ignore_index=True)
    # A handful of polygons self-intersect after simplification.
    green["geometry"] = shapely.make_valid(green.geometry.values)
    green = gpd.overlay(
        green, sa2[["sa2_code_2021", "geometry"]].to_crs(AREA_CRS),
        how="intersection", keep_geom_type=True,
    )
    merged = green.dissolve(by=["sa2_code_2021", "kind"]).reset_index()
    conservation = merged[merged["kind"].eq("conservation")].set_index("sa2_code_2021").geometry
    park = merged["kind"].eq("park")
    merged.loc[park, "geometry"] = [
        g.difference(conservation[code]) if code in conservation.index else g
        for code, g in zip(merged.loc[park, "sa2_code_2021"], merged.loc[park, "geometry"])
    ]
    # One row per separate piece, so distance and catchment queries stay local.
    pieces = merged.explode(index_parts=False).reset_index(drop=True)
    pieces = pieces[pieces.geom_type.isin(["Polygon", "MultiPolygon"]) & ~pieces.is_empty]
    return pieces, len(osm)

def add_green_space_features(listings, sa2_master, sa2, coverage, cache_dir):
    """
    Per listing: nearest_green_space_km, the distance to the edge of the nearest
    green space (0 inside one), and green_space_1km_ha, the green space within
    CATCHMENT_M of it.

    Per SA2: park_pct and conservation_pct, the share of the SA2's area in each,
    and park_m2_per_person, park area per resident (erp_2025). Parks are kept
    apart from conservation land because national parks would otherwise make
    every regional SA2 look greener than any suburb.
    """
    try:
        green, n_osm = load_green_space(sa2, cache_dir)
    except Exception as e:
        print(f"WARNING: green space unavailable: {e}")
        record_source(coverage, SOURCE_NAME, "failed", str(e))
        return listings, sa2_master

    # SA2 shares of area.
    area = (
        green.assign(area_m2=green.area)
             .groupby(["sa2_code_2021", "kind"])["area_m2"].sum()
             .unstack(fill_value=0)
             .reindex(columns=list(GREEN_ZONES.values()), fill_value=0)
    )
    area = sa2[["sa2_code_2021"]].assign(
        sa2_m2=sa2.to_crs(AREA_CRS).area.to_numpy()
    ).merge(area, left_on="sa2_code_2021", right_index=True, how="left").fillna(0)
    shares = area[["sa2_code_2021"]].copy()
    for kind in GREEN_ZONES.values():
        shares[f"{kind}_pct"] = (100 * area[kind] / area["sa2_m2"]).round(2)
    shares["park_m2"] = area["park"]
    sa2_master = sa2_master.merge(shares, on="sa2_code_2021", how="left")
    sa2_master["park_m2_per_person"] = (
        sa2_master.pop("park_m2") / sa2_master["erp_2025"].replace(0, np.nan)
    ).round(1)

    # Per-listing distance and catchment.
    has_coords = listings["lat"].notna() & listings["lon"].notna()
    pts = gpd.GeoSeries(
        gpd.points_from_xy(listings.loc[has_coords, "lon"], listings.loc[has_coords, "lat"]),
        index=listings.index[has_coords],
        crs="EPSG:4326",
    ).to_crs(AREA_CRS)
    geoms = green.geometry.values
    tree = shapely.STRtree(geoms)

    nearest = tree.query_nearest(pts.values, return_distance=True, all_matches=False)[1]
    listings.loc[pts.index, "nearest_green_space_km"] = np.round(nearest / 1000, 3)

    buffers = pts.buffer(CATCHMENT_M).values
    ipt, igreen = tree.query(buffers, predicate="intersects")
    overlap = shapely.area(shapely.intersection(buffers[ipt], geoms[igreen]))
    catchment = np.bincount(ipt, weights=overlap, minlength=len(pts))
    listings.loc[pts.index, "green_space_1km_ha"] = np.round(catchment / 1e4, 2)

    km2 = green.assign(a=green.area / 1e6).groupby("kind")["a"].sum()
    record_source(
        coverage, SOURCE_NAME, "joined" if n_osm else "partial",
        f"{km2.get('park', 0):,.0f} km2 park, {km2.get('conservation', 0):,.0f} km2 conservation; "
        + (f"{n_osm:,} OSM park outlines added to the zones; " if n_osm
           else "OSM park outlines unavailable, zones only (growth areas undercounted); ")
        + f"{int(listings['nearest_green_space_km'].notna().sum()):,} listings measured",
    )
    return listings, sa2_master
