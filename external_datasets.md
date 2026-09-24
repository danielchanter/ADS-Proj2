## All Questions

### Geographic Zones

- [SA2 District Boundaries for July 2021 - June 2026](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files) List of SA2s

- [SA2 Correspondence files for July 2021 - June 2026](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/allocation-files) Lookup table mapping suburbs (SAL), postcodes (POA) and LGAs onto SA2

- [Digital Atlas of Australia](https://digital.atlas.gov.au/pages/about) REST API for various attributes mapped to different regions (SA2, suburbs, etc.) (need to do more research on which attributes to consider)

### Routing

- [OpenRouteService](https://account.heigit.org/manage/key)

### Nice to have's

- [Victorian Property Sales Report](https://discover.data.vic.gov.au/dataset/victorian-property-sales-report-median-house-by-suburb-time-series) Median house by suburb

- [Crime Statistics](https://www.crimestatistics.vic.gov.au/crime-statistics/latest-victorian-crime-data/download-data) **Joined.** The *LGA Criminal Incidents* workbook, rolling years ending March, 2017 → 2026. One sheet is used: Table 03 (incidents by **postcode, suburb and offence division**). The download URL carries a release folder and a revision suffix that change every quarter, so `sprint_2/run/build_master_dataset.py` scrapes the current link off the download page rather than hard-coding it, then joins it onto each listing as a backward as-of match on `(suburb, date_listed)` — a listing is only ever described by a reporting period that had already closed when it was advertised. Produces the `crime_*` columns; 12,712/12,717 listings match an ABS suburb.
  - Why a denominator was needed: CSA publishes suburb-level **counts** with no population, which makes Melbourne CBD and a small country town incomparable. The pipeline divides by Census 2021 suburb (SAL) population from ABS GCP layer 5 to get `crime_suburb_rate_per_1k`. Where the suburb name is used twice in Victoria (15 cases — Hillside, Newtown, Ascot…) the right SAL is picked using the LGA the incidents sit in, which ABS encodes in the SAL name.
  - Caveat worth carrying into the modelling: incidents are counted where they *happen*, residents are counted where they *sleep*, so industrial and entertainment areas score very high. Dandenong South is 5,560 per 1,000 on 125 residents. No population cutoff is applied; `crime_suburb_population` is kept so modelling can log-transform, filter or weight. The raw rate correlates with rent at only −0.12 because Victoria's highest-crime suburbs are also its activity centres, so prefer the per-division rates for cross-sectional work.

### Rent time series (fills the 2025 → 2026 gap)

The supplied Domain data is a single snapshot (scraped 2025-09-09; 93% of listings are 2025Q3), so it has no usable time series. This is the source that bridges it to the present:

- [SQM Research Weekly Rents Index](https://sqmresearch.com.au/weekly-rents.php?postcode=3168&t=1) Weekly advertised rents **per postcode**, 2009-08 → current week (verified to 2026-09-15). Five series per postcode: `houses_all`, `houses_3`, `units_all`, `units_2`, `combined`. Fetched by `sprint_2/run/fetch_sqm_rents.py` — the series is embedded as JSON in the page HTML, so no API key is needed. Joined onto each listing by `sprint_2/run/build_master_dataset.py` as an as-of match on `(postcode, date_listed)`, producing the `sqm_*` and `rent_vs_sqm_market` columns. Verified 20/20 coverage on a metro+regional sample, including thin rural postcodes.
  - Why it works here: it **overlaps** the Domain snapshot, so it can be anchored to it rather than spliced blind. At the 2025-09-08 week, SQM medians sit within 0–2% of the Domain medians in postcodes with decent sample sizes (3000, 3029 houses, 3350 houses, 3840). A few postcodes disagree by more. Clayton (3168) is the clearest: SQM reads about 13% higher. That is because Domain's Clayton listings include single rooms rented out in student share-houses, which are cheap and pull Domain's median down, while SQM counts whole properties only. Neither number is wrong — they are counting different things.
  - Licensing: free for personal/reference use, cite SQM Research.

## Question 1 and 3

### Demographics

- [ABS Census Data Packs](https://www.abs.gov.au/census/find-census-data/datapacks) G01 (persons, age, birthplace), G02 (median weekly rent and median household income), G33 (household income bands), G37 (tenure and landlord type), G40 (dwelling structure), G43 (labour force)

- [ABS Regional Population by SA2](https://www.abs.gov.au/statistics/people/population/regional-population/latest-release) Estimated resident population and population density by SA2 + components of change (births, deaths, internal and overseas migration). **Joined.** Yearly SA2 population 2001-2025 (data cube 32180DS0003) goes to `sa2_yearly.csv` for forecasting.

- [ABS Personal Income in Australia](https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/personal-income-australia/latest-release) Median and mean employee income, total income and investment income per SA2, taxation based. **Joined.** SA2 total income 2016-17 to 2022-23, from the 2020-21, 2021-22 and 2022-23 releases stacked (earlier releases use 2016 boundaries), goes to `sa2_yearly.csv`; 2022-23 is also joined to listings.

- [Socio-Economic Indexes for Areas (SEIFA)](https://www.abs.gov.au/statistics/people/people-and-communities/socio-economic-indexes-areas-seifa-australia/latest-release) Four indexes (IRSAD, IRSD, IER, IEO) with scores, deciles and percentiles per SA2

### Amenities

- [School Locations](https://discover.data.vic.gov.au/dataset?q=school+locations) 2019, 2020, 2021, 2023, 2024, 2025 (I have no idea where 2022 is)

- [School Zones](https://discover.data.vic.gov.au/dataset/?sort=score+desc%2C+metadata_modified+desc&q=school+zones&organization=&groups=&res_format=) 2022, 2023, 2024, 2025, 2026, 2027

- [OpenStreetMap (downloadable)](https://download.geofabrik.de/australia-oceania/australia/victoria.html)[OpenStreetMap (API)](https://overpass-turbo.eu) Parks, supermarkets, shopping centres, cafés, restaurants, bars, gyms, childcare, hospitals, GPs, libraries

- [Vicmap as a Service](https://discover.data.vic.gov.au/dataset/vicmap-as-a-service-vaas) REST API for Features of Interest and Geomarks. There are a lot more but too lazy to check if the rest are relevant (someone please do)

### Transport

- [PTV Timetable API](https://discover.data.vic.gov.au/dataset/ptv-timetable-api) Timetable for metropolitan and regional train, tram, and bus

- [Metro Train Stations with Accessibility Information](https://discover.data.vic.gov.au/dataset/metro-train-stations-with-accessibility-information) Locations of train stations and their accessibility information


## Question 2 

### Population forecasts

- [Victoria in Future — Statistical areas (SA2, SA3, SA4, GCCSA)](https://www.planning.vic.gov.au/guides-and-resources/Data-spatial-and-insights/discover-and-access-planning-open-data/victoria-in-future/download-data/statistical-areas-sa2,-sa3,-sa4,-gccsa) Population projections by age, sex, household and dwelling counts in five year increments by statistical areas

- [VIF2023 — Victoria demographic projections to 2051](https://discover.data.vic.gov.au/dataset/vif2023-victoria-demographic-projections-to-2051) Same as above but for VIC as a whole

- [ABS Population Projection, Australia](https://www.abs.gov.au/statistics/people/population/population-projections-australia/latest-release)

### Nice to have's

- [ABS Building Approvals](https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia/latest-release) Dwelling approvals by LGA

- [ABS CPI](https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia/latest-release) Quarterly rents index for Melbourne

## Question 2 and 3

- [SA2 District Boundaries for July 2026 - June 2031 + hierarchy of statistical areas](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/latest-release) Maps suburbs (SAL), postcodes (POA) and LGAs onto SA2

