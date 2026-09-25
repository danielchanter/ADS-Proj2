"""
Q2: predicted suburb rental-growth rate, next five years.

The target var here comes from the SQM Research weekly rent index with 
a log-linear trend fitted to each postcode's recent index gives an annualised
rent-growth rate. so this is our "predicted growth rate".

Population and income growth are features fed into a model that predicts
the SQM-measured rent-growth trend.

Suburbs are the granularity for question 2 but SQM is per postcode and the demand side covariates are per SA2. 

Both are moved onto suburbs with the share correspondence built in sprint_2

"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.model_selection import KFold, cross_val_predict
from sklearn.metrics import mean_absolute_error, r2_score

REPO = Path(__file__).resolve().parent.parent
PROCESSED = REPO / "sprint_2" / "data" / "processed"
SQM_PATH = REPO / "data" / "external" / "sqm_weekly_rents.csv"
OUT_DIR = Path(__file__).resolve().parent / "data"




TREND_YEARS = 3 # How much recent history the trend is fitted on.


MIN_MONTHS = 30 # A postcode needs at least this many monthly points inside that window before its trend is trusted

SERIES = "combined"

RANDOM_STATE = 42

MIN_DWELLINGS_FOR_RANKING = 200 # Suburbs below this many 2021 dwellings are excluded from the top-10 ranking



# ---------------------------------------------------------------------
# rent-growth trend per postcode
# ---------------------------------------------------------------------

def load_sqm(path: Path) -> pd.DataFrame:
    d = pd.read_csv(path)
    d["date"] = pd.to_datetime(d["date"])
    d[SERIES] = pd.to_numeric(d[SERIES], errors="coerce").replace(0, np.nan)
    return d


def postcode_growth(
    sqm: pd.DataFrame, years=TREND_YEARS, min_months=MIN_MONTHS, min_distinct_frac=0.4,
    max_abs_growth_pct=20.0,
) -> pd.DataFrame:
    """
    Annualised rent-growth rate per postcode.
    OLS slope of log(rent) on time and fitted to the last `years` of monthly-averaged data.

    Monthly averaging first because weekly SQM data is noisy and a log-linear
    fit on weekly points may let a few stale/duplicated weeks skew the slope.

    Thin rural postcodes need a second guard beyond months of coverage since SQM's 
    index can be tracking one or two listings rather than a market.
     
    These can shows up as long runs of near identical values punctuated by a sudden level
    shift when that one listing changes

    `min_distinct_frac` rejects a postcode whose monthly series is mostly
    repeats, and `max_abs_growth_pct` catches whatever still slips through.
    """
    d = sqm[["postcode", "date", SERIES]].dropna()
    d = d.assign(month=d["date"].dt.to_period("M").dt.to_timestamp())
    monthly = d.groupby(["postcode", "month"], as_index=False)[SERIES].mean()

    cutoff = monthly["month"].max() - pd.DateOffset(years=years)
    recent = monthly[monthly["month"] >= cutoff]

    rows = []
    for postcode, g in recent.groupby("postcode"):
        g = g.sort_values("month")
        if len(g) < min_months:
            continue
        if g[SERIES].nunique() < min_distinct_frac * len(g):
            continue
        t = (g["month"] - g["month"].min()).dt.days.to_numpy() / 365.25
        y = np.log(g[SERIES].to_numpy())
        slope, intercept = np.polyfit(t, y, 1)
        growth_pct = (np.exp(slope) - 1) * 100
        if abs(growth_pct) > max_abs_growth_pct:
            continue
        fitted = intercept + slope * t
        resid_var = np.var(y - fitted)
        y_var = np.var(y)
        rows.append({
            "postcode": postcode,
            "rent_growth_pct": growth_pct,
            "trend_r2": 1 - resid_var / y_var if y_var > 0 else np.nan,
            "trend_n_months": len(g),
            "current_market_rent": g[SERIES].iloc[-1],
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------
# Suburb to postcode and SA2 weighted mapping
# ---------------------------------------------------------------------

def move_to_suburb(corr: pd.DataFrame, values: pd.DataFrame, key_col: str) -> pd.DataFrame:
    """
    Dwelling-weighted average of `values` (indexed by postcode or sa2_code_2021) onto suburbs (SAL).

    `share_of_suburb` on a correspondence row is that suburb's dwellings in
    one suburb x postcode x SA2 overlap.
    Summing it within (suburb, key) gives the share of the suburb's dwellings in that one postcode or SA2, and
    weighting by that (renormalised over rows with a non-null value) gives a
    suburb figure comparable across suburbs regardless of how many
    postcodes/SA2s they cross.
    
    `coverage` is the weight  matched so a suburb whose postcodes mostly
    lack an SQM trend is visibly thin rather than silently averaging over a handful of covered ones.
    """
    weights = (
        corr.groupby(["sal_code_2021", "sal_name_2021", key_col], as_index=False)
        ["share_of_suburb"].sum()
    )
    weights[key_col] = weights[key_col].astype(str)
    values = values.copy()
    values[key_col] = values[key_col].astype(str)
    merged = weights.merge(values, on=key_col, how="left")

    value_cols = [c for c in values.columns if c != key_col]

    def agg(g: pd.DataFrame) -> pd.Series:
        total_w = g["share_of_suburb"].sum()
        out = {"sal_name_2021": g["sal_name_2021"].iloc[0]}
        covered_w = None
        for col in value_cols:
            valid = g.dropna(subset=[col])
            w = valid["share_of_suburb"].sum()
            out[col] = np.average(valid[col], weights=valid["share_of_suburb"]) if w > 0 else np.nan
            if covered_w is None:
                covered_w = w
        out["coverage"] = covered_w / total_w if total_w else 0.0
        return pd.Series(out)

    return merged.groupby("sal_code_2021").apply(agg).reset_index()


# ---------------------------------------------------------------------
#  Historical growth of a yearly SA2 series (population, income)
# ---------------------------------------------------------------------

def sa2_series_cagr(sa2_yearly: pd.DataFrame, col: str, out_name: str, years=5) -> pd.DataFrame:
    """Same log-linear trend as postcode_growth, applied to a yearly SA2 series."""
    d = sa2_yearly[["sa2_code_2021", "year", col]].dropna()
    d = d[d[col] > 0]
    cutoff = d["year"].max() - years
    recent = d[d["year"] >= cutoff]

    rows = []
    for code, g in recent.groupby("sa2_code_2021"):
        g = g.sort_values("year")
        if len(g) < 3:
            continue
        t = (g["year"] - g["year"].min()).to_numpy(dtype=float)
        y = np.log(g[col].to_numpy())
        slope, _ = np.polyfit(t, y, 1)
        rows.append({"sa2_code_2021": code, out_name: (np.exp(slope) - 1) * 100})
    return pd.DataFrame(rows)


#  feature table

SA2_FEATURE_COLS = [
    "irsad_score", "irsd_score", "ier_score", "ieo_score",
    "census_median_household_income_weekly", "census_median_rent_weekly",
    "population_density_2025", "erp_2025",
    "vif_population_growth_pct_2026_31",
    "age_25_44_pct", "born_overseas_pct",
    "school_count", "train_station_count", "tram_stop_count", "bus_stop_count",
]


def build_suburb_table() -> pd.DataFrame:
    corr = pd.read_csv(
        PROCESSED / "suburb_postcode_sa2.csv",
        dtype={"postcode": str, "sa2_code_2021": str, "sal_code_2021": str},
    )
    sqm = load_sqm(SQM_PATH)
    sa2_master = pd.read_csv(PROCESSED / "sa2_master.csv", dtype={"sa2_code_2021": str})
    sa2_yearly = pd.read_csv(PROCESSED / "sa2_yearly.csv", dtype={"sa2_code_2021": str})
    master = pd.read_csv(
        PROCESSED / "vic_property_master.csv",
        dtype={"sal_code_2021": str},
        low_memory=False,
    )

    # measured rent growth trend
    pc_growth = postcode_growth(sqm)
    target = move_to_suburb(
        corr, pc_growth[["postcode", "rent_growth_pct", "trend_r2", "trend_n_months"]],
        "postcode",
    ).rename(columns={"coverage": "sqm_coverage"})

    # demand-side features, SA2 to suburb 
    sa2_features = sa2_master[["sa2_code_2021"] + SA2_FEATURE_COLS].copy()
    feat = move_to_suburb(corr, sa2_features, "sa2_code_2021").drop(columns=["coverage"])

    pop_cagr = sa2_series_cagr(sa2_yearly, "erp", "erp_cagr_pct")
    income_cagr = sa2_series_cagr(sa2_yearly, "income_median", "income_median_cagr_pct")
    sa2_history = pop_cagr.merge(income_cagr, on="sa2_code_2021", how="outer")
    hist = move_to_suburb(corr, sa2_history, "sa2_code_2021").drop(columns=["coverage"])

    # crime
    crime_cols = [
        "crime_suburb_rate_per_1k", "crime_suburb_rate_yoy_pct",
        "crime_suburb_rate_3yr_change_pct",
    ]
    have_crime = [c for c in crime_cols if c in master.columns]
    crime = pd.DataFrame(columns=["sal_code_2021"] + have_crime)
    if have_crime and "sal_code_2021" in master.columns:
        m = master.dropna(subset=["sal_code_2021", "date_listed"]).sort_values("date_listed")
        crime = (
            m.groupby("sal_code_2021")[have_crime].last().reset_index()
        )

    # Suburb size
    dwellings = (
        corr.groupby(["sal_code_2021"], as_index=False)["dwellings_2021"].sum()
    )

    suburb = (
        target.merge(feat, on=["sal_code_2021", "sal_name_2021"], how="outer")
        .merge(hist, on=["sal_code_2021", "sal_name_2021"], how="outer")
        .merge(crime, on="sal_code_2021", how="left")
        .merge(dwellings, on="sal_code_2021", how="left")
    )
    return suburb








# Model to predict the measured trend from external features


MODEL_FEATURES = SA2_FEATURE_COLS + [
    "erp_cagr_pct", "income_median_cagr_pct",
    "crime_suburb_rate_per_1k", "crime_suburb_rate_yoy_pct", "crime_suburb_rate_3yr_change_pct",
]

# A suburb's measured trend is only used as a training label once at least
# this much of its dwellings sit in postcodes with a trustworthy SQM trend;
# below that, one thin postcode could dominate the average.
MIN_TRAIN_COVERAGE = 0.5


def fit_growth_model(suburb: pd.DataFrame):
    features = [c for c in MODEL_FEATURES if c in suburb.columns]
    train = suburb[
        (suburb["sqm_coverage"] >= MIN_TRAIN_COVERAGE) & suburb["rent_growth_pct"].notna()
    ].copy()

    medians = train[features].median()
    X_train = train[features].fillna(medians)
    y_train = train["rent_growth_pct"]

    model = ExtraTreesRegressor(
        n_estimators=500, min_samples_leaf=3, random_state=RANDOM_STATE, n_jobs=-1
    )
    cv = KFold(n_splits=5, shuffle=True, random_state=RANDOM_STATE)
    cv_pred = cross_val_predict(model, X_train, y_train, cv=cv)

    print(f"Training suburbs (sqm_coverage >= {MIN_TRAIN_COVERAGE}): {len(train)}")
    print(f"5-fold CV MAE:  {mean_absolute_error(y_train, cv_pred):.2f} pct pts/yr")
    print(f"5-fold CV R^2:  {r2_score(y_train, cv_pred):.3f}")

    model.fit(X_train, y_train)

    importance = (
        pd.Series(model.feature_importances_, index=features)
        .sort_values(ascending=False)
    )
    print("\nFeature importance (predicting measured rent-growth trend):")
    print(importance.to_string())

    X_all = suburb[features].fillna(medians)
    suburb = suburb.copy()
    suburb["model_growth_pct"] = model.predict(X_all)
    return suburb, importance






# Measured trend and model prediction by coverage

def main():
    suburb = build_suburb_table()
    suburb, importance = fit_growth_model(suburb)

    coverage = suburb["sqm_coverage"].fillna(0).clip(0, 1)
    measured = suburb["rent_growth_pct"]
    suburb["predicted_growth_pct"] = np.where(
        measured.notna(),
        coverage * measured.fillna(0) + (1 - coverage) * suburb["model_growth_pct"],
        suburb["model_growth_pct"],
    )

    suburb = suburb.sort_values("predicted_growth_pct", ascending=False)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    suburb.to_csv(OUT_DIR / "suburb_growth_predictions.csv", index=False)
    importance.rename("importance").to_csv(OUT_DIR / "growth_feature_importance.csv")

    cols = ["sal_name_2021", "predicted_growth_pct", "rent_growth_pct",
            "model_growth_pct", "sqm_coverage", "dwellings_2021"]
    ranked = suburb[suburb["dwellings_2021"] >= MIN_DWELLINGS_FOR_RANKING]
    print(f"\nTop 10 suburbs by predicted 5-year rent growth "
          f"(>= {MIN_DWELLINGS_FOR_RANKING} dwellings, {len(ranked):,}/{len(suburb):,} suburbs eligible):")
    print(ranked[cols].head(10).to_string(index=False))

    print(f"\nWrote {len(suburb):,} suburbs to {OUT_DIR / 'suburb_growth_predictions.csv'}")


if __name__ == "__main__":
    main()
