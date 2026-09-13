# Domain.com.au Victoria Rental Data

A comprehensive dataset of rental property listings from Domain.com.au covering all Victorian suburbs, collected in September 2025 for MAST30034 coursework at the University of Melbourne.

## Important Notice

**⚠️ IMPORTANT**: This dataset is strictly for MAST30034 University of Melbourne teaching purposes only.

## Data Usage Restrictions

### DO NOT REDISTRIBUTE THE DATA

- The data are exclusively for MAST30034 coursework
- Data must NOT be shared, sold, or redistributed outside of the course
- Students must comply with University of Melbourne academic integrity policies
- Any analysis or findings using this data should acknowledge the source

## Data Structure

```
Data/
├── suburbs/                     # Individual suburb CSV files
│   ├── Ashburton_3147.csv
│   ├── Box_Hill_3128.csv
│   └── ...
├── vic_rentals_all.csv          # Merged data from all suburbs
├── postcodes.csv                # All Victorian suburbs and postcodes
└── suburb_summary.csv           # Statistical summary by suburb
```

## Dataset Overview

- **Coverage**: All Victorian suburbs
- **Collection Date**: September 2025
- **Scope**: Up to 15 pages of rental listings per suburb
- **Total Records**: Varies by market conditions at time of collection
- **Format**: CSV

## Data Fields

### Basic Property Information

| Field | Description | Example |
|-------|-------------|---------|
| `listing_id` | Unique Domain listing identifier | "8750241" |
| `suburb` | Property suburb | "ASHBURTON" |
| `postcode` | Property postcode | "3147" |
| `address` | Street address | "127/386 Burwood Highway" |
| `property_type` | Type of property | "Apartment / Unit / Flat" |
| `url` | Full Domain.com.au listing URL | "https://www.domain.com.au/..." |

### Rental Details

| Field | Description | Example |
|-------|-------------|---------|
| `weekly_rent` | Weekly rental price in AUD | 335 |
| `bond` | Bond amount required in AUD | 1456 |
| `available_date` | When property becomes available | "Friday, 24 October 2025" |
| `date_listed` | When property was listed | "2025-01-15" |
| `days_listed` | Number of days on market | 25 |

### Property Features

| Field | Description | Example |
|-------|-------------|---------|
| `bedrooms` | Number of bedrooms | 2 |
| `bathrooms` | Number of bathrooms | 1 |
| `carspaces` | Number of parking spaces | 1 |
| `land_area` | Land size (if applicable) | "650 m²" |
| `structured_features` | Additional property features | "airConditioning, balcony, pool" |

### Location Data

| Field | Description | Example |
|-------|-------------|---------|
| `lat` | Latitude coordinate | -37.852066 |
| `lon` | Longitude coordinate | 145.13045 |

### Media & Virtual Tour Information

| Field | Description | Example |
|-------|-------------|---------|
| `photo_count` | Number of property photos | 12 |
| `video_count` | Number of property videos | 1 |
| `floorplans_count` | Number of floorplan images | 2 |
| `virtual_tour` | Virtual tour availability | true/false |

### Agency Information

| Field | Description | Example |
|-------|-------------|---------|
| `agency` | Real estate agency name | "Ray White Box Hill" |
| `agency_id` | Domain agency identifier | "12345" |
| `agent_names` | Listing agent names | "John Smith, Jane Doe" |

### Property Classification

| Field | Description | Example |
|-------|-------------|---------|
| `primary_type` | Primary property classification | "Residential" |
| `secondary_type` | Secondary property classification | "Unit" |

### System Fields

| Field | Description | Example |
|-------|-------------|---------|
| `domain_page_id` | Domain internal page ID | "R-12345678" |
| `property_id` | Domain property identifier | "PB-1234567" |
| `scraped_date` | When data was collected | "2025-09-09 14:38:22" |

## Summary Statistics File

The `suburb_summary.csv` file contains aggregated statistics for each suburb:

| Field | Description |
|-------|-------------|
| `suburb` | Suburb name |
| `postcode` | Suburb postcode |
| `listings_count` | Total number of listings found |

## Data Quality Notes

- **Coverage Gaps**: Not all suburbs in `postcodes.csv` have rental listings. Rural, commercial, or low-density areas may have no available rentals.
- **Temporal Snapshot**: Data represents market conditions at the time of collection (September 2025).
- **Duplicate Handling**: Listings appearing in multiple suburb searches have been filtered to maintain data integrity.
- **Missing Values**: Some fields may be empty where information was not available on Domain.com.au.

## Usage Guidelines for Students

1. **Load the data**: Start with `vic_rentals_all.csv` for comprehensive analysis or individual suburb files for focused studies
2. **Check data types**: Ensure numeric fields are properly parsed (weekly_rent, bond, bedrooms, etc.)
3. **Handle missing values**: Some properties may lack certain details (e.g., land_area for apartments)
4. **Coordinate usage**: Lat/lon fields enable geographic analysis and mapping
5. **Time analysis**: Use date_listed and days_listed for market dynamics studies

## Academic Integrity

When using this dataset in your coursework:
- Cite the data source appropriately
- Do not share the raw data with others
- Include only aggregated results in public reports
- Follow MAST30034 assignment guidelines

## Contributors

Data collected and prepared by:
- Wenqin Liu
- Wenjie Wang
- Mingming Gong

## Contact

For questions regarding the dataset or its structure, please contact the course coordinator or teaching assistants through the official MAST30034 channels.

## Legal Disclaimer

This dataset was collected for educational purposes in compliance with academic research guidelines. Users must:
- Use the data only for MAST30034 coursework
- Not use the data for commercial purposes
- Not redistribute or share the data outside the course
- Delete the data after course completion
- Respect the intellectual property rights of Domain.com.au and property advertisers

---

*Last Updated: September 2025*