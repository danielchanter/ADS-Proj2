"""
ABS and Victorian Government area-level sources, all on ASGS 2021 SA2s.

Snapshot tables (one row per SA2) are joined onto every listing; the yearly
population and income series go to sa2_yearly.csv for the forecasting model.
"""

from __future__ import annotations

import re
from pathlib import Path

import numpy as np
import pandas as pd

from .common import arcgis_geojson, cached_download, normalize_code, numeric

# ---------------------------------------------------------------------
# SOURCES
# ---------------------------------------------------------------------

SA2_URL = (
    "https://geo.abs.gov.au/arcgis/rest/services/ASGS2021/SA2/"
    "FeatureServer/0/query"
)

GCP_URL = (
    "https://services1.arcgis.com/vHnIGBHHqDR6y0CR/ArcGIS/rest/services/"
    "2021_ABS_General_Community_Profile/FeatureServer/4/query"
)

SEIFA_URL = (
    "https://geo.abs.gov.au/arcgis/rest/services/Hosted/"
    "ABS_Socio_Economic_Indexes_for_Areas_SEIFA_by_2021_SA2/"
    "FeatureServer/0/query"
)

REGIONAL_POP_URL = (
    "https://geo.abs.gov.au/arcgis/rest/services/Hosted/"
    "SA2_Regional_Population_2025/FeatureServer/3/query"
)

VIF_URL = (
    "https://www.planning.vic.gov.au/__data/assets/excel_doc/0028/691660/"
    "VIF2023_SA2_Pop_Hhold_Dwelling_Projections_to_2036_Release_2.xlsx"
)

# Population estimates by SA2, 30 June 2001 to 2025, on ASGS 2021 boundaries.
# The ArcGIS layer above only carries the latest two years; this is the history.
REGIONAL_POP_HISTORY_URL = (
    "https://www.abs.gov.au/statistics/people/population/regional-population/"
    "2024-25/32180DS0003_2001-25.xlsx"
)

# Personal Income in Australia, Table 1. Each release covers five financial
# years. All three below use ASGS 2021 SA2 codes, so stacking them extends the
# series back to 2016-17; the 2019-20 release and earlier use 2016 boundaries.
# Newest first: where releases overlap, the newest (revised) figure is kept.
_PERSONAL_INCOME_BASE = (
    "https://www.abs.gov.au/statistics/labour/earnings-and-working-conditions/"
    "personal-income-australia/"
)
_PERSONAL_INCOME_TABLE = (
    "Table%201%20-%20Total%20income%2C%20earners%20and%20summary%20statistics%20"
    "by%20geography%2C%20{first}%20to%20{release}.xlsx"
)
PERSONAL_INCOME_RELEASES = [
    (release, _PERSONAL_INCOME_BASE + release + "/" +
     _PERSONAL_INCOME_TABLE.format(first=first, release=release))
    for release, first in [
        ("2022-23", "2018-19"),
        ("2021-22", "2017-18"),
        ("2020-21", "2016-17"),
    ]
]
PERSONAL_INCOME_SNAPSHOT_YEAR = "2022-23"

# Personal Income Table 1 measures, in workbook order, and their column names.
PERSONAL_INCOME_MEASURES = {
    "earners": "income_earners",
    "median age": "income_earners_median_age",
    "sum": "income_sum",
    "median": "income_median",
    "mean": "income_mean",
}

# ---------------------------------------------------------------------
# SNAPSHOT LOADERS (one row per SA2)
# ---------------------------------------------------------------------

def load_sa2():
    # State code 2 = Victoria.
    g = arcgis_geojson(
        SA2_URL,
        where="state_code_2021='2'",
        # Names only above SA2: each SA3/SA4/GCCSA code maps 1:1 onto its name.
        out_fields=(
            "sa2_code_2021,sa2_name_2021,sa3_name_2021,sa4_name_2021,"
            "gccsa_name_2021,area_albers_sqkm"
        ),
    )
    g["sa2_code_2021"] = normalize_code(g["sa2_code_2021"])
    return g

