"""Straight-line nearest distances and point-in-SA2 counts."""

from __future__ import annotations

import numpy as np
import geopandas as gpd
from scipy.spatial import cKDTree

EARTH_RADIUS_KM = 6371.0088

def sphere_xyz(lat, lon):
    """Lat/lon in degrees to 3D unit-sphere points, so a KD tree can search them."""
    lat = np.radians(lat)
    lon = np.radians(lon)
    return np.c_[np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)]

def nearest_features(properties, features, lat_col, lon_col, prefix):
    """
    Fast nearest straight-line distance. Driving distances to the CBD and the
    nearest train station come from ORS instead (routing.add_ors_route_features).
    """
    out = properties.copy()
    valid = features[[lat_col, lon_col]].dropna().copy()
    if valid.empty:
        out[f"nearest_{prefix}_km"] = np.nan
        return out

    fxyz = sphere_xyz(valid[lat_col].to_numpy(), valid[lon_col].to_numpy())
    tree = cKDTree(fxyz)

    mask = out["lat"].notna() & out["lon"].notna()
    pxyz = sphere_xyz(out.loc[mask, "lat"].to_numpy(), out.loc[mask, "lon"].to_numpy())
    dist_chord, idx = tree.query(pxyz, k=1)
    # chord -> central angle -> km
    angle = 2 * np.arcsin(np.clip(dist_chord / 2, 0, 1))
    out.loc[mask, f"nearest_{prefix}_km"] = EARTH_RADIUS_KM * angle
    return out

def locate_in_sa2(points, sa2, lat_col, lon_col):
    """Tag each point with the sa2_code_2021 it falls in (NaN outside Victoria)."""
    pg = gpd.GeoDataFrame(
        points,
        geometry=gpd.points_from_xy(points[lon_col], points[lat_col]),
        crs="EPSG:4326",
    )
    return gpd.sjoin(
        pg,
        sa2[["sa2_code_2021", "geometry"]],
        how="left",
        predicate="within",
    )

def point_counts_by_sa2(points, sa2, lat_col, lon_col, name):
    """Number of points in every SA2, as columns sa2_code_2021 and {name}_count."""
    col = f"{name}_count"
    out = sa2[["sa2_code_2021"]].copy()
    points = points.dropna(subset=[lat_col, lon_col])
    if points.empty:
        out[col] = 0
        return out
    j = locate_in_sa2(points, sa2, lat_col, lon_col)
    counts = j.groupby("sa2_code_2021").size().rename(col)
    return out.merge(counts, on="sa2_code_2021", how="left").fillna({col: 0})

def add_point_access(listings, sa2_master, sa2, points, lat_col, lon_col, name):
    """
    Distance from each listing to the nearest point (nearest_{name}_km) and
    the number of points in each SA2 ({name}_count).
    """
    listings = nearest_features(listings, points, lat_col, lon_col, name)
    counts = point_counts_by_sa2(points, sa2, lat_col, lon_col, name)
    sa2_master = sa2_master.merge(counts, on="sa2_code_2021", how="left")
    return listings, sa2_master
