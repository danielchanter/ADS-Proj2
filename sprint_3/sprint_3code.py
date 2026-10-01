import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score


# ============================================================
# 1. LOAD DATA
# ============================================================

df = pd.read_csv("vic_property_master.csv")

print("Shape:", df.shape)

target = "weekly_rent"

df[target] = pd.to_numeric(
    df[target],
    errors="coerce"
)

# Remove missing rent
df = df.dropna(subset=[target])

# Remove unrealistic rent values
df = df[
    (df[target] >= 100) &
    (df[target] <= 5000)
]

print("Rows after cleaning:", len(df))


# ============================================================
# 2. SELECT NUMERIC FEATURES
# ============================================================

numeric_cols = df.select_dtypes(
    include=np.number
).columns.tolist()

features = [
    col for col in numeric_cols
    if col != target
]


# ============================================================
# 3. REMOVE UNSUITABLE FEATURES
# ============================================================

# Variables that either contain information derived from rent,
# are identifiers, or are too directly related to rent
exclude_features = [
    "rent_per_bedroom",
    "rent_vs_2021_census_median",
    "rent_to_area_median_hh_income_pct",
    "listing_id",
    "bond"
]

features = [
    col for col in features
    if col not in exclude_features
]


# ============================================================
# 4. REMOVE DUPLICATE SEIFA REPRESENTATIONS
# ============================================================

# Keep the actual scores rather than score + percentile + decile

duplicate_features = [
    "irsad_aus_percentile",
    "irsad_aus_decile",
    "ieo_aus_percentile",
    "ieo_aus_decile",
    "irsd_aus_percentile",
    "irsd_aus_decile",
    "ier_aus_percentile",
    "ier_aus_decile"
]

features = [
    col for col in features
    if col not in duplicate_features
]


# ============================================================
# 5. KEEP ONLY MOST RECENT PERSONAL INCOME
# ============================================================

most_recent_income = (
    "tax_mean_total_personal_income_2022_23"
)

income_cols = [
    col for col in features
    if "tax_mean_total_personal_income" in col
]

features = [
    col for col in features
    if col not in income_cols
    or col == most_recent_income
]

print("Number of features:", len(features))
print(
    "Personal income feature kept:",
    most_recent_income
)


# ============================================================
# 6. READABLE FEATURE NAMES
# ============================================================

pretty_names = {

    # Property
    "bedrooms":
        "Bedrooms",

    "bathrooms":
        "Bathrooms",

    "carspaces":
        "Car spaces",

    "days_listed":
        "Days listed",


    # Housing market
    "census_median_rent_weekly":
        "Area median rent",

    "census_median_mortgage_monthly":
        "Area median mortgage",


    # Income
    "tax_mean_total_personal_income_2022_23":
        "Mean personal income",

    "census_median_personal_income_weekly":
        "Median personal income",


    # Socioeconomic
    "irsad_score":
        "Socioeconomic advantage",

    "irsd_score":
        "Socioeconomic disadvantage",

    "ieo_score":
        "Education & occupation",

    "ier_score":
        "Economic resources",


    # Demographics
    "census_avg_household_size":
        "Average household size",

    "census_median_age":
        "Median age",

    "population_density_2025":
        "Population density",

    "born_overseas_pct":
        "Born overseas (%)",

    "age_0_14_pct":
        "Population aged 0–14 (%)",


    # Accessibility
    "nearest_park_km":
        "Distance to park",

    "nearest_school_km":
        "Distance to school",

    "nearest_gp_clinic_km":
        "Distance to GP clinic",

    "nearest_train_station_km":
        "Distance to train station",

    "nearest_supermarket_km":
        "Distance to supermarket",


    # Amenities
    "osm_cafe_count":
        "Nearby cafes",


    # Location
    "lat":
        "Latitude",

    "lon":
        "Longitude"
}


# ============================================================
# 7. CREATE X AND y
# ============================================================

X = df[features].copy()
y = df[target]

X = X.replace(
    [np.inf, -np.inf],
    np.nan
)

X = X.fillna(
    X.median()
)


# ============================================================
# 8. TRAIN / TEST SPLIT
# ============================================================

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)


# ============================================================
# EXTRA TREES
# ============================================================

model = ExtraTreesRegressor(
    n_estimators=300,
    random_state=42,
    n_jobs=-1
)

model.fit(
    X_train,
    y_train
)

predictions = model.predict(
    X_test
)