def load_gcp():
    # The public SA2 service contains selected General Community Profile variables.
    fields = [
        "SA2_CODE_2021","SA2_NAME_2021","Tot_P_P",
        "Age_0_4_yr_P","Age_5_14_yr_P","Age_15_19_yr_P","Age_20_24_yr_P",
        "Age_25_34_yr_P","Age_35_44_yr_P","Age_45_54_yr_P","Age_55_64_yr_P",
        "Age_65_74_yr_P","Age_75_84_yr_P","Age_85ov_P",
        "Birthplace_Australia_P","Birthplace_Elsewhere_P",
        "Lang_used_home_Eng_only_P","Lang_used_home_Oth_Lang_P",
        "Median_age_persons","Median_mortgage_repay_monthly",
        "Median_tot_prsnl_inc_weekly","Median_rent_weekly",
        "Median_tot_fam_inc_weekly","Median_tot_hhd_inc_weekly",
        "Average_household_size",
    ]
    d = arcgis_geojson(GCP_URL, out_fields=",".join(fields), return_geometry=False)
    d = d.rename(columns={"SA2_CODE_2021":"sa2_code_2021","SA2_NAME_2021":"sa2_name_2021_gcp"})
    d["sa2_code_2021"] = normalize_code(d["sa2_code_2021"])
    # Retain Victoria only through SA2 code prefix.
    d = d[d["sa2_code_2021"].str.startswith("2")].copy()

    tot = numeric(d["Tot_P_P"]).replace(0, np.nan)
    d["age_0_14_pct"] = 100*(numeric(d["Age_0_4_yr_P"]) + numeric(d["Age_5_14_yr_P"])) / tot
    d["age_25_44_pct"] = 100*(numeric(d["Age_25_34_yr_P"]) + numeric(d["Age_35_44_yr_P"])) / tot
    d["age_65_plus_pct"] = 100*(numeric(d["Age_65_74_yr_P"]) + numeric(d["Age_75_84_yr_P"]) + numeric(d["Age_85ov_P"])) / tot
    d["born_overseas_pct"] = 100*numeric(d["Birthplace_Elsewhere_P"]) / (
        numeric(d["Birthplace_Australia_P"]) + numeric(d["Birthplace_Elsewhere_P"])
    ).replace(0, np.nan)
    d["non_english_home_pct"] = 100*numeric(d["Lang_used_home_Oth_Lang_P"]) / (
        numeric(d["Lang_used_home_Eng_only_P"]) + numeric(d["Lang_used_home_Oth_Lang_P"])
    ).replace(0, np.nan)

    # Tot_P_P is only the denominator above: erp_2025 is the current population.
    keep = [
        "sa2_code_2021","Median_age_persons",
        "Median_tot_prsnl_inc_weekly","Median_tot_hhd_inc_weekly",
        "Median_rent_weekly","Median_mortgage_repay_monthly",
        "Average_household_size","age_0_14_pct","age_25_44_pct",
        "age_65_plus_pct","born_overseas_pct","non_english_home_pct"
    ]
    return d[keep].rename(columns={
        "Median_age_persons":"census_median_age",
        "Median_tot_prsnl_inc_weekly":"census_median_personal_income_weekly",
        "Median_tot_hhd_inc_weekly":"census_median_household_income_weekly",
        "Median_rent_weekly":"census_median_rent_weekly",
        "Median_mortgage_repay_monthly":"census_median_mortgage_monthly",
        "Average_household_size":"census_avg_household_size",
    })

def load_seifa():
    # Scores only: the national deciles and percentiles are rank transforms of
    # them (r >= 0.95) and add nothing a model can use.
    fields = "sa2_code_2021,irsad_score,irsd_score,ier_score,ieo_score"
    d = arcgis_geojson(SEIFA_URL, out_fields=fields, return_geometry=False)
    d["sa2_code_2021"] = normalize_code(d["sa2_code_2021"])
    return d[d["sa2_code_2021"].str.startswith("2")].copy()

def load_regional_population():
    # erp_2024, the change in persons, and births/deaths/natural increase all
    # restate erp_2025 and its growth rate; the full ERP history is in sa2_yearly.
    fields = (
        "sa2_code_2021,erp_2025,erp_change_per_cent_2024_25,"
        "pop_density_2025_people_per_km2,"
        "net_internal_migration_2024_25,net_overseas_migration_2024_25"
    )
    d = arcgis_geojson(
        REGIONAL_POP_URL,
        where="state_code_2021='2'",
        out_fields=fields,
        return_geometry=False,
    )
    d["sa2_code_2021"] = normalize_code(d["sa2_code_2021"])
    return d.rename(columns={
        "erp_change_per_cent_2024_25":"population_growth_pct_2024_25",
        "pop_density_2025_people_per_km2":"population_density_2025",
    })

