## All Questions

### Geographic Zones

- [SA2 District Boundaries for July 2021 - June 2026](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files) List of SA2s

- [SA2 Correspondence files for July 2021 - June 2026](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/allocation-files) Lookup table mapping suburbs (SAL), postcodes (POA) and LGAs onto SA2

- [Digital Atlas of Australia](https://digital.atlas.gov.au/pages/about) REST API for various attributes mapped to different regions (SA2, suburbs, etc.) (need to do more research on which attributes to consider)

### Routing

- [OpenRouteService](https://account.heigit.org/manage/key)

### Nice to have's

- [Victorian Property Sales Report](https://discover.data.vic.gov.au/dataset/victorian-property-sales-report-median-house-by-suburb-time-series) Median house by suburb

- [Crime Statistics](https://www.crimestatistics.vic.gov.au/crime-statistics/latest-victorian-crime-data/download-data) At the LGA and postcode level but the Crime by Location tool also exposes suburb

- [Rental report](https://www.dffh.vic.gov.au/publications/rental-report) Median weekly rent by suburb and by dwelling type. This one is drawn from rents actually signed rather than advertised prices (which the ABS dataset is), so might be worth validating one with the other

## Question 1 and 3

### Demographics

- [ABS Census Data Packs](https://www.abs.gov.au/census/find-census-data/datapacks) G01 (persons, age, birthplace), G02 (median weekly rent and median household income), G33 (household income bands), G37 (tenure and landlord type), G40 (dwelling structure), G43 (labour force)

- [ABS Regional Population by SA2](https://www.abs.gov.au/statistics/people/population/regional-population/latest-release) Estimated resident population and population density by SA2 + components of change (births, deaths, internal and overseas migration)

- [ABS Personal Income in Australia](https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/personal-income-australia/latest-release) Median and mean employee income, total income and investment income per SA2, taxation based

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

- [Victoria in Future — Statistical areas (SA2, SA3, SA4, GCCSA)](https://www.planning.vic.gov.au/guides-and-resources/Data-spatial-and-insights/discover-and-access-planning-open-data/victoria-in-future/download-data/statistical-areas-sa2,-sa3,-sa4,-gccsa) Population projections by age,sex, household and dwelling counts in five year increments by statistical areas

- [VIF2023 — Victoria demographic projections to 2051](https://discover.data.vic.gov.au/dataset/vif2023-victoria-demographic-projections-to-2051) Same as above but for VIC as a whole

- [ABS Population Projection, Australia](https://www.abs.gov.au/statistics/people/population/population-projections-australia/latest-release)

### Nice to have's

- [ABS Building Approvals](https://www.abs.gov.au/statistics/industry/building-and-construction/building-approvals-australia/latest-release) Dwelling approvals by LGA

- [ABS CPI](https://www.abs.gov.au/statistics/economy/price-indexes-and-inflation/consumer-price-index-australia/latest-release) Quarterly rents index for Melbourne

## Question 2 and 3

- [SA2 District Boundaries for July 2026 - June 2031 + hierarchy of statistical areas](https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/latest-release) Maps suburbs (SAL), postcodes (POA) and LGAs onto SA2

