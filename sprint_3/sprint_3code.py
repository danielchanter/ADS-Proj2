import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler
from sklearn.pipeline import Pipeline
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error, r2_score

# 1. LOAD DATA

df = pd.read_csv("vic_property_master.csv")

target = "weekly_rent"

df[target] = pd.to_numeric(
    df[target],
    errors="coerce"
)

df = df.dropna(subset=[target])

# Remove unrealistic weekly rents
df = df[
    (df[target] >= 100) &
    (df[target] <= 5000)
]

print("Rows:", len(df))
print("Columns:", len(df.columns))

# 2. READABLE FEATURE NAMES

pretty_names = {

    # PROPERTY / GEOGRAPHICAL

    "lat": "Latitude",
    "lon": "Longitude",

    "area_albers_sqkm": "SA2 area",

    "nearest_school_km": "Distance to school",
    "nearest_train_station_km": "Distance to train station",
    "nearest_bar_pub_km": "Distance to bar/pub",
    "nearest_cafe_km": "Distance to cafe",
    "nearest_childcare_km": "Distance to childcare",
    "nearest_gp_clinic_km": "Distance to GP clinic",
    "nearest_gym_km": "Distance to gym",
    "nearest_hospital_km": "Distance to hospital",
    "nearest_library_km": "Distance to library",
    "nearest_park_km": "Distance to park",
    "nearest_restaurant_km": "Distance to restaurant",
    "nearest_shopping_centre_km": "Distance to shopping centre",
    "nearest_supermarket_km": "Distance to supermarket",

    "bedrooms": "Bedrooms",
    "bathrooms": "Bathrooms",
    "carspaces": "Car spaces",

    "school_count": "Nearby schools",
    "train_station_count": "Nearby train stations",

    "osm_cafe_count": "Nearby cafes",
    "osm_childcare_count": "Nearby childcare centres",
    "osm_gp_clinic_count": "Nearby GP clinics",
    "osm_gym_count": "Nearby gyms",
    "osm_hopsital_count": "Nearby hospitals",
    "osm_hospital_count": "Nearby hospitals",
    "osm_library_count": "Nearby libraries",
    "osm_park_count": "Nearby parks",
    "osm_restaurant_count": "Nearby restaurants",
    "osm_shopping_centre_count": "Nearby shopping centres",
    "osm_supermarket_count": "Nearby supermarkets",

    # SOCIOECONOMIC

    "census_median_age": "Median age",
    "census_population_2021": "Population (2021)",

    "census_median_personal_income_weekly":
        "Median personal income",

    "census_median_household_income_weekly":
        "Median household income",

    "census_median_rent_weekly":
        "Area median rent",

    "census_median_mortgage_monthly":
        "Area median mortgage",

    "census_avg_household_size":
        "Average household size",

    "age_0_14_pct":
        "Population aged 0–14",

    "age_25_44_pct":
        "Population aged 25–44",

    "age_65_plus_pct":
        "Population aged 65+",

    "born_overseas_pct":
        "Born overseas",

    "non_english_home_pct":
        "Non-English speaking households",

    "irsad_score":
        "Socioeconomic advantage",

    "irsd_score":
        "Socioeconomic disadvantage",

    "ieo_score":
        "Education & occupation",

    "ier_score":
        "Economic resources",

    "population_density_2025":
        "Population density",

    "population_growth_pct_2024_25":
        "Population growth (2024–25)",

    "erp_2024":
        "Estimated population (2024)",

    "erp_2025":
        "Estimated population (2025)",

    "tax_income_earners_2022_23":
        "Income earners",

    "tax_median_total_personal_income_2022_23":
        "Median personal income (tax)",

    "tax_mean_total_personal_income_2022_23":
        "Mean personal income",

    "vif_population_2021":
        "VIF population (2021)",

    "vif_population_2026":
        "VIF population forecast (2026)",

    "vif_population_2031":
        "VIF population forecast (2031)",

    "vif_population_2036":
        "VIF population forecast (2036)",

    "vif_population_growth_pct_2021_26":
        "Forecast population growth (2021–26)",

    "vif_population_growth_pct_2026_36":
        "Forecast population growth (2026–36)",

    # MISCELLANEOUS

    "days_listed":
        "Days listed"
}

# 3. GEOGRAPHICAL + PROPERTY FEATURES