def load_vif(cache_dir: Path):
    """
    Projected population growth per SA2 from the VIF 2023 Total_Population sheet.

    Only that sheet is read. The workbook's other sheets (dwellings, households
    and their breakdowns) share the same year headers, so reading them together
    and matching on "2036" mixes household counts into the population figures.
    """
    path = cached_download(VIF_URL, cache_dir / "vif_sa2.xlsx")
    raw = pd.read_excel(path, sheet_name="Total_Population", header=None)
    # The header row holds the SA2 code label and the projection years.
    header_i = next(
        (i for i in range(min(len(raw), 30))
         if any("sa2" in str(x).lower() for x in raw.iloc[i])
         and any(str(x).startswith("2036") for x in raw.iloc[i])),
        None,
    )
    if header_i is None:
        print("WARNING: VIF workbook layout not recognised; VIF columns skipped.")
        return pd.DataFrame(columns=["sa2_code_2021"])

    d = pd.read_excel(path, sheet_name="Total_Population", header=header_i)
    d.columns = [str(c).strip().removesuffix(".0") for c in d.columns]
    code = next(c for c in d.columns if "sa2" in c.lower() and "code" in c.lower())
    d["sa2_code_2021"] = normalize_code(d[code])
    d = d[d["sa2_code_2021"].str.fullmatch(r"2\d{8}")].copy()

    # Only the projected change leaves this function: the projected levels are
    # near-copies of erp_2025. 2026-31 matches the five-year horizon the brief
    # asks about; 2026-36 correlates with it at 0.99 so adds nothing.
    out = d[["sa2_code_2021"]].copy()
    start = numeric(d["2026"]).replace(0, np.nan)
    out["vif_population_growth_pct_2026_31"] = 100 * (numeric(d["2031"]) / start - 1)
    return out

# ---------------------------------------------------------------------
# YEARLY SERIES (one row per SA2-year)
# ---------------------------------------------------------------------

def load_regional_population_yearly(cache_dir: Path):
    """
    Estimated resident population per Victorian SA2 at 30 June, 2001 onwards,
    in long format (one row per SA2-year) with the year-on-year growth.

    Header rows in the workbook: the years sit on one row and the SA2 code
    label on the row below, so both are read and combined by position.
    """
    path = cached_download(REGIONAL_POP_HISTORY_URL, cache_dir / "regional_population_2001_25.xlsx")
    raw = pd.read_excel(path, sheet_name="Table 1", header=None)

    label_row = next(
        i for i in range(min(len(raw), 15))
        if any(str(x).strip().lower() == "sa2 code" for x in raw.iloc[i])
    )
    labels = [str(x).strip().lower() for x in raw.iloc[label_row]]
    years = raw.iloc[label_row - 1]
    code_col = labels.index("sa2 code")
    year_cols = {
        j: int(y) for j, y in years.items()
        if re.fullmatch(r"\d{4}", str(y).strip().removesuffix(".0"))
    }

    body = raw.iloc[label_row + 1:]
    codes = normalize_code(body[code_col])
    body = body[codes.str.fullmatch(r"2\d{8}")]

    wide = body[list(year_cols)].rename(columns=year_cols)
    wide.insert(0, "sa2_code_2021", normalize_code(body[code_col]))
    d = wide.melt(id_vars="sa2_code_2021", var_name="year", value_name="erp")
    d["year"] = d["year"].astype(int)
    d["erp"] = pd.to_numeric(d["erp"], errors="coerce")
    d = d.sort_values(["sa2_code_2021", "year"]).reset_index(drop=True)
    d["erp_growth_pct"] = 100 * d.groupby("sa2_code_2021")["erp"].pct_change()
    return d

def read_personal_income_sa2(path: Path, release: str):
    """
    Table 1.4 (SA2) of one Personal Income release, as one row per SA2 and
    financial year.

    The header spans two rows: the measure name (Earners, Median ($), ...) is
    written once above its block of five year columns, and the years are on
    the row below. The measure is forward-filled across its block.
    """
    raw = pd.read_excel(path, sheet_name="Table 1.4", header=None)
    year_row = next(
        i for i in range(min(len(raw), 15))
        if str(raw.iat[i, 0]).strip().upper() == "SA2"
    )
    measures = raw.iloc[year_row - 1].ffill()
    years = raw.iloc[year_row]

    body = raw.iloc[year_row + 1:]
    codes = normalize_code(body[0])
    body = body[codes.str.fullmatch(r"2\d{8}")]

    frames = []
    for j in range(2, raw.shape[1]):
        label = str(measures[j]).strip().lower()
        key = next((k for k in PERSONAL_INCOME_MEASURES if label.startswith(k)), None)
        if key is None or not re.fullmatch(r"\d{4}-\d{2}", str(years[j]).strip()):
            continue
        frames.append(pd.DataFrame({
            "sa2_code_2021": normalize_code(body[0]).to_numpy(),
            "financial_year": str(years[j]).strip(),
            "measure": PERSONAL_INCOME_MEASURES[key],
            # Values are text with thousands separators; "np" (not published)
            # becomes NaN.
            "value": pd.to_numeric(
                body[j].astype(str).str.replace(",", "", regex=False), errors="coerce"
            ).to_numpy(),
        }))

    d = pd.concat(frames, ignore_index=True).pivot_table(
        index=["sa2_code_2021", "financial_year"], columns="measure",
        values="value", aggfunc="first", dropna=False,
    ).reset_index()
    d.columns.name = None
    d["income_release"] = release
    return d

