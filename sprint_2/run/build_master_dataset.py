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
  * OpenRouteService driving distance/time to the CBD (Flinders St) and the
    nearest train station. Needs ORS_API_KEY set; without it only the
    straight-line cbd_km is added.

Install:
    pip install pandas geopandas shapely requests pyogrio openpyxl scipy

Usage:
    python sprint_2/run/build_master_dataset.py   # or run.py, which wraps it

Notes:
  * OSM enrichment is optional because Overpass can be slow/rate-limited.
  * Each source lives in its own module under pipeline/; this file only wires
    them together in order.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import pandas as pd

from pipeline.common import DOMAIN_CSV, OUTPUT_DIR, record_source
from pipeline.abs_sources import (
    PERSONAL_INCOME_SNAPSHOT_YEAR, build_sa2_yearly, load_gcp,
    load_personal_income_yearly, load_regional_population,
    load_regional_population_yearly, load_sa2, load_seifa, load_vif,
    personal_income_snapshot,
)
from pipeline.access import add_osm_access, load_osm_amenities, load_schools, load_stations
from pipeline.crime import add_crime_features, build_crime_reference
from pipeline.crime import SOURCE_NAME as CRIME_SOURCE
from pipeline.geo import add_point_access
from pipeline.listings import join_sa2, load_listings, split_target_derived
from pipeline.routing import add_ors_route_features
from pipeline.sqm import SQM_RENTS_CSV, add_sqm_market_features, load_sqm_weekly_rents
from pipeline.sqm import SOURCE_NAME as SQM_SOURCE


def parse_args():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=str(DOMAIN_CSV))
    ap.add_argument("--output-dir", default=str(OUTPUT_DIR))
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
    ap.add_argument("--no-ors", action="store_true",
                    help="Skip OpenRouteService driving routes (straight-line cbd_km is still added).")
    ap.add_argument("--ors-station-candidates", type=int, default=3,
                    help="Straight-line-nearest stations to route to when finding the road-nearest one.")
    return ap.parse_args()


def load_sa2_yearly(cache, coverage):
    """
    Yearly SA2 series for the forecasting model, plus the income series the
    snapshot needs. A failure here should not stop the listing table.
    """
    yearly_sources = [
        ("ABS Regional Population 2001-2025 (yearly)", load_regional_population_yearly),
        ("ABS Personal Income 2016-17 to 2022-23 (yearly)", load_personal_income_yearly),
    ]
    yearly_frames = []
    for name, loader in yearly_sources:
        try:
            d = loader(cache)
            detail = f"{d['year'].min()}-{d['year'].max()}, {d['sa2_code_2021'].nunique()} SA2s"
            record_source(coverage, name, "joined", f"sa2_yearly.csv: {detail}")
        except Exception as e:
            print(f"WARNING: {name} failed: {e}")
            record_source(coverage, name, "failed", str(e))
            d = pd.DataFrame(columns=["sa2_code_2021", "year"])
        yearly_frames.append(d)
    population_yearly, income_yearly = yearly_frames
    return build_sa2_yearly(population_yearly, income_yearly), income_yearly


def build_sa2_master(sa2, income_yearly, cache, coverage):
    """One row per SA2 with every area-level snapshot source joined on."""
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
        matched = sa2_master["sa2_code_2021"].isin(d["sa2_code_2021"]).sum()
        record_source(
            coverage, name, "joined", "Joined to SA2 master",
            matches=int(matched), total=int(len(sa2_master)),
        )
        sa2_master = sa2_master.merge(d, on="sa2_code_2021", how="left")
    return sa2_master


def add_as_of_sources(master, args, cache, coverage):
    """Sources joined on each listing's date_listed: SQM rents and crime."""
    if args.no_sqm:
        record_source(coverage, SQM_SOURCE, "skipped", "--no-sqm")
    else:
        sqm_path = Path(args.sqm_csv)
        master = add_sqm_market_features(
            master,
            load_sqm_weekly_rents(sqm_path),
            coverage,
            tolerance_days=args.sqm_tolerance_days,
            source_path=sqm_path,
        )

    if args.no_crime:
        record_source(coverage, CRIME_SOURCE, "skipped", "--no-crime")
    else:
        try:
            master = add_crime_features(
                master,
                build_crime_reference(cache, args.crime_xlsx),
                coverage,
            )
        except Exception as e:
            print(f"WARNING: crime enrichment failed: {e}")
            record_source(coverage, CRIME_SOURCE, "failed", str(e))
    return master


def write_outputs(outdir, master, sa2_master, sa2_yearly, rent_ratios, coverage):
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


def main():
    args = parse_args()
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    cache = outdir / "_cache"
    cache.mkdir(exist_ok=True)
    coverage = []

    # 1. Listings, placed in their SA2 ---------------------------------
    sa2 = load_sa2()
    listings = join_sa2(load_listings(args.input), sa2)

    # 2. Area-level sources -------------------------------------------
    sa2_yearly, income_yearly = load_sa2_yearly(cache, coverage)
    sa2_master = build_sa2_master(sa2, income_yearly, cache, coverage)

    # 3. Schools and stations -----------------------------------------
    schools = load_schools()
    if not schools.empty:
        listings, sa2_master = add_point_access(
            listings, sa2_master, sa2, schools, "school_lat", "school_lon", "school"
        )
    stations = load_stations()
    if not stations.empty:
        listings, sa2_master = add_point_access(
            listings, sa2_master, sa2, stations, "station_lat", "station_lon", "train_station"
        )

    # 4. Driving routes to the CBD and nearest station (ORS) ----------
    listings = add_ors_route_features(
        listings, stations, coverage,
        api_key=None if args.no_ors else os.environ.get("ORS_API_KEY"),
        cache_dir=cache,
        station_candidates=args.ors_station_candidates,
    )

    # 5. Optional OSM --------------------------------------------------
    if not args.no_osm:
        osm = load_osm_amenities(sa2, cache)
        if not osm.empty:
            listings, sa2_master = add_osm_access(listings, sa2_master, sa2, osm)

    # 6. SA2 master onto listings, then the as-of joins ---------------
    master = listings.drop(columns="geometry").merge(
        sa2_master,
        on="sa2_code_2021",
        how="left",
        suffixes=("","_area"),
    )
    master = add_as_of_sources(master, args, cache, coverage)

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

    # 7. Outputs -------------------------------------------------------
    write_outputs(outdir, master, sa2_master, sa2_yearly, rent_ratios, coverage)


if __name__ == "__main__":
    main()
