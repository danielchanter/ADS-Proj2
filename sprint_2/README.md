# Sprint 2: Victorian Rental Data Pipeline

This folder builds the enriched Victorian rental-property master table from the
raw Domain rental listings.

## Run

The raw Domain listings must first be placed at `sprint_2/data/raw/vic_rentals_all.csv`
(course data from Canvas; it is not committed).

From the repository root:

```bash
# macOS / Linux
source .venv/bin/activate
# Windows (PowerShell):  .venv\Scripts\Activate.ps1

pip install -r sprint_2/requirements.txt
python sprint_2/run/run.py
```

`run.py` wraps `run/build_master_dataset.py`, which wires together one module
per source family under `run/pipeline/`: `listings`, `correspondence`, `abs_sources`, `access`
(schools, stations, tram and bus stops, OSM), `sqm`, `crime`, `routing` (ORS)
and `land` (Vicmap Property), with shared
helpers in `common` and `geo`.

After each run, check `data/processed/source_coverage.csv`: any core source
with `status = failed` (fewer than 90% of Victorian SA2s matched) or any
"column check" row means a loader silently returned nothing.

OpenStreetMap amenities are now included by default. If Overpass is unavailable
or you need a quick run:

```bash
python sprint_2/run/run.py --no-osm
```

## Rent time series

The Domain data is a single snapshot scraped 2025-09-09, so it has no rent time
series. `fetch_sqm_rents.py` pulls the SQM Research Weekly Rents Index for every
postcode in the Domain data (weekly, per postcode, 2009 to the current week),
which is what bridges the snapshot to the present:

```bash
python sprint_2/run/fetch_sqm_rents.py            # all Domain postcodes, ~10 min
python sprint_2/run/fetch_sqm_rents.py --postcodes 3168 3000
```

Writes `data/external/sqm_weekly_rents.csv` (long format) and a per-postcode
coverage audit. Use it as a **growth index** anchored on the Domain snapshot;
see `external_datasets.md` for the validation against the 2025-09 overlap. SQM data is free for reference use but must not be
redistributed, so `data/` stays gitignored.

### How it joins to the master table

Run the fetcher **before** `run.py`. The pipeline then joins the index onto each
listing as-of its `date_listed`, so a listing sees the market as it was when it
was advertised:

```bash
python sprint_2/run/fetch_sqm_rents.py   # writes data/external/sqm_weekly_rents.csv
python sprint_2/run/run.py
```

If the CSV is missing the join is skipped and recorded as `missing` in
`source_coverage.csv`; the rest of the pipeline is unaffected. Flags:
`--no-sqm` to skip it, `--sqm-csv` for a different path, `--sqm-tolerance-days`
(default 31) to change how stale a matched week may be.

Columns added, all prefixed `sqm_`:

| Column | Meaning |
| --- | --- |
| `sqm_week_lag_days` | how far before the listing date the matched index week sits |
| `sqm_market_rent` | the type-matched series at that week: house index for houses, unit index for units, combined if ambiguous |
| `sqm_market_rent_yoy_pct` | market momentum in that postcode at the listing date, matched 52 weeks back by date |
| `sqm_market_rent_bedroom_matched` | tighter comparable, non-null only for 3-bed houses and 2-bed units |

The five raw SQM series are read but not kept: for any one listing, four of
them are the wrong property type. `rent_vs_sqm_market` is in
`vic_property_rent_ratios.csv` (see "Rent-derived columns" below).

Two things worth knowing:

- **Tolerance matters.** Thin rural postcodes drop out of the index for months
  at a time. The 31-day cap gives them a null during those gaps rather than a
  stale value. SQM does not publish postcode 3413 at all.
- **Zeros mean missing.** SQM writes 0 when a series had no advertised
  stock that week, so the loader reads zeros as null.

## Crime

Crime Statistics Victoria's **LGA Criminal Incidents** workbook is joined onto
every listing. Nothing needs to be run first: the pipeline finds the current
workbook on CSA's download page, caches it under `data/processed/_cache/`, and
uses one of its sheets:

- **Table 03**: incidents by postcode, suburb and offence division. CSA
  publishes raw counts with no denominator, so the pipeline divides by the ABS
  Census 2021 suburb (SAL) population to get a rate.

Flags: `--no-crime` to skip, `--crime-xlsx` to point at a workbook you already
have. A matched reporting period may be at most 370 days stale.

### How it joins

