"""Thresholds and column roles for the cleaning.

Numbers come from profiling the Domain 2025-09-09 snapshot (12,717 listings).
Everything arguable lives here rather than inline, so a threshold can be
disagreed with in one place.
"""

from __future__ import annotations

TARGET = "weekly_rent"

# --- target plausibility ---------------------------------------------
RENT_SENTINELS = (0,)        # Domain's "price withheld", not a free property
RENT_SALE_PRICE_MIN = 20_000  # 3 listings carry a sale price (808,500 Berwick)
RENT_MIN = 80.0              # below this: a car space, a single room, or a typo
RENT_FLAG_HIGH = 3_000.0     # flagged, not dropped: real at the top of the market

# --- what counts as a comparable dwelling ----------------------------
NON_DWELLING_PROPERTY_TYPES = ("Car Space", "Vacant land", "Block of Units")

# Rooming houses advertise one room while bedrooms/bathrooms describe the whole
# building (Waurn Ponds 9 bed/9 bath at $300/wk). Filtering on these reads the
# target, so drop_per_room_pricing=False disables it.
MIN_RENT_PER_BEDROOM = 80.0
MIN_BEDROOMS_FOR_PER_ROOM_CHECK = 4

# --- implausible attributes become missing, never clipped ------------
MAX_BEDROOMS = 12             # a Southbank flat listed 50; Woodstock's 11 is real
MAX_BATHROOMS = 8             # with BATHROOM_BEDROOM_EXCESS: a studio listed 12
BATHROOM_BEDROOM_EXCESS = 2
MAX_CARSPACES = 12

# --- listing vintage and duplicates ----------------------------------
MAX_LISTING_AGE_DAYS = 365    # 314 listings predate 2024; one reads 2008
DUPLICATE_KEY = ("address_key", "postcode", "bedrooms", "bathrooms")

# --- columns that must never be predictors ---------------------------
# bond is 4.345 x weekly_rent on 83.5% of listings. Some upstream runs route
# these to vic_property_rent_ratios.csv instead of the master table; naming them
# here means the exclusion holds whether or not the column is present.
LEAKAGE_COLS = (
    "bond", "rent_per_bedroom", "rent_vs_2021_census_median",
    "rent_vs_sqm_market", "rent_to_area_median_hh_income_pct",
    "rent_outlier_high",  # a threshold on the target, added by clean.py
)

# The three SA codes correlate at r = 1.00; as numbers they let a tree split on
# the alphabetical ordering ABS used when assigning them.
ID_COLS = (
    "listing_id", "url", "address", "address_key", "sa2_code_2021",
    "sa3_code_2021", "sa4_code_2021", "gccsa_code_2021", "sal_code_2021",
    "mb_code_2021", "postcode",
)

# Duplicate the joined SA2 covariates, and would one-hot into hundreds of columns.
NAME_COLS = (
    "sa2_name_2021", "sa3_name_2021", "sa4_name_2021", "gccsa_name_2021",
    "sal_name_2021", "suburb",
)

# Consequences of the price, not causes. days_listed is also r = 1.00 with
# (snapshot - date_listed).
POST_LISTING_COLS = ("days_listed", "date_listed", "available_date")

TEXT_COLS = ("structured_features",)            # superseded by the feat_* flags
DUPLICATE_OF_PROPERTY_TYPE = ("secondary_type",)  # 99.95% equal to property_type

CATEGORICAL_COLS = ("property_type", "primary_type")

# --- redundancy -------------------------------------------------------
# 45 covariate pairs sit above 0.95. prune_vintages runs first, then
# prune_correlated keeps the first member of each group in preference order.
CORRELATION_THRESHOLD = 0.95

PREFER_PATTERNS = (
    r"_aus_percentile$",   # comparable across SEIFA indexes
    r"erp_2025$",          # ABS estimate over a VIF projection of the same year
    r"_2022_23$",          # most recent tax vintage
    r"^census_",
    r"^vif_population_growth",
    r"^population_growth",
)

# Dropped by name, with the reason that reaches the exclusion listing. Needed
# because ier_score and ier_aus_percentile correlate at only 0.94 despite being
# one index measured twice.
DROP_PATTERNS = (
    (r"_aus_decile$", "decile is a ten-bucket rounding of the percentile already kept"),
    (r"^(?:irsad|irsd|ier|ieo)_score$",
     "SEIFA raw score; the same index as its percentile on an arbitrary scale"),
)

# Re-releases of one measurement, where only the latest is wanted. The five tax
# years form a chain (neighbours above 0.95, the ends not), so correlation
# pruning alone keeps both ends. vif_population_* is excluded deliberately:
# those are projections to different horizons, not vintages.
VINTAGE_FAMILIES = (
    "tax_income_earners", "tax_median_total_personal_income",
    "tax_mean_total_personal_income", "erp",
)
VINTAGE_SUFFIX = r"_(\d{4})(?:_(\d{2}))?$"

# --- missing data -----------------------------------------------------
# Of null-carspace listings that carry a feature list, 7.5% mention parking,
# against 57.2% where carspaces is filled: null means none.
FILL_ZERO_COLS = ("carspaces",)
MISSING_INDICATOR_COLS = ("carspaces", "land_m2", "bedrooms", "bathrooms")
