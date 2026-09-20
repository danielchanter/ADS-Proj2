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


X = df[features].copy()
y = df[target]

X = X.replace([np.inf, -np.inf], np.nan)
X = X.fillna(X.median())

from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

# --------------------------------------------------
# TRAIN / TEST SPLIT
# --------------------------------------------------

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)


# --------------------------------------------------
# RIDGE REGRESSION
# --------------------------------------------------

# Try different strengths of regularisation
alphas = np.logspace(-3, 3, 100)

ridge = Pipeline([
    ("scaler", StandardScaler()),
    ("ridge", RidgeCV(alphas=alphas))
])

ridge.fit(X_train, y_train)


# --------------------------------------------------
# MODEL PERFORMANCE
# --------------------------------------------------

predictions = ridge.predict(X_test)

mae = mean_absolute_error(y_test, predictions)
r2 = r2_score(y_test, predictions)

print("\nRidge Regression Performance")
print("----------------------------")
print(f"MAE: ${mae:.2f}")
print(f"R²: {r2:.3f}")

print(
    f"Best alpha: "
    f"{ridge.named_steps['ridge'].alpha_:.4f}"
)


# --------------------------------------------------
# FEATURE COEFFICIENTS
# --------------------------------------------------

coefficients = pd.DataFrame({
    "feature": X.columns,
    "coefficient": ridge.named_steps["ridge"].coef_
})

# Absolute coefficient tells us strength
coefficients["importance"] = coefficients[
    "coefficient"
].abs()

coefficients = coefficients.sort_values(
    "importance",
    ascending=False
).reset_index(drop=True)


# --------------------------------------------------
# TOP 20 FEATURES
# --------------------------------------------------

print("\nTop 20 Ridge Features")
print("---------------------")

print(
    coefficients[
        ["feature", "coefficient", "importance"]
    ].head(20)
)


# --------------------------------------------------
# PLOT TOP 20
# --------------------------------------------------

top20 = coefficients.head(20).sort_values(
    "importance",
    ascending=True
)

plt.figure(figsize=(10, 8))

plt.barh(
    top20["feature"],
    top20["importance"]
)

plt.xlabel("Absolute Standardised Ridge Coefficient")
plt.ylabel("Feature")
plt.title("Top 20 Features for Predicting Weekly Rent - Ridge")

plt.tight_layout()
plt.show()

from sklearn.linear_model import LassoCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

# --------------------------------------------------
# TRAIN / TEST SPLIT
# --------------------------------------------------

X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    random_state=42
)


# --------------------------------------------------
# LASSO REGRESSION
# --------------------------------------------------

lasso = Pipeline([
    ("scaler", StandardScaler()),
    ("lasso", LassoCV(
        alphas=np.logspace(-3, 3, 100),
        cv=5,
        max_iter=10000
    ))
])

lasso.fit(X_train, y_train)


# --------------------------------------------------
# MODEL PERFORMANCE
# --------------------------------------------------

predictions = lasso.predict(X_test)

mae = mean_absolute_error(y_test, predictions)
r2 = r2_score(y_test, predictions)

print("\nLasso Regression Performance")
print("----------------------------")
print(f"MAE: ${mae:.2f}")
print(f"R²: {r2:.3f}")
print(f"Best alpha: {lasso.named_steps['lasso'].alpha_:.4f}")


# --------------------------------------------------
# FEATURE COEFFICIENTS
# --------------------------------------------------

coefficients = pd.DataFrame({
    "feature": X.columns,
    "coefficient": lasso.named_steps["lasso"].coef_
})

# Absolute coefficient = feature importance
coefficients["importance"] = coefficients[
    "coefficient"
].abs()

coefficients = coefficients.sort_values(
    "importance",
    ascending=False
).reset_index(drop=True)


# --------------------------------------------------
# SELECTED FEATURES
# --------------------------------------------------

selected = coefficients[
    coefficients["coefficient"] != 0
].copy()

removed = coefficients[
    coefficients["coefficient"] == 0
].copy()

print("\nFeatures selected by Lasso:")
print("---------------------------")
print(selected[["feature", "coefficient"]])

print("\nNumber of features selected:", len(selected))
print("Number of features removed:", len(removed))


# --------------------------------------------------
# FEATURES REMOVED BY LASSO
# --------------------------------------------------

print("\nFeatures removed by Lasso:")
print("--------------------------")

for feature in removed["feature"]:
    print(feature)


# --------------------------------------------------
# TOP 20 FEATURES
# --------------------------------------------------

top20 = selected.head(20).sort_values(
    "importance",
    ascending=True
)

plt.figure(figsize=(10, 8))

plt.barh(
    top20["feature"],
    top20["importance"]
)

plt.xlabel("Absolute Standardised Lasso Coefficient")
plt.ylabel("Feature")
plt.title("Top 20 Features for Predicting Weekly Rent - Lasso")

plt.tight_layout()
plt.show()