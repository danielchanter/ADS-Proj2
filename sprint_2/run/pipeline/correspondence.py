"""
Suburb (SAL), postcode (POA) and SA2 correspondence, built from ABS mesh blocks.

The three geographies do not nest: rents come per postcode (SQM), covariates per
SA2, and Q2 asks for suburbs. Every Victorian mesh block has exactly one suburb,
one postcode and one SA2, so summing Census 2021 dwellings over mesh blocks gives
the size of every suburb x postcode x SA2 intersection, and from that the share
of each area that falls in each of the others.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from .common import cached_download, record_source, suburb_key

# ---------------------------------------------------------------------
# SOURCES
# ---------------------------------------------------------------------

# ASGS Edition 3 allocation files: one row per mesh block, Australia-wide.
_ALLOCATION_BASE = (
    "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/"
    "edition-3-july-2021-june-2026/access-and-downloads/allocation-files/"
)
MB_URL = _ALLOCATION_BASE + "MB_2021_AUST.xlsx"
POA_URL = _ALLOCATION_BASE + "POA_2021_AUST.xlsx"
SAL_URL = _ALLOCATION_BASE + "SAL_2021_AUST.xlsx"

# Census 2021 dwellings and persons per mesh block. Victoria is split across
# two sheets because it does not fit in one.
MB_COUNTS_URL = (
    "https://www.abs.gov.au/census/guide-census-data/mesh-block-counts/2021/"
    "Mesh%20Block%20Counts%2C%202021.xlsx"
)
MB_COUNTS_VIC_SHEETS = ["Table 2", "Table 2.1"]

SOURCE_NAME = "ABS ASGS 2021 suburb/postcode/SA2 correspondence"

# ---------------------------------------------------------------------
# MESH BLOCKS
# ---------------------------------------------------------------------

def load_vic_mesh_blocks(cache_dir: Path):
    """
    One row per Victorian mesh block with its SA2, suburb, postcode and Census
    2021 dwelling and person counts.

    The four workbooks take about a minute to parse, so the joined Victorian
    table is cached as CSV. The non-spatial mesh blocks (no usual address,
    migratory, offshore and shipping) have no area and are dropped: they belong
    to no real suburb.
    """
    cached = cache_dir / "vic_mesh_blocks.csv"
    if cached.exists():
        return pd.read_csv(cached, dtype={"mb_code": str, "sa2_code_2021": str,
                                          "sal_code_2021": str, "postcode": str})

    def read(url, name, cols):
        path = cached_download(url, cache_dir / name)
        d = pd.read_excel(path, usecols=cols, dtype=str)
        # Victorian mesh block codes start with the state code, 2.
        return d[d["MB_CODE_2021"].str.fullmatch(r"2\d{10}", na=False)]

    mb = read(MB_URL, "abs_mb_2021.xlsx",
              ["MB_CODE_2021", "SA2_CODE_2021", "SA2_NAME_2021", "AREA_ALBERS_SQKM"])
    poa = read(POA_URL, "abs_poa_2021.xlsx", ["MB_CODE_2021", "POA_CODE_2021"])
    sal = read(SAL_URL, "abs_sal_2021.xlsx", ["MB_CODE_2021", "SAL_CODE_2021", "SAL_NAME_2021"])

    counts_path = cached_download(MB_COUNTS_URL, cache_dir / "abs_mb_counts_2021.xlsx")
    # Six title rows sit above the header.
    counts = pd.concat(
        pd.read_excel(counts_path, sheet_name=s, header=6, dtype={"MB_CODE_2021": str})
        for s in MB_COUNTS_VIC_SHEETS
    )
    counts = counts[counts["MB_CODE_2021"].str.fullmatch(r"2\d{10}", na=False)]

    d = (
        mb.merge(poa, on="MB_CODE_2021", how="left")
        .merge(sal, on="MB_CODE_2021", how="left")
        .merge(counts[["MB_CODE_2021", "Dwelling", "Person"]], on="MB_CODE_2021", how="left")
    )
    d = d.rename(columns={
        "MB_CODE_2021": "mb_code",
        "SA2_CODE_2021": "sa2_code_2021", "SA2_NAME_2021": "sa2_name_2021",
        "SAL_CODE_2021": "sal_code_2021", "SAL_NAME_2021": "sal_name_2021",
        "POA_CODE_2021": "postcode",
        "AREA_ALBERS_SQKM": "area_sqkm",
        "Dwelling": "dwellings", "Person": "persons",
    })
    for c in ["area_sqkm", "dwellings", "persons"]:
        d[c] = pd.to_numeric(d[c], errors="coerce")
    d = d[d["area_sqkm"].notna()].copy()
    d[["dwellings", "persons"]] = d[["dwellings", "persons"]].fillna(0)
    d.to_csv(cached, index=False)
    return d

# ---------------------------------------------------------------------
# CORRESPONDENCE
# ---------------------------------------------------------------------

def _share(d, area_col):
    """
    Each row's share of its `area_col` area, by 2021 dwellings.

    An area with no dwellings at all (123 suburbs: parks, industrial estates,
    reservoirs) falls back to land area, so its shares still sum to 1.
    """
    by_dwellings = d["dwellings"] / d.groupby(area_col)["dwellings"].transform("sum")
    by_area = d["area_sqkm"] / d.groupby(area_col)["area_sqkm"].transform("sum")
    return by_dwellings.fillna(by_area)

def build_correspondence(mesh_blocks: pd.DataFrame):
    """
    One row per suburb x postcode x SA2 intersection, with its 2021 dwellings,
    persons and area, and the share of each of the three areas it makes up.

    To move a value from one geography to another, weight by the share of the
    target: a suburb's SA2 covariate is sum(share_of_suburb * SA2 value) over
    the suburb's rows, and the same for postcode rents. Within one suburb (or
    postcode, or SA2) the shares sum to 1.
    """
    keys = ["sal_code_2021", "sal_name_2021", "postcode", "sa2_code_2021", "sa2_name_2021"]
    d = mesh_blocks.groupby(keys, as_index=False)[["dwellings", "persons", "area_sqkm"]].sum()
    d["share_of_suburb"] = _share(d, "sal_code_2021")
    d["share_of_postcode"] = _share(d, "postcode")
    d["share_of_sa2"] = _share(d, "sa2_code_2021")
    d = d.rename(columns={
        "dwellings": "dwellings_2021", "persons": "persons_2021",
    })
    return d.sort_values(["sal_code_2021", "postcode", "sa2_code_2021"]).reset_index(drop=True)

def load_correspondence(cache_dir: Path, coverage):
    """The correspondence table, or an empty one if the ABS files are unavailable."""
    try:
        corr = build_correspondence(load_vic_mesh_blocks(cache_dir))
    except Exception as e:
        print(f"WARNING: {SOURCE_NAME} failed: {e}")
        record_source(coverage, SOURCE_NAME, "failed", str(e))
        return pd.DataFrame(columns=["sal_code_2021", "sal_name_2021", "postcode", "sa2_code_2021"])
    record_source(
        coverage, SOURCE_NAME, "joined",
        f"suburb_postcode_sa2.csv: {len(corr):,} rows, "
        f"{corr['sal_code_2021'].nunique():,} suburbs, {corr['postcode'].nunique()} postcodes, "
        f"{corr['sa2_code_2021'].nunique()} SA2s",
    )
    return corr

# ---------------------------------------------------------------------
# LISTINGS
# ---------------------------------------------------------------------

def add_suburb_code(listings, corr, coverage):
    """
    Give each listing its ABS suburb (SAL) code, matched on (suburb, postcode).

    The name alone is not a key: fifteen Victorian suburb names are reused
    ("Hillside (Melton - Vic.)", "Hillside (East Gippsland - Vic.)"), and the
    postcode tells them apart. A listing whose Domain suburb is not an ABS
    suburb (Hidden Valley 3756, for one) gets nulls.
    """
    out = listings.copy()
    if corr.empty:
        out["sal_code_2021"] = pd.NA
        out["sal_name_2021"] = pd.NA
        return out

    pairs = corr[["sal_code_2021", "sal_name_2021", "postcode"]].drop_duplicates().copy()
    pairs["suburb_key"] = suburb_key(pairs["sal_name_2021"])
    # One SAL per (name, postcode) on the current data; keep the first in case
    # a future release breaks that.
    pairs = pairs.drop_duplicates(["suburb_key", "postcode"])

    left = pd.DataFrame({
        "suburb_key": suburb_key(out["suburb"]).to_numpy(),
        "postcode": out["postcode"].astype("string").to_numpy(),
    })
    # pairs is unique on the join keys, so the merge keeps listing order.
    matched = left.merge(pairs, on=["suburb_key", "postcode"], how="left")
    out["sal_code_2021"] = matched["sal_code_2021"].to_numpy()
    out["sal_name_2021"] = matched["sal_name_2021"].to_numpy()

    n = int(out["sal_code_2021"].notna().sum())
    record_source(
        coverage, f"{SOURCE_NAME} (listings)", "joined",
        f"sal_code_2021 on (suburb, postcode): {n:,}/{len(out):,} listings",
    )
    return out