def rescale_to_newer(older: pd.DataFrame, newer: pd.DataFrame):
    """
    Put an older release on the same basis as the newer ones before stacking.

    ABS revised the series in the 2021-22 release: for the same years, the
    2020-21 release has ~8% different earner counts and ~4% different medians,
    while 2021-22 and 2022-23 agree exactly. Each SA2's older figures are
    multiplied by the median newer/older ratio over the years both cover, so
    the years only the older release has do not add a false step to the series.
    Median age is left as is. Releases that already agree get a ratio of 1.
    """
    scaled = [m for m in PERSONAL_INCOME_MEASURES.values() if m != "income_earners_median_age"]
    both = older.merge(newer, on=["sa2_code_2021", "financial_year"], suffixes=("", "_new"))
    ratios = pd.DataFrame({"sa2_code_2021": both["sa2_code_2021"]})
    for m in scaled:
        ratios[m] = both[f"{m}_new"] / both[m]
    ratios = ratios.replace([np.inf, -np.inf], np.nan).groupby("sa2_code_2021").median()

    out = older.set_index("sa2_code_2021")
    factor = ratios.reindex(out.index)
    if (factor.fillna(1) - 1).abs().to_numpy().max() > 1e-9:
        out[scaled] = out[scaled] * factor[scaled]
        out["income_release"] = out["income_release"] + " (rescaled)"
    return out.reset_index()

def load_personal_income_yearly(cache_dir: Path):
    """
    SA2 personal income from every release in PERSONAL_INCOME_RELEASES,
    one row per SA2 and financial year. `year` is the calendar year the
    financial year ends in, so 2022-23 lines up with ERP at 30 June 2023.
    """
    frames = []
    for release, url in PERSONAL_INCOME_RELEASES:
        path = cached_download(url, cache_dir / f"personal_income_{release}.xlsx")
        frames.append(read_personal_income_sa2(path, release))

    # Releases are newest first, so keep="first" keeps the revised figure.
    d = frames[0]
    for older in frames[1:]:
        d = pd.concat([d, rescale_to_newer(older, d)], ignore_index=True)
    d = d.drop_duplicates(["sa2_code_2021", "financial_year"], keep="first")
    d["year"] = d["financial_year"].str[:4].astype(int) + 1
    cols = ["sa2_code_2021", "year", "financial_year", *PERSONAL_INCOME_MEASURES.values(), "income_release"]
    return d[cols].sort_values(["sa2_code_2021", "year"]).reset_index(drop=True)

def personal_income_snapshot(income_yearly: pd.DataFrame, financial_year=PERSONAL_INCOME_SNAPSHOT_YEAR):
    """One financial year of SA2 income, in the wide shape the SA2 master uses."""
    if income_yearly.empty:
        return pd.DataFrame(columns=["sa2_code_2021"])
    suffix = financial_year.replace("-", "_")
    d = income_yearly[income_yearly["financial_year"] == financial_year]
    return d[["sa2_code_2021", "income_median", "income_mean", "income_earners"]].rename(columns={
        "income_median": f"tax_median_total_personal_income_{suffix}",
        "income_mean": f"tax_mean_total_personal_income_{suffix}",
        "income_earners": f"tax_income_earners_{suffix}",
    })

def build_sa2_yearly(population_yearly: pd.DataFrame, income_yearly: pd.DataFrame):
    """
    Time-varying SA2 covariates for the forecasting model, one row per SA2-year.
    Kept apart from the listing table, which is a single snapshot.
    """
    frames = [d for d in (population_yearly, income_yearly) if not d.empty]
    if not frames:
        return pd.DataFrame(columns=["sa2_code_2021", "year"])
    out = frames[0]
    for d in frames[1:]:
        out = out.merge(d, on=["sa2_code_2021", "year"], how="outer")
    return out.sort_values(["sa2_code_2021", "year"]).reset_index(drop=True)
