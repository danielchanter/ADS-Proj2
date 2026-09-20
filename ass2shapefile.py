import requests
import zipfile
import os
import geopandas as gpd
import matplotlib.pyplot as plt
import pandas as pd

shape_url = (
    "https://www.abs.gov.au/statistics/standards/"
    "australian-statistical-geography-standard-asgs/"
    "edition-3-july-2021-june-2026/access-and-downloads/"
    "digital-boundary-files/SA2_2021_AUST_SHP_GDA2020.zip"
)

population_url = (
    "https://www.abs.gov.au/statistics/people/population/"
    "regional-population/2024-25/32180DS0003_2001-25.xlsx"
)

zip_path = "data/SA2_2021.zip"
extract_path = "data/SA2_2021"
shp_path = f"{extract_path}/SA2_2021_AUST_GDA2020.shp"

pop_file = "data/sa2_population_2001_2025.xlsx"

os.makedirs("data", exist_ok=True)

if not os.path.exists(zip_path):
    print("Downloading SA2 boundaries...")

    response = requests.get(shape_url, stream=True)
    response.raise_for_status()

    with open(zip_path, "wb") as file:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            file.write(chunk)

else:
    print(f"Using existing {zip_path}")

if not os.path.exists(shp_path):
    print("Extracting SA2 boundaries...")

    os.makedirs(extract_path, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_path)

else:
    print("SA2 boundaries already extracted")

sa2 = gpd.read_file(shp_path)

# Keep Victoria only
vic_sa2 = sa2[
    sa2["STE_CODE21"] == "2"
].copy()

print("\nVictoria SA2 data:")
print(vic_sa2.head())

if not os.path.exists(pop_file):
    print("\nDownloading ABS population data...")

    response = requests.get(population_url, stream=True)
    response.raise_for_status()

    with open(pop_file, "wb") as file:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            file.write(chunk)

else:
    print(f"\nUsing existing {pop_file}")

pop = pd.read_excel(
    pop_file,
    sheet_name="Table 1",
    header=None,
    skiprows=6
)

# Columns:
# 8  = SA2 code
# 9  = SA2 name
# 34 = 2025 population
pop = pop.iloc[:, [8, 9, 34]]

pop.columns = [
    "SA2_CODE21",
    "SA2_NAME_POP",
    "population_2025"
]

vic_sa2["SA2_CODE21"] = (
    vic_sa2["SA2_CODE21"]
    .astype(str)
    .str.strip()
)

pop["SA2_CODE21"] = (
    pop["SA2_CODE21"]
    .astype(str)
    .str.replace(".0", "", regex=False)
    .str.strip()
)

vic_sa2 = vic_sa2.merge(
    pop[
        [
            "SA2_CODE21",
            "population_2025"
        ]
    ],
    on="SA2_CODE21",
    how="left"
)

print("\nMerged Victoria SA2 population data:")

print(
    vic_sa2[
        [
            "SA2_CODE21",
            "SA2_NAME21",
            "population_2025"
        ]
    ].head(20)
)

print(
    "\nMissing population values:",
    vic_sa2["population_2025"].isna().sum()
)

fig, ax = plt.subplots(figsize=(10, 10))

vic_sa2.plot(
    ax=ax,
    column="population_2025",
    legend=True,
    edgecolor="black",
    linewidth=0.2
)

ax.set_title(
    "Estimated Resident Population by SA2 - Victoria, 2025"
)

ax.axis("off")

plt.tight_layout()
plt.show()

## INCOME BY SA2 AREA

import glob

income_url = (
    "https://www.abs.gov.au/census/find-census-data/datapacks/download/"
    "2021_GCP_SA2_for_VIC_short-header.zip"
)

income_zip = "data/SA2_income_2021_VIC.zip"
income_extract = "data/SA2_income_2021_VIC"

if not os.path.exists(income_zip):
    print("\nDownloading 2021 Census SA2 income data...")

    response = requests.get(income_url, stream=True)
    response.raise_for_status()

    with open(income_zip, "wb") as file:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            file.write(chunk)

    print("Income download complete.")

else:
    print(f"\nUsing existing {income_zip}")

if not os.path.exists(income_extract):
    print("Extracting income data...")

    os.makedirs(income_extract, exist_ok=True)

    with zipfile.ZipFile(income_zip, "r") as zip_ref:
        zip_ref.extractall(income_extract)

    print("Income data extracted.")

else:
    print("Income data already extracted")

g02_files = glob.glob(
    f"{income_extract}/**/*G02*.csv",
    recursive=True
)

if len(g02_files) == 0:
    raise FileNotFoundError("Could not find G02 Census file")

income_file = g02_files[0]

print("\nIncome file:")
print(income_file)

income = pd.read_csv(income_file)

print("\nIncome columns:")
print(income.columns.tolist())

income = income.rename(
    columns={
        "SA2_CODE_2021": "SA2_CODE21"
    }
)

income["SA2_CODE21"] = (
    income["SA2_CODE21"]
    .astype(str)
    .str.replace(".0", "", regex=False)
    .str.strip()
)

income_clean = income[
    [
        "SA2_CODE21",
        "Median_tot_prsnl_inc_weekly"
    ]
].copy()

income_clean = income_clean.rename(
    columns={
        "Median_tot_prsnl_inc_weekly":
        "median_weekly_personal_income_2021"
    }
)

income_clean["median_weekly_personal_income_2021"] = pd.to_numeric(
    income_clean["median_weekly_personal_income_2021"],
    errors="coerce"
)

vic_sa2 = vic_sa2.merge(
    income_clean,
    on="SA2_CODE21",
    how="left"
)

print("\nVictoria SA2 population + income:")

print(
    vic_sa2[
        [
            "SA2_CODE21",
            "SA2_NAME21",
            "population_2025",
            "median_weekly_personal_income_2021"
        ]
    ].head(20)
)

print(
    "\nMissing income values:",
    vic_sa2["median_weekly_personal_income_2021"].isna().sum()
)

fig, ax = plt.subplots(figsize=(10, 10))

vic_sa2.plot(
    ax=ax,
    column="median_weekly_personal_income_2021",
    legend=True,
    edgecolor="black",
    linewidth=0.2
)

ax.set_title(
    "Median Weekly Personal Income by SA2 - Victoria, 2021"
)

ax.axis("off")

plt.tight_layout()
plt.show()