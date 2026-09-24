"""OpenRouteService driving distance and time to the CBD and the nearest station."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from scipy.spatial import cKDTree

from .common import record_source
from .geo import nearest_features, sphere_xyz

SOURCE_NAME = "OpenRouteService driving routes"

# OpenRouteService driving matrix. The brief asks for proximity to the CBD and
# the nearest train station "as travelled by car", so these are road-network
# distances, not the straight-line nearest_*_km columns. The key is read from
# the ORS_API_KEY environment variable (free at account.heigit.org).
ORS_MATRIX_URL = "https://api.openrouteservice.org/v2/matrix/driving-car"

# The public plan allows 3,500 origin x destination pairs per matrix request,
# and counts quota per request, so each request is packed up to that limit.
ORS_MAX_PAIRS = 3500
# Public plan: 60 matrix requests a minute. One per second stays under it.
ORS_MIN_INTERVAL_S = 1.1

# Flinders Street Station, the usual reference point for "the CBD".
MELBOURNE_CBD = {"lat": -37.8183, "lon": 144.9671}

# Listing coordinates are rounded to 5 dp (~1 m) before routing, so listings in
# the same building share one route and one cache entry.
ORS_COORD_DP = 5

def coord_key(lat, lon):
    return f"{lat:.{ORS_COORD_DP}f},{lon:.{ORS_COORD_DP}f}"

class OrsMatrixClient:
    """
    Driving distance and time for origin/destination pairs, cached on disk.

    The cache maps origin key -> destination key -> [km, minutes], with None for
    a pair ORS could not route. It is saved after every request, so a run cut
    off by the daily quota picks up where it stopped.
    """

    def __init__(self, api_key, cache_path: Path):
        self.api_key = api_key
        self.cache_path = cache_path
        self.cache = {}
        if cache_path.exists():
            try:
                self.cache = json.loads(cache_path.read_text())
            except Exception:
                self.cache = {}
        self.requests_made = 0
        self._last_request = 0.0
        self._failed_in_a_row = 0

    def lookup(self, origin, dest):
        return self.cache.get(origin, {}).get(dest)

    def _post(self, locations, sources, destinations):
        """One matrix request. Returns (status, body)."""
        body = {
            "locations": locations,
            "sources": sources,
            "destinations": destinations,
            "metrics": ["distance", "duration"],
            "units": "km",
        }
        headers = {
            "Authorization": self.api_key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        # Four tries: a 429 means the per-minute window is full, so wait it out.
        for attempt in range(4):
            wait = ORS_MIN_INTERVAL_S - (time.time() - self._last_request)
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.time()
            try:
                r = requests.post(ORS_MATRIX_URL, json=body, headers=headers, timeout=180)
            except requests.RequestException as e:
                print(f"  ORS request error: {e}; retrying")
                time.sleep(10)
                continue
            self.requests_made += 1
            if r.status_code == 429:
                print("  ORS rate limit hit; waiting 60s")
                time.sleep(60)
                continue
            if r.status_code >= 500:
                time.sleep(10)
                continue
            return r.status_code, (r.json() if r.content else {})
        return None, {}

    def route(self, origins, dests):
        """
        Route every origin to every destination in one request and cache it.

        origins and dests are lists of (key, lat, lon). A 4xx on more than one
        origin is usually one point ORS cannot place on a road, so the origins
        are halved until the bad one is isolated and recorded as unroutable.
        """
        locations = [[lon, lat] for _, lat, lon in origins + dests]
        n = len(origins)
        status, body = self._post(
            locations, list(range(n)), list(range(n, n + len(dests)))
        )
        if status is None:
            raise RuntimeError("ORS matrix unavailable after retries")
        if status in (401, 403):
            # ORS answers 403 both to a bad key and to a spent daily quota.
            raise PermissionError(f"ORS refused the request (bad key or quota used up): {body}")
        if status != 200:
            if n > 1:
                half = n // 2
                self.route(origins[:half], dests)
                self.route(origins[half:], dests)
                return
            # One bad listing is expected now and then. A run of them means a
            # destination is the problem, and halving every batch down to
            # single origins would spend the whole daily quota finding that out.
            self._failed_in_a_row += 1
            if self._failed_in_a_row >= 20:
                raise RuntimeError(f"ORS rejected 20 single origins in a row: {body}")
            for dkey, _, _ in dests:
                self.cache.setdefault(origins[0][0], {})[dkey] = None
            self.save()
            return

        self._failed_in_a_row = 0
        dist = body.get("distances") or []
        dur = body.get("durations") or []
        for i, (okey, _, _) in enumerate(origins):
            row = self.cache.setdefault(okey, {})
            for j, (dkey, _, _) in enumerate(dests):
                km = dist[i][j] if i < len(dist) else None
                sec = dur[i][j] if i < len(dur) else None
                row[dkey] = None if km is None or sec is None else [km, sec / 60]
        self.save()

    def save(self):
        tmp = self.cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.cache))
        tmp.replace(self.cache_path)

def pack_batches(origin_dests, max_pairs=ORS_MAX_PAIRS):
    """
    Group origins into requests of at most max_pairs origin x destination pairs.

    origin_dests is a list of (origin, [dest, ...]). A request routes every
    origin in it to every destination any of them needs, so origins that share
    candidate destinations (neighbours) are packed together to keep the waste
    down. Returns a list of (origins, dests).
    """
    ordered = sorted(origin_dests, key=lambda od: tuple(sorted(d[0] for d in od[1])))
    batches, cur_o, cur_d = [], [], {}
    for origin, dests in ordered:
        merged = dict(cur_d)
        merged.update({d[0]: d for d in dests})
        if cur_o and (len(cur_o) + 1) * len(merged) > max_pairs:
            batches.append((cur_o, list(cur_d.values())))
            cur_o, merged = [], {d[0]: d for d in dests}
        cur_o.append(origin)
        cur_d = merged
    if cur_o:
        batches.append((cur_o, list(cur_d.values())))
    return batches

ROUTE_COLS = ["cbd_drive_km", "cbd_drive_min",
              "nearest_train_station_drive_km", "nearest_train_station_drive_min"]
CBD_DEST = ("cbd", MELBOURNE_CBD["lat"], MELBOURNE_CBD["lon"])

def listing_origins(master):
    """
    Per-listing origin key (NA without coordinates) and the distinct origins
    as (key, lat, lon) tuples.
    """
    lat = pd.to_numeric(master["lat"], errors="coerce")
    lon = pd.to_numeric(master["lon"], errors="coerce")
    valid = lat.notna() & lon.notna()
    okeys = pd.Series(pd.NA, index=master.index, dtype="object")
    okeys[valid] = [coord_key(a, b) for a, b in zip(lat[valid], lon[valid])]
    origins = (
        pd.DataFrame({"key": okeys[valid], "lat": lat[valid].round(ORS_COORD_DP),
                      "lon": lon[valid].round(ORS_COORD_DP)})
        .drop_duplicates("key").reset_index(drop=True)
    )
    return okeys, list(origins.itertuples(index=False, name=None))

def candidate_stations(origins, stations, k):
    """Origin key -> the k straight-line-nearest stations as (key, lat, lon)."""
    # load_stations already gives one point per station.
    st = stations.dropna(subset=["station_lat", "station_lon"]).reset_index(drop=True)
    if st.empty or not origins:
        return {}
    k = min(k, len(st))
    _, lats, lons = zip(*origins)
    tree = cKDTree(sphere_xyz(st["station_lat"].to_numpy(), st["station_lon"].to_numpy()))
    _, idx = tree.query(sphere_xyz(np.array(lats), np.array(lons)), k=k)
    idx = np.asarray(idx).reshape(len(origins), k)
    st_tuples = [
        (coord_key(a, b), round(a, ORS_COORD_DP), round(b, ORS_COORD_DP))
        for a, b in zip(st["station_lat"], st["station_lon"])
    ]
    return {o[0]: [st_tuples[j] for j in idx[i]] for i, o in enumerate(origins)}

def plan_requests(client, origins, candidates):
    """
    Matrix requests for every origin/destination pair not already cached.
    Returns (number of origins with something to route, [(origins, dests), ...]).
    """
    todo = []
    for o in origins:
        have = client.cache.get(o[0], {})
        wanted = [CBD_DEST] + candidates.get(o[0], [])
        need = [d for d in wanted if d[0] not in have]
        if need:
            todo.append((o, need))

    # The CBD is shared by every origin, so it gets its own requests rather than
    # widening every station batch by one column.
    cbd_todo = [o for o, need in todo if any(d[0] == "cbd" for d in need)]
    st_todo = [(o, [d for d in need if d[0] != "cbd"]) for o, need in todo]
    st_todo = [(o, need) for o, need in st_todo if need]

    batches = (
        [(cbd_todo[i:i + ORS_MAX_PAIRS - 1], [CBD_DEST])
         for i in range(0, len(cbd_todo), ORS_MAX_PAIRS - 1)]
        + pack_batches(st_todo)
    )
    return len(todo), batches

def run_requests(client, batches):
    """Send the batches; return "" on success or a note on why it stopped early."""
    try:
        for b, (o, d) in enumerate(batches, 1):
            client.route(o, d)
            if b % 10 == 0 or b == len(batches):
                print(f"  ORS {b}/{len(batches)} requests")
    except Exception as e:
        # Keep what was routed; the rest is fetched on the next run.
        print(f"WARNING: ORS routing stopped early: {e}")
        return f"partial: {e}"
    return ""

def route_features(client, origins, candidates):
    """ROUTE_COLS per origin key, from whatever the cache holds."""
    rows = {}
    for o in origins:
        key = o[0]
        cbd_r = client.lookup(key, "cbd")
        st_r = [client.lookup(key, d[0]) for d in candidates.get(key, [])]
        st_r = [r for r in st_r if r is not None]
        rows[key] = [
            cbd_r[0] if cbd_r else np.nan,
            cbd_r[1] if cbd_r else np.nan,
            min(r[0] for r in st_r) if st_r else np.nan,
            min(r[1] for r in st_r) if st_r else np.nan,
        ]
    return pd.DataFrame.from_dict(rows, orient="index", columns=ROUTE_COLS)

def add_ors_route_features(master, stations, coverage, api_key, cache_dir,
                           station_candidates=3):
    """
    Driving distance (km) and time (min) to the CBD and to the nearest train station.

    ORS matrix requests are expensive, so the nearest station by road is found
    among the station_candidates nearest by straight line. The road-nearest
    station is almost always among the closest few; checking one would pick
    the wrong station wherever a river or freeway lies between.

    The station distance and time are each the minimum over the candidates, so
    they can come from different stations when the shortest route is not the
    quickest.
    """
    # Straight-line CBD distance needs no key, so it is always added.
    cbd = pd.DataFrame({"cbd_lat": [MELBOURNE_CBD["lat"]], "cbd_lon": [MELBOURNE_CBD["lon"]]})
    master = nearest_features(master, cbd, "cbd_lat", "cbd_lon", "cbd")
    master = master.rename(columns={"nearest_cbd_km": "cbd_km"})

    if not api_key:
        record_source(
            coverage, SOURCE_NAME, "skipped",
            "no ORS_API_KEY set; only straight-line cbd_km added",
        )
        return master

    okeys, origins = listing_origins(master)
    candidates = candidate_stations(origins, stations, station_candidates)

    client = OrsMatrixClient(api_key, cache_dir / "ors_driving_matrix.json")
    n_todo, batches = plan_requests(client, origins, candidates)
    if batches:
        print(f"Routing {n_todo:,} of {len(origins):,} listing locations "
              f"with ORS in {len(batches)} requests...")
    status_note = run_requests(client, batches)

    out = route_features(client, origins, candidates).reindex(okeys.to_numpy())
    out.index = master.index
    for c in ROUTE_COLS:
        master[c] = out[c]

    n_cbd = int(master["cbd_drive_km"].notna().sum())
    n_st = int(master["nearest_train_station_drive_km"].notna().sum())
    record_source(
        coverage, SOURCE_NAME, "partial" if status_note else "joined",
        f"driving-car matrix to Flinders St and the road-nearest of "
        f"{station_candidates} straight-line-nearest stations; "
        f"{n_cbd:,}/{len(master):,} listings have a CBD route and {n_st:,} a "
        f"station route; {client.requests_made} ORS requests this run"
        + (f"; {status_note}" if status_note else ""),
    )
    return master
