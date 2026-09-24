import pandas as pd
import numpy as np
from pathlib import Path
from sklearn.feature_selection import mutual_info_regression


# other

OSM_AMENITIES = {
    "supermarket": [('shop','supermarket')],
    "cafe": [('amenity','cafe')],
    "restaurant": [('amenity','restaurant')],
    "bar_pub": [('amenity','bar'),('amenity','pub')],
    "gym": [('leisure','fitness_centre')],
    "childcare": [('amenity','childcare')],
    "hospital": [('amenity','hospital')],
    "gp_clinic": [('amenity','clinic'),('healthcare','doctor')],
    "library": [('amenity','library')],
    "park": [('leisure','park')],
    "shopping_centre": [('shop','mall')],
}


# loading data 
base = Path(__file__).resolve().parent.parent
dataPath = base / "sprint_2" /  "data" / "processed"

sa2 = pd.read_csv(dataPath / "sa2_master.csv")

# Dealing with Null/NaN/Erroneous values
sa2.drop(columns=["shape", "vif_population_2036", "vif_population_growth_pct_2026_36"], inplace=True)

# NaN values in OSM counts can be imputed with 0 -> = none
for key in OSM_AMENITIES:
    sa2[f"osm_{key}_count"] = sa2[f"osm_{key}_count"].fillna(0)


sa2 = sa2.drop(index=[522, 523]) # these rows are almost all null in every column unusable data removed

sa2 = sa2.drop(index=[33, 38, 157, 160]) # No Age data / Home Language Data removed for now

sa2 = sa2.drop(index=[7, 170, 206, 214, 274]) # No economic test scores, removed for now


# obvious removals
sa2.drop(columns=["state_code_2021", "state_name_2021", # Only looking at Victoria hence state name/code irrelevant
                  "sa3_code_2021", "sa3_name_2021", # as defined by the ABS "SA2 represents a single community or suburb", hence keeping only SA2 as needed by question
                  "sa4_code_2021", "sa4_name_2021", # as above
                  "irsad_score", "ier_score", "irsd_score", "ieo_score", # raw scores aren't that good for comparison
                  "irsd_aus_decile","ier_aus_decile", "irsad_aus_decile" # already have percentile
                  ], inplace=True)


# Correlation
corr = sa2.corrwith(sa2["vif_population_2031"])
print(corr.sort_values(ascending=False))


# Mutual Information
# Target
y = sa2["vif_population_2031"]

# Numeric predictors only
X = sa2.drop(columns=["vif_population_2031"]).select_dtypes(include="number")
X = X.drop(columns=["sa2_code_2021"])

# Calculate mutual information
mi = mutual_info_regression(X, y, random_state=67)
mi_scores = pd.Series(mi, index=X.columns)
mi_scores = mi_scores.sort_values(ascending=False)

print(mi_scores)
# osm_bar_pub_count, census_median_personal_income_weekly, irsad_aus_percentile all have MI score of 0, look to remove
# bar and pub + irsad also low correlation, likely to not be useful

# to do: redundant data / outliers / decide what to do with null values / what is our growth measurement, (income and population pretty clear metrics)