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
