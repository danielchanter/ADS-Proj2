"""The Domain listing table: cleaning, amenity flags, SA2 join, and the
rent-derived columns that must stay out of the modelling table."""

from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd

from .common import normalize_code

# Scraper/admin/low-information fields are dropped. secondary_type is left out
# because it equals property_type on every listing.
LISTING_COLUMNS = [
    "listing_id","suburb","postcode","weekly_rent","bond","available_date",
    "date_listed","days_listed","bedrooms","bathrooms","carspaces",
    "property_type","address","lat","lon","primary_type",
    "structured_features","url"
]
NUMERIC_COLUMNS = ["weekly_rent","bond","days_listed","bedrooms","bathrooms","carspaces","lat","lon"]
DATE_COLUMNS = ["date_listed"]

def load_listings(path):
    p = pd.read_csv(path)
    p = p[[c for c in LISTING_COLUMNS if c in p.columns]].copy()
    p["postcode"] = p["postcode"].astype("Int64")
    for c in NUMERIC_COLUMNS:
        if c in p:
            p[c] = pd.to_numeric(p[c], errors="coerce")
    for c in DATE_COLUMNS:
        if c in p:
            p[c] = pd.to_datetime(p[c], errors="coerce")
    return add_structured_feature_flags(p)

# Domain's structured_features holds 571 distinct free-text labels, many of them
# synonyms ("Balcony / Deck", "Balcony"; four spellings of split-system air con).
# Each flag below gathers the labels that mean the same thing. "Close to shops"
# and similar agent claims are left out: the measured distances cover them.
STRUCTURED_FEATURE_FLAGS = {
    "built_in_wardrobes": r"built.?in wardrobe",
    "heating": r"heating|fireplace|heater",
    "air_conditioning": r"air.?con|cooling",
    "dishwasher": r"dishwasher",
    "secure_parking": r"secure parking|garage|carport",
    "internal_laundry": r"internal laundry",
    "balcony": r"balcony|deck|terrace",
    "outdoor_space": r"garden|courtyard|outdoor entertain|fully fenced|backyard",
    "floorboards": r"floorboard|timber floor",
    "bath": r"^bath(?:tub)?$",
    "ensuite": r"ensuite",
    "study": r"study",
    "furnished": r"^(?:fully |partly |partially )?furnished",
    "pets_allowed": r"pets? allowed|pet friendly",
    "pool": r"swimming pool|^pool$|pool.?(?:in.?ground|above)",
    "gym": r"\bgym\b",
    "solar": r"solar",
    "security": r"intercom|alarm|security",
}

def add_structured_feature_flags(p):
    """
    One 0/1 column per STRUCTURED_FEATURE_FLAGS entry, replacing the raw text.

    A listing with no feature list gets <NA> in every flag rather than 0: the
    agent not filling the field says nothing about whether it has a dishwasher.
    """
    if "structured_features" not in p.columns:
        return p
    raw = p["structured_features"]
    tokens = raw.fillna("").astype(str).str.split(r",\s*").explode().str.strip().str.lower()
    for name, pattern in STRUCTURED_FEATURE_FLAGS.items():
        hit = tokens.str.contains(pattern, regex=True).groupby(level=0).any()
        p[f"feat_{name}"] = hit.astype("Int8").where(raw.notna())
    return p.drop(columns="structured_features")

# Geographic identifiers/names are strings. Do NOT initialise these with np.nan,
# because pandas will infer float64 and then reject SA2 codes such as "212021453".
SA2_STRING_COLS = [
    "sa2_code_2021","sa2_name_2021",
    "sa3_name_2021","sa4_name_2021","gccsa_name_2021"
]
SA2_NUMERIC_COLS = ["area_albers_sqkm"]

def join_sa2(p, sa2):
    """The listings as points, with the attributes of the SA2 each falls in."""
    prop_geo = gpd.GeoDataFrame(
        p,
        geometry=gpd.points_from_xy(p["lon"], p["lat"]),
        crs="EPSG:4326",
    )

    valid = prop_geo.geometry.notna() & prop_geo["lat"].notna() & prop_geo["lon"].notna()
    joined_valid = gpd.sjoin(
        prop_geo.loc[valid],
        sa2,
        how="left",
        predicate="within",
    ).drop(columns=["index_right"], errors="ignore")

    joined = prop_geo.copy()
    for c in SA2_STRING_COLS:
        joined[c] = pd.Series(pd.NA, index=joined.index, dtype="string")
    for c in SA2_NUMERIC_COLS:
        joined[c] = np.nan

    # Assign each group separately so pandas preserves the correct dtypes.
    for c in SA2_STRING_COLS:
        joined.loc[joined_valid.index, c] = (
            joined_valid[c].astype("string").to_numpy()
        )
    for c in SA2_NUMERIC_COLS:
        joined.loc[joined_valid.index, c] = pd.to_numeric(
            joined_valid[c], errors="coerce"
        ).to_numpy()

    joined["sa2_code_2021"] = normalize_code(joined["sa2_code_2021"])
    return joined

# Columns computed from weekly_rent. They go to vic_property_rent_ratios.csv, not
# the master table, so they cannot end up as predictors of the rent they divide.
# bond is here too: it is set from the rent, almost always one month of it.
TARGET_DERIVED_COLS = [
    "bond",
    "rent_per_bedroom",
    "rent_vs_2021_census_median",
    "rent_vs_sqm_market",
    "rent_to_area_median_hh_income_pct",
]

# A Victorian bond is capped at one month's rent unless rent exceeds $900/week,
# which puts nearly all of them at 4.35 weeks. Outside 2-8 weeks is a data error.
BOND_WEEKS_RANGE = (2, 8)

def split_target_derived(master):
    """Return (master without rent-derived columns, those columns by listing_id)."""
    rent = pd.to_numeric(master["weekly_rent"], errors="coerce").replace(0, np.nan)

    def ratio_to(col):
        if col not in master.columns:
            return np.nan
        return rent / pd.to_numeric(master[col], errors="coerce").replace(0, np.nan)

    ratios = pd.DataFrame({"listing_id": master["listing_id"]})
    if "bond" in master.columns:
        weeks = pd.to_numeric(master["bond"], errors="coerce") / rent
        ratios["bond"] = master["bond"].where(weeks.between(*BOND_WEEKS_RANGE))
    if "bedrooms" in master.columns:
        ratios["rent_per_bedroom"] = ratio_to("bedrooms")
    ratios["rent_vs_2021_census_median"] = ratio_to("census_median_rent_weekly")
    ratios["rent_vs_sqm_market"] = ratio_to("sqm_market_rent")
    # Affordability for Q3: listing rent against the area's weekly household income.
    ratios["rent_to_area_median_hh_income_pct"] = 100 * ratio_to(
        "census_median_household_income_weekly"
    )

    master = master.drop(columns=[c for c in TARGET_DERIVED_COLS if c in master.columns])
    return master, ratios