geo_property_features = [

    # Geographic location
    "lat",
    "lon",
    "area_albers_sqkm",

    # Accessibility
    "nearest_school_km",
    "nearest_train_station_km",
    "nearest_bar_pub_km",
    "nearest_cafe_km",
    "nearest_childcare_km",
    "nearest_gp_clinic_km",
    "nearest_gym_km",
    "nearest_hospital_km",
    "nearest_library_km",
    "nearest_park_km",
    "nearest_restaurant_km",
    "nearest_shopping_centre_km",
    "nearest_supermarket_km",

    # Property characteristics
    "bedrooms",
    "bathrooms",
    "carspaces",

    # Amenity counts
    "school_count",
    "train_station_count",
    "osm_cafe_count",
    "osm_childcare_count",
    "osm_gp_clinic_count",
    "osm_gym_count",
    "osm_hopsital_count",
    "osm_library_count",
    "osm_park_count",
    "osm_restaurant_count",
    "osm_shopping_centre_count",
    "osm_supermarket_count"
]

# 4. SOCIOECONOMIC FEATURES

socioeconomic_features = [

    # Census
    "census_median_age",
    "census_population_2021",
    "census_median_personal_income_weekly",
    "census_median_household_income_weekly",
    "census_median_rent_weekly",
    "census_median_mortgage_monthly",
    "census_avg_household_size",

    # Demographics
    "age_0_14_pct",
    "age_25_44_pct",
    "age_65_plus_pct",
    "born_overseas_pct",
    "non_english_home_pct",

    # SEIFA
    "irsad_score",
    "irsd_score",
    "ieo_score",
    "ier_score",

    # Population
    "population_density_2025",
    "population_growth_pct_2024_25",
    "erp_2024",
    "erp_2025",

    # Most recent tax data
    "tax_income_earners_2022_23",
    "tax_median_total_personal_income_2022_23",
    "tax_mean_total_personal_income_2022_23",

    # VIF population
    "vif_population_2021",
    "vif_population_2026",
    "vif_population_2031",
    "vif_population_2036",
    "vif_population_growth_pct_2021_26",
    "vif_population_growth_pct_2026_36"
]

# 5. MISCELLANEOUS FEATURES

# available_date and date_listed are dates rather than simple
# numerical predictors, and URL is an identifier.
#
# days_listed is useful numerically.

misc_features = [
    "days_listed"
]

# 6. REMOVE FEATURES THAT DO NOT EXIST

def existing_features(feature_list):

    missing = [
        feature
        for feature in feature_list
        if feature not in df.columns
    ]

    if missing:
        print("\nNot found in dataset:")
        for feature in missing:
            print(" -", feature)

    return [
        feature
        for feature in feature_list
        if feature in df.columns
    ]


geo_property_features = existing_features(
    geo_property_features
)

socioeconomic_features = existing_features(
    socioeconomic_features
)

misc_features = existing_features(
    misc_features
)

# 7. CREATE THE THREE FEATURE SETS

geo_model_features = (
    geo_property_features
    + misc_features
)

socio_model_features = (
    socioeconomic_features
    + misc_features
)

all_model_features = list(dict.fromkeys(
    geo_property_features
    + socioeconomic_features
    + misc_features
))


print("\nFeature sets")
print("------------")
print(
    "Geographical/property:",
    len(geo_model_features)
)
print(
    "Socioeconomic:",
    len(socio_model_features)
)
print(
    "All:",
    len(all_model_features)
)

# 8. MODEL FUNCTION

