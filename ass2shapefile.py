import requests
import zipfile
import os
import geopandas as gpd
import matplotlib.pyplot as plt

url = "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files/SA2_2021_AUST_SHP_GDA2020.zip"

zip_path = "data/SA2_2021.zip"
extract_path = "data/SA2_2021"

# Make data folder
os.makedirs("data", exist_ok=True)

# Download only if not already downloaded
if not os.path.exists(zip_path):
    response = requests.get(url)
    response.raise_for_status()

    with open(zip_path, "wb") as file:
        file.write(response.content)

# Extract
if not os.path.exists(extract_path):
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_path)

# Load shapefile
sa2 = gpd.read_file(
    f"{extract_path}/SA2_2021_AUST_GDA2020.shp"
)

# Victoria only
vic_sa2 = sa2[sa2["STE_CODE21"] == "2"]

print(vic_sa2.head())
print(vic_sa2.columns)

# Plot Victoria SA2 boundaries
vic_sa2.plot(
    figsize=(10, 10),
    edgecolor="black"
)

plt.title("Victoria SA2 Boundaries")
plt.axis("off")
plt.show()