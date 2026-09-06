import requests
import zipfile
import os
import geopandas as gpd
import matplotlib.pyplot as plt

url = "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-4-july-2026-june-2031/access-and-downloads/digital-boundary-files/SA2_2026_AUST_SHP_GDA2020.zip"

zip_path = "data/SA2_2026.zip"
extract_path = "data/SA2_2026"

# Make data folder and download:
os.makedirs("data", exist_ok=True)
response = requests.get(url)
response.raise_for_status()

with open(zip_path, "wb") as file:
    file.write(response.content)

with zipfile.ZipFile(zip_path, "r") as zip_ref:
    zip_ref.extractall(extract_path)

# Load shapefile
sa2 = gpd.read_file(
    f"{extract_path}/SA2_2026_AUST_GDA2020.shp"
)

# Victoria only
vic_sa2 = sa2[sa2["STE_CODE26"] == "2"]

print(vic_sa2.head())

vic_sa2.plot(
    figsize=(10, 10),
    edgecolor="black"
)

plt.show()