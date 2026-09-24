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
| `sqm_week`, `sqm_week_lag_days` | the index week matched, and how far before the listing date it sits |
| `sqm_houses_all`, `sqm_houses_3`, `sqm_units_all`, `sqm_units_2`, `sqm_combined` | the five raw series at that week |
| `sqm_*_yoy_pct` | year-on-year change of each series, matched 52 weeks back by date |
| `sqm_market_series`, `sqm_market_rent` | which series the listing is compared against (house index for houses, unit index for units, combined if ambiguous) and its value |
| `sqm_market_rent_yoy_pct` | market momentum in that postcode at the listing date |
| `sqm_market_rent_bedroom_matched` | tighter comparable, non-null only for 3-bed houses and 2-bed units |
| `rent_vs_sqm_market` | `weekly_rent / sqm_market_rent` — the listing priced against its market |

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
| `crime_suburb_incidents`, `crime_suburb_population` | the suburb's incidents that period and its Census 2021 population |
| `crime_suburb_rate_per_1k` | incidents per 1,000 residents — the headline suburb measure |
| `crime_suburb_{person,property,drug,public_order,justice,other}_rate_per_1k` | the same rate split by offence division |
| `crime_suburb_rate_yoy_pct`, `crime_suburb_rate_3yr_change_pct` | whether the suburb is getting better or worse |
| `crime_sal_ambiguous` | 1 where a repeated suburb name could not be resolved by LGA |

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
- **Coverage is 99.7%, and the misses are old listings.** The workbook starts at
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
future unresolved name is matched to the more populous suburb and flagged in
`crime_sal_ambiguous`.

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

Additional registered/discovered sources:
- Victorian Property Sales Report
- DFFH Rental Report
- School Zones
- PTV timetable data
- Vicmap Features of Interest
- Digital Atlas of Australia
- ABS Building Approvals
- ABS CPI / Melbourne rents
- ASGS 2026 correspondence

Sources with coarse or differently-defined geographies are left unmerged. They
are recorded in `source_coverage.csv` and
`extra_source_manifest.csv` so the modelling team can distinguish:
1. features actually joined to each listing,
2. forecast/context sources, and
3. sources requiring a defined period/geographic correspondence.

This prevents suburb/LGA values from being incorrectly presented as exact SA2
measurements.

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
