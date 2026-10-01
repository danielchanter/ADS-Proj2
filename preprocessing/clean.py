"""Cleaning the property table.

Rows are dropped only for an unusable target, the wrong vintage, or being a
duplicate property. A bad feature value becomes missing instead of being
clipped or filled, so how to fill it stays a decision for the model.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from . import config as cfg

# ---------------------------------------------------------------------
# LOADING
# ---------------------------------------------------------------------

# Read as strings so an SA2 code is never inferred as a float.
_STRING_COLS = ["listing_id", "postcode", "sa2_code_2021", "sa3_code_2021",
                "sa4_code_2021", "gccsa_code_2021", "sal_code_2021", "mb_code_2021"]
_NUMERIC_COLS = ["weekly_rent", "bond", "bedrooms", "bathrooms", "carspaces",
                 "days_listed", "lat", "lon", "land_m2"]
_DATE_COLS = ["date_listed", "available_date"]


def load_master(path):
    """The property master table, with every column that has a meaning typed."""
    path = Path(path)
    head = pd.read_csv(path, nrows=0)
    df = pd.read_csv(path, low_memory=False,
                     dtype={c: "string" for c in _STRING_COLS if c in head.columns})
    for c in _NUMERIC_COLS:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    for c in _DATE_COLS:
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    return df


# ---------------------------------------------------------------------
# TARGET
# ---------------------------------------------------------------------

def _void_rent_sentinels(df):
    # A weekly_rent of 0 is Domain's "price withheld". Voiding it before the
    # target filter keeps it out of the implausibly-low group.
    df.loc[df[cfg.TARGET].isin(cfg.RENT_SENTINELS), cfg.TARGET] = np.nan
    return df


def _drop_unusable_target(df):
    """Rows whose weekly_rent cannot be a weekly dwelling rent.

    Three problems share the column: no price at all, a sale price sitting in
    the rent field (808,500 in Berwick, 595,000 in Rosebud), and figures at $18
    to $30 that price a room or a car space. A sale price is dropped rather than
    rescued by dividing, because a sale price and an annual figure cannot be
    told apart here and guessing would invent a target value.
    """
    rent = df[cfg.TARGET]
    unusable = rent.isna() | (rent >= cfg.RENT_SALE_PRICE_MIN) | (rent < cfg.RENT_MIN)
    return df[~unusable].copy()


def _flag_high_rent(df):
    # The top of the tail is real ($5,866 in Camberwell, $5,750 in Toorak), so
    # these are flagged rather than dropped. The flag is a threshold on the
    # target, which is why config.LEAKAGE_COLS also names it.
    df["rent_outlier_high"] = (df[cfg.TARGET] > cfg.RENT_FLAG_HIGH).astype("int8")
    return df


# ---------------------------------------------------------------------
# WHAT IS BEING PRICED
# ---------------------------------------------------------------------

def _drop_non_dwellings(df):
    """Car spaces, vacant land and whole blocks of units price something other
    than one dwelling, and no cleaning makes them comparable. Farm and
    Acreage / Semi-Rural stay: unusual dwellings, not non-dwellings."""
    if "property_type" not in df.columns:
        return df
    return df[~df["property_type"].isin(cfg.NON_DWELLING_PROPERTY_TYPES)].copy()


def _drop_per_room_pricing(df):
    """Rooming houses, where the rent is one room but bedrooms is the building.

    A nine-bedroom, nine-bathroom house in Waurn Ponds at $300 a week is the
    shape of it, and property_type gives no hint. This rule reads weekly_rent to
    decide which rows to keep, which is selection on the outcome; it is applied
    because those rows measure a different quantity, and drop_per_room_pricing
    turns it off.
    """
    if "bedrooms" not in df.columns:
        return df
    per_bed = df[cfg.TARGET] / df["bedrooms"].replace(0, np.nan)
    suspect = ((df["bedrooms"] >= cfg.MIN_BEDROOMS_FOR_PER_ROOM_CHECK)
               & (per_bed < cfg.MIN_RENT_PER_BEDROOM))
    return df[~suspect.fillna(False)].copy()


# ---------------------------------------------------------------------
# ATTRIBUTE REPAIR
# ---------------------------------------------------------------------

def _void_implausible_attributes(df):
    """Impossible counts become missing. Clipping 50 bedrooms to 12 would assert
    the property has 12; missing says only that the field is wrong."""
    if "bedrooms" in df.columns:
        bad = (df["bedrooms"] > cfg.MAX_BEDROOMS).fillna(False)
        df.loc[bad, "bedrooms"] = np.nan

    if "bathrooms" in df.columns:
        # Two conditions, not one: above the cap *and* well above the bedroom
        # count. That catches the Geelong West studio listed with 12 bathrooms
        # while leaving the genuine nine-bed, nine-bath rooming houses to
        # _drop_per_room_pricing, where they belong.
        over_beds = (df["bathrooms"] > df["bedrooms"] + cfg.BATHROOM_BEDROOM_EXCESS
                     if "bedrooms" in df.columns else True)
        bad = ((df["bathrooms"] > cfg.MAX_BATHROOMS) & over_beds).fillna(False)
        df.loc[bad, "bathrooms"] = np.nan

    if "carspaces" in df.columns:
        bad = (df["carspaces"] > cfg.MAX_CARSPACES).fillna(False)
        df.loc[bad, "carspaces"] = np.nan

    return df


def _fill_structural_zeros(df):
    """Absent parking means no parking, not unknown parking: Domain omits the
    field when there is none. The indicator keeps an inferred zero
    distinguishable from a recorded one."""
    for col in cfg.FILL_ZERO_COLS:
        if col in df.columns:
            df[f"{col}_missing"] = df[col].isna().astype("int8")
            df[col] = df[col].fillna(0.0)
    return df


def _add_missing_indicators(df):
    # bedrooms, bathrooms and land_m2 keep their nulls for the model to impute;
    # the flag records that the value was absent rather than filled, since
    # absence may itself carry information.
    for col in cfg.MISSING_INDICATOR_COLS:
        flag = f"{col}_missing"
        if col in df.columns and flag not in df.columns:
            df[flag] = df[col].isna().astype("int8")
    return df


# ---------------------------------------------------------------------
# VINTAGE AND DUPLICATES
# ---------------------------------------------------------------------

def _drop_stale_listings(df):
    """Listings advertised too long before the snapshot to describe its market;
    one is dated 2008-01-15. The snapshot date is read from the data rather than
    hard-coded, so a refreshed scrape still works. Undated rows are kept."""
    if "date_listed" not in df.columns:
        return df
    listed = df["date_listed"]
    stale = ((listed.max() - listed).dt.days > cfg.MAX_LISTING_AGE_DAYS).fillna(False)
    return df[~stale].copy()


_STREET_ABBREV = {
    "AVE": "AVENUE", "ST": "STREET", "RD": "ROAD", "DR": "DRIVE", "CT": "COURT",
    "CRT": "COURT", "PL": "PLACE", "CRES": "CRESCENT", "PDE": "PARADE",
}


def _address_key(series):
    """'1/12-14 South Ave' and '1/12-14 South Avenue' to a common form."""
    s = series.astype("string").str.upper().str.strip()
    for short, long in _STREET_ABBREV.items():
        s = s.str.replace(rf"\b{short}\b", long, regex=True)
    return s.str.replace(r"[^A-Z0-9]+", " ", regex=True).str.strip()


def _drop_duplicate_properties(df):
    """One row per property, keeping its most recent listing.

    listing_id catches nothing: the same property is uploaded twice under
    consecutive ids and relisted months later under a new one. Both put
    identical rows on either side of a train/test split. Address-less rows are
    exempt, since they would collapse into one group.
    """
    if "address" not in df.columns:
        return df

    df["address_key"] = _address_key(df["address"])
    key = [c for c in cfg.DUPLICATE_KEY if c in df.columns]
    has_address = df["address_key"].notna() & (df["address_key"] != "")

    candidates = df[has_address]
    if "date_listed" in df.columns:
        candidates = candidates.sort_values("date_listed", na_position="first")
    older_copies = candidates.index[candidates.duplicated(subset=key, keep="last")]
    return df.drop(index=older_copies).copy()


# ---------------------------------------------------------------------
# GEOGRAPHY
# ---------------------------------------------------------------------

# Profiling found none outside: the apparent outliers are genuinely Mildura and
# Mallacoota. The check stays because a refreshed scrape could introduce some.
VIC_LAT = (-39.25, -33.95)
VIC_LON = (140.90, 150.05)


def _void_coordinates_outside_victoria(df):
    if not {"lat", "lon"}.issubset(df.columns):
        return df
    # Presence is tested first because a plain ~between() counts NaN as out of
    # range, which would turn missing coordinates into bad ones.
    present = df["lat"].notna() & df["lon"].notna()
    inside = df["lat"].between(*VIC_LAT) & df["lon"].between(*VIC_LON)
    df.loc[present & ~inside, ["lat", "lon"]] = np.nan
    return df


# ---------------------------------------------------------------------
# ENTRY POINT
# ---------------------------------------------------------------------

def clean_listings(df, drop_per_room_pricing=True):
    """Apply every rule above and return the cleaned table.

    Order matters: sentinels void before the target filter, so zeros count as
    withheld rather than implausibly low; attribute repair runs before dedupe,
    so a typo cannot split a duplicate pair into two properties.
    """
    if cfg.TARGET not in df.columns:
        raise KeyError(f"{cfg.TARGET!r} not in the input; is this the master table?")

    df = df.copy()
    df = _void_rent_sentinels(df)
    df = _drop_unusable_target(df)
    df = _drop_non_dwellings(df)
    df = _void_implausible_attributes(df)
    if drop_per_room_pricing:
        df = _drop_per_room_pricing(df)
    df = _drop_stale_listings(df)
    df = _drop_duplicate_properties(df)
    df = _void_coordinates_outside_victoria(df)
    df = _fill_structural_zeros(df)
    df = _add_missing_indicators(df)
    df = _flag_high_rent(df)
    return df.reset_index(drop=True)
