import pandas as pd
from pathlib import Path
from sklearn.model_selection import train_test_split

LEAKAGE_COLS = [
    "bond", "rent_per_bedroom", 
    "rent_vs_2021_census_median", 
    "rent_to_area_median_hh_income_pct",
    "rent_outlier_high",  
]

base = Path(__file__).resolve().parent.parent

propertydf = pd.read_csv(base / "preprocessing" / "vic_property_clean.csv")

# features that do not have any value 
obvremove = ["address", "address_key", "lat", "lon", "primary_type", "secondary_type", "structured_features", "url", "irsad_score", "irsd_score", "ier_score", "ieo_score", "ieo_aus_decile", "irsd_aus_decile", "irsad_aus_decile", "ieo_aus_decile", "ier_aus_decile"]

# LEAKAGE_COLS are features that are not usuable for rent prediction obviously
# address, lat, lon not useful for our granularity (suburb)
# structured_features nuked idk what do with them
# primary_type, secondary_type most entries are exactly the same as property_type, very redundant information
# url - lol
# test scores and deciles, already have percentiles so reduce redundant info 

finalproperty = propertydf.drop(columns=LEAKAGE_COLS)
finalproperty = finalproperty.drop(columns=obvremove)

# feature engineering 
# convert dates to datetime
finalproperty["date_listed"] = pd.to_datetime(finalproperty["date_listed"], errors="coerce")
finalproperty["available_date"] = pd.to_datetime(finalproperty["available_date"], errors="coerce")

# extract months
finalproperty["listed_month"] = finalproperty["date_listed"].dt.month
finalproperty["available_month"] = finalproperty["available_date"].dt.month

# remove the date columns as they are no longer needed
finalproperty.drop(columns=["date_listed", "available_date"], inplace=True)


# data cleaning
bad_listings =[17727626, 17721987, 17722092, 17727546]
# first one literally missing everything other than house features
# next three no osm/sa2 data 

finalproperty = finalproperty[
    ~finalproperty["listing_id"].isin(bad_listings)
].copy()

# there are 3 listings left with only missing date values, so fill them with -1 to indicate missing
finalproperty["listed_month"] = finalproperty["listed_month"].fillna(-1).astype(int)
finalproperty["available_month"] = finalproperty["available_month"].fillna(-1).astype(int)
finalproperty["days_listed"] = finalproperty["days_listed"].fillna(-1)


# feature selection (again?)
name_cols = ["sa2_name_2021", "sa3_name_2021", "sa4_name_2021", "gccsa_name_2021"]
id_cols = ["listing_id", "sa2_code_2021", "sa3_code_2021", "sa4_code_2021", "gccsa_code_2021"]
cat_cols = ["suburb", "postcode", "property_type", "listed_month", "available_month"]

# detecting highly correlated features (correlation > 0.95) 
target = "weekly_rent"
temp = finalproperty.drop(columns=[target])
temp = temp.drop(columns=id_cols)

num_cols = [
    col for col in temp.columns
    if col not in name_cols + cat_cols
]

# calculate correlation 
# used spearman correlation to detect monotonic relationships 
corr = temp[num_cols].corr(method="spearman") 
high_corr = []
for i in range(len(corr.columns)):
    for j in range(i):
        value = corr.iloc[i, j]

        if abs(value) >= 0.95:
            high_corr.append(
                (
                    corr.columns[i],
                    corr.columns[j],
                    value
                )
            )


for feature1, feature2, correlation in high_corr:
    print(f"{feature1} <-> {feature2}: {correlation:.3f}")

# removing selected features based on correlation 
removep2 = [
    # literally r = 1 and r = 0.995
    "vif_population_2021",
    "erp_2024",

    # all the tax data is highly correlated (obviously) such keep the latest year get rid of the rest
    "tax_income_earners_2018_19",
    "tax_income_earners_2019_20",
    "tax_income_earners_2020_21",
    "tax_income_earners_2021_22",

    "tax_median_total_personal_income_2018_19",
    "tax_median_total_personal_income_2019_20",
    "tax_median_total_personal_income_2020_21",
    "tax_median_total_personal_income_2021_22",

    "tax_mean_total_personal_income_2018_19",
    "tax_mean_total_personal_income_2019_20",
    "tax_mean_total_personal_income_2020_21",
    "tax_mean_total_personal_income_2021_22"
]

finalproperty = finalproperty.drop(columns=removep2)

output_path = base / "modelling" / "finalproperty.csv"

finalproperty.to_csv(output_path, index=False)