CSA reports rolling years labelled by the month they end in, so "2025, year
ending March" covers 2024-04-01 to 2025-03-31. Each listing gets the most recent
period that had **already closed** when it was advertised. This is a backward
as-of join on `date_listed`, the same shape as the SQM join. A listing from mid-2024
therefore gets the year ending March 2024, since the year ending March 2025 was
still in progress.

Listings are matched to a suburb on `(postcode, suburb)`. A listing whose suburb
CSA does not name, or with no postcode or listing date, gets nulls in every
crime column.

Columns added, all prefixed `crime_`:

| Column | Meaning |
| --- | --- |
| `crime_suburb_population` | the suburb's Census 2021 population, the rate's denominator |
| `crime_suburb_rate_per_1k` | incidents per 1,000 residents, the headline suburb measure |
| `crime_suburb_{person,property,drug,public_order,justice,other}_rate_per_1k` | the same rate split by offence division |
| `crime_suburb_rate_yoy_pct`, `crime_suburb_rate_3yr_change_pct` | whether the suburb is getting better or worse |

Three things worth knowing:

- **Incidents are counted where they happen, and the denominator is residents.**
  Mostly industrial or commercial suburbs therefore show very high rates. The
  same effect is real signal in a CBD, so the rate is kept as computed.
  `crime_suburb_population` is kept so the rate can be filtered or weighted.
- **The workbook starts at the year ending March 2017**, so listings advertised
  before then get nulls. The 370-day tolerance is one reporting period plus
  slack.
- **Suburb counts are summed across postcodes.** CSA splits a few suburbs over
  two postcodes: Beaumaris files 3,070 incidents under 3193 and 24 under 3192.
  The ABS population covers the whole suburb, so the incidents are totalled to
  the whole suburb to match.

Fifteen Victorian suburb names are used twice (Hillside, Newtown, Ascot…). They
are told apart by the LGA the incidents sit in, which ABS encodes in the SAL name
(`Hillside (Melton - Vic.)`). All of them resolve on the current data. Any
future unresolved name is matched to the more populous suburb and counted in
the crime row of `source_coverage.csv`.

## Sources handled by the pipeline

Core joins:
- Domain rental listings
- ABS ASGS 2021 SA2 geography
- ABS ASGS 2021 mesh-block allocation files and Census 2021 mesh block counts:
  the suburb/postcode/SA2 correspondence (see below)
- ABS Census 2021 General Community Profile
- ABS SEIFA 2021
- ABS Regional Population 2025, plus yearly SA2 population 2001-2025
- ABS Personal Income by SA2, 2016-17 to 2022-23 (see "Yearly SA2 series" below)
- Victoria in Future 2023
- Victorian school locations
- DTP Public Transport Stops: train stations, tram stops and bus stops
- OpenStreetMap amenities
- SQM Research weekly postcode rent index (as-of join, see above)
- Crime Statistics Victoria, suburb level (as-of join, see above)
- OpenRouteService driving distance/time to the CBD and nearest station (see below)
- Vicmap Property: land size of the lot each listing sits on (see below)

