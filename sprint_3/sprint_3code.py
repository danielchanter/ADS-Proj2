import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from sklearn.ensemble import ExtraTreesRegressor
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

df = pd.read_csv("vic_property_master.csv")

print("Shape:", df.shape)
print(df.head())

target = "weekly_rent"

# Make sure rent is numeric
df[target] = pd.to_numeric(df[target], errors="coerce")

# Remove missing rent
df = df.dropna(subset=[target])

# Remove clearly unrealistic rent values
df = df[
    (df[target] >= 100) &
    (df[target] <= 5000)
]

print("Rows after cleaning:", len(df))

numeric_cols = df.select_dtypes(include=np.number).columns.tolist()

# Remove target
features = [col for col in numeric_cols if col != target]

# Remove target leakage
leakage_features = [
    "rent_per_bedroom",
    "rent_vs_2021_census_median",
    "rent_to_area_median_hh_income_pct"
]

features = [
    col for col in features
    if col not in leakage_features
]

# Remove duplicate SEIFA representations
duplicate_features = [
    "irsad_aus_percentile",
    "irsad_aus_decile",
    "ieo_aus_percentile",
    "ieo_aus_decile"
]

features = [
    col for col in features
    if col not in duplicate_features
]

# Keep only most recent personal income
most_recent_income = "tax_mean_total_personal_income_2022_23"

income_cols = [
    col for col in features
    if "mean_total_personal_income" in col
]

features = [
    col for col in features
    if col not in income_cols or col == most_recent_income
]

print("Number of features:", len(features))
print("Personal income feature kept:", most_recent_income)


X = df[features].copy()
y = df[target]

# Replace infinite values
X = X.replace([np.inf, -np.inf], np.nan)

# Fill missing values with median
X = X.fillna(X.median())

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)

model = ExtraTreesRegressor(
    n_estimators=300,
    random_state=42,
    n_jobs=-1
)

model.fit(X_train, y_train)

predictions = model.predict(X_test)

mae = mean_absolute_error(y_test, predictions)
r2 = r2_score(y_test, predictions)

print("\nModel Performance")
print("-----------------")
print(f"MAE: ${mae:.2f}")
print(f"R²: {r2:.3f}")

importance = pd.DataFrame({
    "feature": X.columns,
    "importance": model.feature_importances_
})

importance = importance.sort_values(
    "importance",
    ascending=False
).reset_index(drop=True)

print("\nTop 20 Features")
print("-----------------")
print(importance.head(20))

top20 = importance.head(20).sort_values(
    "importance",
    ascending=True
)

plt.figure(figsize=(10, 8))

plt.barh(
    top20["feature"],
    top20["importance"]
)

plt.xlabel("Feature Importance")
plt.ylabel("Feature")
plt.title("Top 20 Features for Predicting Weekly Rent")

plt.tight_layout()
plt.show()