def run_models(features, model_name):

    print("\n")
    print("=" * 70)
    print(model_name.upper())
    print("=" * 70)

    # DATA

    X = df[features].copy()
    y = df[target].copy()

    # Make sure everything is numeric
    for col in X.columns:
        X[col] = pd.to_numeric(
            X[col],
            errors="coerce"
        )

    # Infinite -> missing
    X = X.replace(
        [np.inf, -np.inf],
        np.nan
    )

    # Drop columns that are completely missing
    completely_missing = (
        X.columns[
            X.isna().all()
        ].tolist()
    )

    if completely_missing:

        print(
            "\nRemoved completely missing columns:"
        )

        for col in completely_missing:
            print(" -", col)

        X = X.drop(
            columns=completely_missing
        )

    # Median imputation
    X = X.fillna(
        X.median()
    )

    # TRAIN / TEST SPLIT

    X_train, X_test, y_train, y_test = (
        train_test_split(
            X,
            y,
            test_size=0.2,
            random_state=42
        )
    )

    # EXTRA TREES

    extra = ExtraTreesRegressor(
        n_estimators=300,
        random_state=42,
        n_jobs=-1
    )

    extra.fit(
        X_train,
        y_train
    )

    extra_predictions = extra.predict(
        X_test
    )

    extra_mae = mean_absolute_error(
        y_test,
        extra_predictions
    )

    extra_r2 = r2_score(
        y_test,
        extra_predictions
    )


    print("\nExtra Trees")
    print("-----------")
    print(
        f"MAE: ${extra_mae:.2f}"
    )
    print(
        f"R²: {extra_r2:.3f}"
    )

    # EXTRA TREES IMPORTANCE

    extra_importance = pd.DataFrame({
        "feature": X.columns,
        "importance":
            extra.feature_importances_
    })

    extra_importance = (
        extra_importance
        .sort_values(
            "importance",
            ascending=False
        )
        .reset_index(drop=True)
    )

    extra_importance["label"] = (
        extra_importance["feature"]
        .map(pretty_names)
        .fillna(
            extra_importance["feature"]
        )
    )


    print(
        "\nTop 10 Extra Trees Features"
    )

    print(
        extra_importance[
            [
                "label",
                "importance"
            ]
        ].head(10)
    )

    # EXTRA TREES GRAPH

    extra_top10 = (
        extra_importance
        .head(10)
        .sort_values(
            "importance"
        )
    )

    plt.figure(
        figsize=(10, 6)
    )

    plt.barh(
        extra_top10["label"],
        extra_top10["importance"]
    )

    plt.xlabel(
        "Feature Importance"
    )

    plt.ylabel("")

    plt.title(
        f"{model_name}: Extra Trees"
    )

    plt.tight_layout()
    plt.show()

    # RIDGE

    alphas = np.logspace(
        -3,
        4,
        150
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


    print("\nRidge")
    print("-----")

    print(
        f"MAE: ${ridge_mae:.2f}"
    )

    print(
        f"R²: {ridge_r2:.3f}"
    )

    print(
        "Best alpha:",
        ridge.named_steps[
            "ridge"
        ].alpha_
    )

    # RIDGE COEFFICIENTS

    ridge_results = pd.DataFrame({

        "feature":
            X.columns,

        "coefficient":
            ridge.named_steps[
                "ridge"
            ].coef_
    })

    ridge_results["importance"] = (
        ridge_results[
            "coefficient"
        ].abs()
    )

    ridge_results = (
        ridge_results
        .sort_values(
            "importance",
            ascending=False
        )
        .reset_index(drop=True)
    )

    ridge_results["label"] = (
        ridge_results["feature"]
        .map(pretty_names)
        .fillna(
            ridge_results["feature"]
        )
    )


    print(
        "\nTop 10 Ridge Features"
    )

    print(
        ridge_results[
            [
                "label",
                "coefficient",
                "importance"
            ]
        ].head(10)
    )

    # RIDGE GRAPH

    ridge_top10 = (
        ridge_results
        .head(10)
        .sort_values(
            "coefficient"
        )
    )

    plt.figure(
        figsize=(10, 6)
    )

    plt.barh(
        ridge_top10["label"],
        ridge_top10["coefficient"]
    )

    plt.axvline(
        x=0,
        linewidth=1
    )

    plt.xlabel(
        "Standardised Ridge Coefficient"
    )

    plt.ylabel("")

    plt.title(
        f"{model_name}: Ridge Regression"
    )

    plt.tight_layout()
    plt.show()


    # RETURN RESULTS

    return {
        "extra_mae": extra_mae,
        "extra_r2": extra_r2,

        "ridge_mae": ridge_mae,
        "ridge_r2": ridge_r2,

        "extra_importance":
            extra_importance,

        "ridge_results":
            ridge_results
    }

# GEOGRAPHICAL + PROPERTY MODEL

geo_results = run_models(
    geo_model_features,
    "Geographical + Property Features"
)

# SOCIOECONOMIC MODEL

socio_results = run_models(
    socio_model_features,
    "Socioeconomic Features"
)


# ALL FEATURES MODE

all_results = run_models(
    all_model_features,
    "All Features"
)

# COMPARE MODEL PERFORMANCE

performance = pd.DataFrame({

    "Feature Set": [
        "Geographical + Property",
        "Socioeconomic",
        "All Features"
    ],

    "Extra Trees MAE": [
        geo_results["extra_mae"],
        socio_results["extra_mae"],
        all_results["extra_mae"]
    ],

    "Extra Trees R2": [
        geo_results["extra_r2"],
        socio_results["extra_r2"],
        all_results["extra_r2"]
    ],

    "Ridge MAE": [
        geo_results["ridge_mae"],
        socio_results["ridge_mae"],
        all_results["ridge_mae"]
    ],

    "Ridge R2": [
        geo_results["ridge_r2"],
        socio_results["ridge_r2"],
        all_results["ridge_r2"]
    ]
})


print("\n")
print("=" * 70)
print("FINAL MODEL COMPARISON")
print("=" * 70)

print(
    performance.round(3)
)