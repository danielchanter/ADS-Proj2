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

## Rent time series

The Domain data is a single snapshot scraped 2025-09-09, so it has no rent time
series. `fetch_sqm_rents.py` pulls the SQM Research Weekly Rents Index for every
postcode in the Domain data — weekly, per postcode, 2009 to the current week —
which is what bridges the snapshot to the present:

```bash
python sprint_2/run/fetch_sqm_rents.py            # all Domain postcodes, ~10 min
python sprint_2/run/fetch_sqm_rents.py --postcodes 3168 3000
```

Writes `data/external/sqm_weekly_rents.csv` (long format) and a per-postcode
coverage audit. Use it as a **growth index** anchored on the Domain snapshot —
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

- **Tolerance matters.** The median gap between index weeks is 7 days in every
  postcode, but 131 of 425 postcodes drop out of the index for more than 60 days
  at least once, and the worst gap is 8 years. The 31-day cap gives thin rural
  postcodes a null during those gaps. 12,666 of 12,717
  listings match (99.6%); the misses are those dropouts plus one postcode (3413)
  SQM does not publish.
- **Zeros mean missing.** SQM writes 0 when a series had no advertised
  stock that week (7% of `units_2`), so the loader reads zeros as null.

## Crime

Crime Statistics Victoria's **LGA Criminal Incidents** workbook is joined onto
every listing. Nothing needs to be run first — the pipeline finds the current
workbook on CSA's download page, caches it under `data/processed/_cache/`, and
uses one of its sheets:

- **Table 03** — incidents by postcode, suburb and offence division. CSA
  publishes raw counts with no denominator, so the pipeline divides by the ABS
  Census 2021 suburb (SAL) population to get a rate.

Flags: `--no-crime` to skip, `--crime-xlsx` to point at a workbook you already
have, `--crime-tolerance-days` (default 370) to change how stale a matched
reporting period may be.

### How it joins

CSA reports rolling years labelled by the month they end in, so "2025, year
ending March" covers 2024-04-01 to 2025-03-31. Each listing gets the most recent
period that had **already closed** when it was advertised — a backward as-of
join on `date_listed`, the same shape as the SQM join. A listing from mid-2024
therefore gets the year ending March 2024, since the year ending March 2025 was
still in progress.

Listings are matched to a suburb on `(postcode, suburb)`, which resolves
12,712 of 12,717 listings. The other 5 (suburb not named by CSA, or no
postcode/listing date) get nulls in every crime column.

Columns added, all prefixed `crime_`:

| Column | Meaning |
| --- | --- |
| `crime_suburb_population` | the suburb's Census 2021 population, the rate's denominator |
| `crime_suburb_rate_per_1k` | incidents per 1,000 residents — the headline suburb measure |
| `crime_suburb_{person,property,drug,public_order,justice,other}_rate_per_1k` | the same rate split by offence division |
| `crime_suburb_rate_yoy_pct`, `crime_suburb_rate_3yr_change_pct` | whether the suburb is getting better or worse |

Four things worth knowing:

- **Incidents are counted where they happen, and the denominator is residents.**
  Mostly industrial or commercial suburbs therefore show very high rates.
  Dandenong South records 695 incidents against 125 residents — 5,560 per 1,000.
  The same effect is real signal in a CBD (Melbourne 348, Geelong 508, Bendigo
  371 per 1,000), so the rate is kept as computed. Handle it at modelling time:
  `np.log1p` the rates for linear models, and use `crime_suburb_population` to
  filter or weight if residuals point at small suburbs.
- **The raw rate is weak on its own cross-sectionally.** Victoria's highest-crime
  suburbs are its activity centres, which also have its highest rents, so
  `crime_suburb_rate_per_1k` correlates with rent at only −0.12. The per-division
  rates separate "busy" from "disadvantaged" better than the total does.
- **Coverage is 99.6%, and the misses are old listings.** The workbook starts at
  the year ending March 2017, so the 35 listings advertised before then get
  nulls. The 370-day tolerance is one reporting
  period plus slack.
- **Suburb counts are summed across postcodes.** CSA splits a few suburbs over
  two postcodes — Beaumaris files 3,070 incidents under 3193 and 24 under 3192.
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
- ABS Census 2021 General Community Profile
- ABS SEIFA 2021
- ABS Regional Population 2025, plus yearly SA2 population 2001-2025
- ABS Personal Income by SA2, 2016-17 to 2022-23 (see "Yearly SA2 series" below)
- Victoria in Future 2023
- Victorian school locations
- Metro station accessibility (best-effort endpoint)
- OpenStreetMap amenities
- SQM Research weekly postcode rent index (as-of join, see above)
- Crime Statistics Victoria, suburb level (as-of join, see above)

Candidate sources not joined yet. Each needs a period or geographic
correspondence defined before it can be joined without presenting suburb/LGA
values as exact SA2 measurements:
- [DFFH Rental Report](https://www.dffh.vic.gov.au/publications/rental-report) —
  signed-rent benchmark; avoid a same-period suburb median as a predictor
- [Victorian Property Sales Report](https://discover.data.vic.gov.au/dataset/victorian-property-sales-report-median-house-by-suburb-time-series)
- [School Zones](https://discover.data.vic.gov.au/dataset/?q=school+zones)
- [PTV Timetable API](https://discover.data.vic.gov.au/dataset/ptv-timetable-api)
- [Vicmap Features of Interest](https://discover.data.vic.gov.au/dataset/vicmap-features-of-interest-rest-api)
- [ABS Building Approvals](https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia/latest-release) —
  SA2 small-area approvals, a supply covariate for forecasting
- [ABS CPI](https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia/latest-release) —
  Melbourne rents, a Melbourne-wide time series, not an SA2 differentiator
- [ASGS 2026](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/latest-release) —
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
grouping patterns are `STRUCTURED_FEATURE_FLAGS` in `build_master_dataset.py`.
The 10% of listings with no feature list get empty flags rather than 0, since a
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

`weekly_rent` itself is not cleaned: it runs from $0 to $808,500, so choose an
outlier rule before modelling.

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
- About 0.3% of income values are not published by ABS (small SA2s) and are
  left empty.
- 2022-23 is also joined to every listing as the `tax_*_2022_23` columns.

## Outputs

`data/processed/`
- `vic_property_master.csv` — main listing-level table
- `sa2_master.csv` — SA2-level table
- `sa2_yearly.csv` — one row per SA2 and year, for the forecasting model
- `vic_property_rent_ratios.csv` — rent-derived columns, not for use as predictors
- `source_coverage.csv` — source-by-source join/status audit

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

Train access now uses the Victorian Department of Transport and Planning
**Public Transport Stops** statewide GeoJSON as the preferred source.

The pipeline filters the stop data to:
- `METRO TRAIN`
- `REGIONAL TRAIN`

It adds:
- `nearest_train_station_km` for each rental listing
- `train_station_count` for the listing's SA2

The older Metro Train Stations accessibility dataset is only used as a fallback.