mae = mean_absolute_error(
    y_test,
    predictions
)

r2 = r2_score(
    y_test,
    predictions
)

print("\nExtra Trees Performance")
print("-----------------------")
print(f"MAE: ${mae:.2f}")
print(f"R²: {r2:.3f}")


# ============================================================
# EXTRA TREES FEATURE IMPORTANCE
# ============================================================

importance = pd.DataFrame({
    "feature": X.columns,
    "importance": model.feature_importances_
})

importance = importance.sort_values(
    "importance",
    ascending=False
).reset_index(drop=True)


print("\nTop 10 Extra Trees Features")
print("---------------------------")

print(
    importance.head(10)
)


# ============================================================
# READABLE EXTRA TREES GRAPH
# ============================================================

top10 = importance.head(10).copy()

top10["label"] = (
    top10["feature"]
    .map(pretty_names)
    .fillna(top10["feature"])
)

top10 = top10.sort_values(
    "importance",
    ascending=True
)

plt.figure(figsize=(10, 6))

plt.barh(
    top10["label"],
    top10["importance"]
)

plt.xlabel("Feature Importance")
plt.ylabel("")

plt.title(
    "Most Important Features for Predicting Weekly Rent"
)

plt.tight_layout()
plt.show()


# ============================================================
# RIDGE REGRESSION
# ============================================================

alphas = np.logspace(
    -3,
    3,
    100
)

ridge = Pipeline([
    (
        "scaler",
        StandardScaler()
    ),
    (
        "ridge",
        RidgeCV(
            alphas=alphas
        )
    )
])

ridge.fit(
    X_train,
    y_train
)

ridge_predictions = ridge.predict(
    X_test
)

ridge_mae = mean_absolute_error(
    y_test,
    ridge_predictions
)

ridge_r2 = r2_score(
    y_test,
    ridge_predictions
)

print("\nRidge Regression Performance")
print("----------------------------")
print(f"MAE: ${ridge_mae:.2f}")
print(f"R²: {ridge_r2:.3f}")

print(
    "Best alpha:",
    ridge.named_steps["ridge"].alpha_
)


# ============================================================
# RIDGE COEFFICIENTS
# ============================================================

ridge_coefficients = pd.DataFrame({
    "feature":
        X.columns,

    "coefficient":
        ridge.named_steps["ridge"].coef_
})

ridge_coefficients["importance"] = (
    ridge_coefficients[
        "coefficient"
    ].abs()
)

ridge_coefficients = (
    ridge_coefficients
    .sort_values(
        "importance",
        ascending=False
    )
    .reset_index(drop=True)
)


print("\nTop 10 Ridge Features")
print("---------------------")

print(
    ridge_coefficients[
        [
            "feature",
            "coefficient",
            "importance"
        ]
    ].head(10)
)


# ============================================================
# READABLE RIDGE GRAPH
# ============================================================

ridge_top10 = (
    ridge_coefficients
    .head(10)
    .copy()
)

ridge_top10["label"] = (
    ridge_top10["feature"]
    .map(pretty_names)
    .fillna(
        ridge_top10["feature"]
    )
)

ridge_top10 = ridge_top10.sort_values(
    "importance",
    ascending=True
)

plt.figure(figsize=(10, 6))

plt.barh(
    ridge_top10["label"],
    ridge_top10["importance"]
)

plt.xlabel(
    "Absolute Standardised Ridge Coefficient"
)

plt.ylabel("")

plt.title(
    "Most Important Features for Predicting Weekly Rent - Ridge"
)

plt.tight_layout()
plt.show()

# ============================================================
# RIDGE GRAPH WITH DIRECTION
# ============================================================

ridge_top10 = (
    ridge_coefficients
    .head(10)
    .copy()
)

ridge_top10["label"] = (
    ridge_top10["feature"]
    .map(pretty_names)
    .fillna(ridge_top10["feature"])
)

# Sort by actual coefficient
ridge_top10 = ridge_top10.sort_values(
    "coefficient"
)

plt.figure(figsize=(10, 6))

plt.barh(
    ridge_top10["label"],
    ridge_top10["coefficient"]
)

# Zero line
plt.axvline(
    x=0,
    linewidth=1
)

plt.xlabel(
    "Standardised Ridge Coefficient"
)

plt.ylabel("")

plt.title(
    "Relationship Between Key Features and Weekly Rent"
)

plt.tight_layout()
plt.show()