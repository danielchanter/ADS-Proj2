# Sprint 2 — Victorian Rental Data Pipeline

This folder builds the enriched Victorian rental-property master table from the
raw Domain rental listings.

## Run

From the repository root:

```bash
source .venv/bin/activate
pip install -r sprint_2/requirements.txt
python sprint_2/run/run.py
```

OpenStreetMap amenities are now included by default. If Overpass is unavailable
or you need a quick run:

```bash
python sprint_2/run/run.py --no-osm
```

## Sources handled by the pipeline

Core joins:
- Domain rental listings
- ABS ASGS 2021 SA2 geography
- ABS Census 2021 General Community Profile
- ABS SEIFA 2021
- ABS Regional Population 2025
- ABS Personal Income (best-effort workbook parser)
- Victoria in Future 2023
- Victorian school locations
- Metro station accessibility (best-effort endpoint)
- OpenStreetMap amenities

Additional registered/discovered sources:
- Victorian Property Sales Report
- DFFH Rental Report
- Crime Statistics Victoria
- School Zones
- PTV timetable data
- Vicmap Features of Interest
- Digital Atlas of Australia
- ABS Building Approvals
- ABS CPI / Melbourne rents
- ASGS 2026 correspondence

The pipeline deliberately does not blindly merge coarse or differently-defined
geographies. Those sources are recorded in `source_coverage.csv` and
`extra_source_manifest.csv` so the modelling team can distinguish:
1. features actually joined to each listing,
2. forecast/context sources, and
3. sources requiring a defined period/geographic correspondence.

This prevents suburb/LGA values from being incorrectly presented as exact SA2
measurements.

## Outputs

`data/processed/`
- `vic_property_master.csv` — main listing-level table
- `sa2_master.csv` — SA2-level table
- `source_coverage.csv` — source-by-source join/status audit
- `extra_source_manifest.csv` — current discovered DataVic resources

Temporary downloads are cached under `data/processed/_cache/`.


## Overpass / OSM reliability

OpenStreetMap amenity enrichment uses public Overpass servers. These services can
temporarily return HTTP 406, 429, or 5xx responses. The pipeline now tries
multiple public mirrors and both POST and GET requests. If all mirrors fail, OSM
is skipped and the rest of the master dataset is still produced.

For a deliberately fast run without OSM:

```bash
python sprint_2/run/run.py --no-osm
```


## Train station access

Train access now uses the Victorian Department of Transport and Planning
**Public Transport Stops** statewide GeoJSON as the preferred source.

The pipeline filters the stop data to:
- `METRO TRAIN`
- `REGIONAL TRAIN`

It adds:
- `nearest_train_station_km` for each rental listing
- `train_station_count` for the listing's SA2

The older Metro Train Stations accessibility dataset is only used as a fallback.