Candidate sources not joined yet. Each needs a period or geographic
correspondence defined before it can be joined without presenting suburb/LGA
values as exact SA2 measurements:
- [DFFH Rental Report](https://www.dffh.vic.gov.au/publications/rental-report):
  signed-rent benchmark; avoid a same-period suburb median as a predictor
- [Victorian Property Sales Report](https://discover.data.vic.gov.au/dataset/victorian-property-sales-report-median-house-by-suburb-time-series)
- [School Zones](https://discover.data.vic.gov.au/dataset/?q=school+zones)
- [PTV Timetable API](https://discover.data.vic.gov.au/dataset/ptv-timetable-api)
- [Vicmap Features of Interest](https://discover.data.vic.gov.au/dataset/vicmap-features-of-interest-rest-api)
- [ABS Building Approvals](https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia/latest-release):
  SA2 small-area approvals, a supply covariate for forecasting
- [ABS CPI](https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia/latest-release):
  Melbourne rents, a Melbourne-wide time series, not an SA2 differentiator
- [ASGS 2026](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/latest-release):
  correspondence needed for outputs on 2026 geography

## What is kept, and what is left out

Columns that restate another column are not written, so the modelling table has
one column per piece of information:

- **SEIFA:** the four index scores only. The national deciles and percentiles
  are rank transforms of the scores (r ≥ 0.95).
- **Population:** `erp_2025`, its 2024-25 growth, density and migration. The
  2024 level, the change in persons, births, deaths and the 2021 Census count
  all restate those; the full ERP history is in `sa2_yearly.csv`.
- **Victoria in Future:** `vif_population_growth_pct_2026_31`, the projected
  five-year change. The projected levels are near-copies of `erp_2025`, and the
  2026-36 change correlates with the 2026-31 one at 0.99.
- **Geography:** SA2 code and name, then SA3/SA4/GCCSA names only (each code
  maps one-to-one onto its name).
- **Listing:** `secondary_type` is dropped because it equals `property_type` on
  every listing.

### Amenity flags (`feat_*`)

Domain's `structured_features` holds 571 distinct free-text labels, many of
them synonyms. They are grouped into 18 0/1 flags (`feat_air_conditioning`,
`feat_pets_allowed`, `feat_furnished`, …) and the raw text is dropped. The
grouping patterns are `STRUCTURED_FEATURE_FLAGS` in `run/pipeline/listings.py`.
Listings with no feature list get empty flags rather than 0, since a
blank field says nothing about whether the property has a dishwasher.

### Rent-derived columns (`vic_property_rent_ratios.csv`)

Columns computed from `weekly_rent` would leak the target into the model and
inflate its "most important features". They are written to a separate table,
keyed on `listing_id`, for the affordability analysis (Q3) and for checking
predictions against the market:

| column | meaning |
|---|---|
| `bond` | set from the rent, usually one month of it; nulled outside 2-8 weeks' rent as a data error |
| `rent_per_bedroom` | `weekly_rent / bedrooms` |
| `rent_vs_2021_census_median` | against the SA2's 2021 Census median rent |
| `rent_vs_sqm_market` | against the postcode's SQM market rent at the listing date |
| `rent_to_area_median_hh_income_pct` | weekly rent as a % of the SA2's weekly median household income |

`weekly_rent` itself is not cleaned.

## Suburbs, postcodes and SA2s (`suburb_postcode_sa2.csv`)

The sources come on three geographies that do not nest: SQM rents per
postcode, the ABS and VIF covariates per SA2, and Q2 asks for suburbs. Of
Victoria's 2,944 suburbs, 417 cross an SA2 boundary and 43 cross a postcode
boundary. Postcode 3168 is Clayton and Notting Hill, and Clayton itself is
split over two SA2s.

The correspondence is built from ABS mesh blocks, the smallest ABS areas. Each
Victorian mesh block has one suburb (SAL), one postcode (POA) and one SA2, taken
from the ASGS 2021 allocation files, and a Census 2021 dwelling count. Summing
dwellings over mesh blocks gives the size of every suburb × postcode × SA2
overlap. There is one row per overlap:

| column | meaning |
|---|---|
| `sal_code_2021`, `sal_name_2021` | ABS suburb; repeated names carry a disambiguator, `Hillside (Melton - Vic.)` |
| `postcode` | ABS postal area |
| `sa2_code_2021`, `sa2_name_2021` | SA2 |
| `dwellings_2021`, `persons_2021`, `area_sqkm` | size of the overlap |
| `share_of_suburb`, `share_of_postcode`, `share_of_sa2` | the overlap's share of each area's dwellings |

To move a value onto suburbs, weight by `share_of_suburb`:

```python
corr = pd.read_csv("suburb_postcode_sa2.csv", dtype={"postcode": str, "sa2_code_2021": str})
x = corr.merge(sa2_master[["sa2_code_2021", "vif_population_growth_pct_2026_31"]], on="sa2_code_2021")
suburb_vif = (x["share_of_suburb"] * x["vif_population_growth_pct_2026_31"]).groupby(x["sal_code_2021"]).sum()
```

Postcode rents work the same way, joined on `postcode`. Within one suburb,
postcode or SA2 the shares sum to 1.

Each listing also gets `sal_code_2021` and `sal_name_2021`, matched on its
Domain `(suburb, postcode)`. The postcode is needed because suburb names repeat
(Ascot is in both Ballarat and Bendigo, Hillside in both Melton and East Gippsland). 12,716 of 12,717 listings match: the
exception is Hidden Valley 3756, which is not an ABS suburb.

Things worth knowing:

- **Weights are 2021 dwellings.** Rents are about dwellings, so dwellings are a
  better weight than land area. 123 suburbs (parks, industrial estates) have no
  dwellings, and their shares fall back to land area.
- **ABS adds random noise to the counts** to protect confidentiality, so
  a tiny overlap can show 0 dwellings next to a few persons.
- **An ABS postal area is not an Australia Post postcode.** ABS builds postal
  areas from mesh blocks, so the edges are approximate and a few PO-box-only
  postcodes are missing. Every Domain and SQM postcode has a matching postal
  area.
- **The listing's suburb comes from Domain's label, and its SA2 from its
  coordinates.** For 0.5% of listings the SA2 is not one the suburb touches.
  These listings sit near a boundary or carry a neighbouring suburb's name
  (15 of them are in Wodonga).
- The four ABS workbooks (about 90 MB) are cached under `_cache/`, and the
  joined Victorian mesh-block table is cached as `vic_mesh_blocks.csv`. The first
  run adds about a minute.

## Yearly SA2 series (`sa2_yearly.csv`)

Every other area-level source is a single snapshot, which gives a panel model
nothing that changes over time. `sa2_yearly.csv` holds the SA2 covariates that
ABS publishes every year, on the same ASGS 2021 boundaries as everything else,
so no boundary conversion is needed.

| column | meaning |
|---|---|
| `year` | population at 30 June of this year; income for the financial year ending in it (2022-23 → 2023) |
| `erp`, `erp_growth_pct` | estimated resident population and its year-on-year change, 2001-2025 |
| `income_earners`, `income_median`, `income_mean`, `income_sum`, `income_earners_median_age` | personal income from tax data, 2016-17 to 2022-23 |
| `income_release` | which ABS release the income row came from |

- Income is three ABS releases stacked, each covering five years; the newest
  figure is kept where they overlap. Releases before 2020-21 use 2016
  boundaries, so the series stops at 2016-17.
- ABS revised the series in the 2021-22 release. The 2020-21 release differs
  by about 8% on earners and 4% on medians for the same years, so its one extra
  year (2016-17) is rescaled per SA2 by the median ratio over the four
  overlapping years. Those rows are labelled `2020-21 (rescaled)`.
- Income values ABS does not publish (small SA2s) are left empty.
- 2022-23 is also joined to every listing as the `tax_*_2022_23` columns.

## Outputs

`data/processed/`
- `vic_property_master.csv`: main listing-level table
- `sa2_master.csv`: SA2-level table
- `sa2_yearly.csv`: one row per SA2 and year, for the forecasting model
- `suburb_postcode_sa2.csv`: dwelling shares linking suburbs, postcodes and SA2s
- `vic_property_rent_ratios.csv`: rent-derived columns, not for use as predictors
- `source_coverage.csv`: source-by-source join/status audit

Temporary downloads are cached under `data/processed/_cache/`.


## Overpass / OSM reliability

OpenStreetMap amenity enrichment uses public Overpass servers. Each amenity
category is queried separately across Victoria and cached as
`_cache/osm_<category>.json`, so a failed category is retried on the next run
without refetching the others. A single query for every category at once is
heavy enough that the servers reject it, and `overpass-api.de` answers
`Accept: application/json` with 406, so the request sends `*/*`.

Each mirror is tried twice before a category is skipped; the rest of the master
dataset is still produced. GPs use `amenity=doctors`; the secondary
`healthcare=doctor` tag times out statewide.

It adds `nearest_<category>_km` per listing and `osm_<category>_count` per SA2.

For a deliberately fast run without OSM:

```bash
python sprint_2/run/run.py --no-osm
```


## Train station access

Train access uses the Victorian Department of Transport and Planning
**Public Transport Stops** statewide GeoJSON, filtered to `METRO TRAIN` and
`REGIONAL TRAIN`.

Only about a third of those records are stations. The rest are station
furniture (Park & Ride, Lift, Decision point, Taxi Zone…) and rail replacement
bus stops, which would otherwise count as "stations" in `train_station_count`.
The pipeline keeps records named `X Station` or `X Railway Station` and
collapses each station's platform and entrance records (Southern Cross has 36)
to one point at their mean.

It adds:
- `nearest_train_station_km` for each rental listing (straight-line distance)
- `train_station_count` for the listing's SA2 (number of stations)

The older Metro Train Stations accessibility dataset is only used as a fallback.

## Tram and bus stops

The same Public Transport Stops download gives tram stops (`METRO TRAM`) and bus
stops (`METRO BUS` and `REGIONAL BUS`). Regional coaches and SkyBus are left
out, since they run a few services a day or only to the airport.

Each direction of a stop is its own record, one on each side of the road.
Records are merged into one stop when they have the same name and are within
250 m of each other. Name alone is not enough: bus stop names repeat across the
state, and "Station St/High St" is used for stops 180 km apart.

It adds:
- `nearest_tram_stop_km` and `nearest_bus_stop_km` for each rental listing (straight line)
- `tram_stop_count` and `bus_stop_count` for the listing's SA2

The feed has no timetables, so these columns measure how close a stop is, not
how good the service is. Service frequency would need the PTV GTFS timetable.

## Driving routes (OpenRouteService)

The brief asks for proximity to the CBD and the nearest train station
"calculated via routes (as travelled by car)". These come from the
OpenRouteService `driving-car` matrix API. It needs a free key from
[account.heigit.org](https://account.heigit.org/manage/key), read from the
`ORS_API_KEY` environment variable:

```bash
export ORS_API_KEY=your-key-here
python sprint_2/run/run.py
```

Without a key the routes are skipped (recorded as `skipped` in
`source_coverage.csv`) and only the straight-line `cbd_km` is added.

| Column | Meaning |
| --- | --- |
| `cbd_km` | straight-line distance to Flinders Street Station; needs no key |
| `cbd_drive_km`, `cbd_drive_min` | driving distance and time to Flinders Street Station |
| `nearest_train_station_drive_km`, `nearest_train_station_drive_min` | driving distance and time to the road-nearest train station |

How it works:

- **Listings are routed once per location.** Coordinates are rounded to 5 dp
  (about 1 m), so listings in the same building share one route.
- **The road-nearest station is found among the 3 nearest by straight line.**
  A river or freeway can make the closest station by distance a longer drive
  than the second closest. Change the number with `--ors-station-candidates`.
  Distance and time are each the minimum over the candidates, so on rare
  occasions they come from different stations.
- **Requests are packed to the 3,500-pair limit.** Neighbouring listings share
  candidate stations, so a whole Victoria run is about 60 matrix requests,
  well inside the free plan's daily quota. Requests are spaced to stay under
  60 a minute, and a `429` waits a minute and retries.
- **Everything is cached** in `_cache/ors_driving_matrix.json`, saved after
  each request. A rerun makes no requests, and a run stopped by a quota or
  network error keeps what it got (status `partial`) and fetches the rest next
  time.
- **Unroutable points get nulls.** If ORS rejects a request because one
  location cannot be placed on the road network, the batch is halved until the
  offending location is isolated, and only that one is left null.

`--no-ors` skips routing even when a key is set.

## Land size (Vicmap Property)

Domain's `land_area` field is almost always empty, and the listing
pages can no longer be re-scraped: they are a year old and Domain answers
automated requests with 403. Land size is taken instead from **Vicmap Property**,
the state's open property map, which has a polygon for every rateable property
in Victoria. Each listing's coordinates are matched to the polygon they fall in,
and the polygon's area is measured in VicGrid (EPSG:7899). Where Domain did give
a land size, the two agree: 533 m² against 530 m², and 185 m² against 189 m².

| Column | Meaning |
| --- | --- |
| `land_m2` | area of the lot the listing sits on; empty on a shared lot |
| `land_lot_shared` | 1 when the lot is shared with other dwellings (strata apartments and townhouses), 0 when the dwelling has its own lot |

How it works:

- **The listing's lot is the smallest polygon around it.** A subdivided lot sits
  inside the larger parent parcel it was cut from, and a point inside it
  intersects both.
- **Strata lots are detected, not guessed from `property_type`.** Vicmap stores
  a strata building as one identical polygon per unit, each the size of the whole
  building's lot (190 of them at one Chapel Street address). Two or more
  containing polygons within 1% of the smallest mark the lot as shared. A unit
  has no land of its own, so `land_m2` is left empty rather than given the
  building's lot area.
- **Listings are looked up once per location**, 25 to a request, with
  coordinates rounded to 5 dp. Results are cached in
  `_cache/vicmap_property_land.json` after every request, so a rerun makes no
  requests and a failed batch is retried on the next run. The first run takes
  about 30 minutes. `--no-land` skips it.

Things worth knowing:

- **A granny flat or studio on a house block gets the whole block.** It shares
  the lot with the main house but is not strata-titled, so nothing marks the lot
  as shared.
- **Apartments can have land.** Units in a small block that has been subdivided
  into separate titles each have their own lot, and they are kept with their
  own land size.
- **A development that has not been strata-subdivided yet** shows up as one
  unshared lot, so an off-the-plan apartment can get the whole site's area.
- **Listings geocoded onto a road or reserve** fall outside every property
  polygon and get nulls in both columns.
- **Floor area is not included.** No open Victorian dataset records it.
  Building footprints (Microsoft or Overture) would give ground-floor area only,
  and would miss upper storeys.
