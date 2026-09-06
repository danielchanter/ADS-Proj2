import requests
import zipfile
import os
import geopandas as gpd
import matplotlib.pyplot as plt

url = "https://www.abs.gov.au/statistics/standards/australian-statistical-geography-standard-asgs/edition-3-july-2021-june-2026/access-and-downloads/digital-boundary-files/SA2_2021_AUST_SHP_GDA2020.zip"

<<<<<<< HEAD
zip_path = "data/SA2_2026.zip"
extract_path = "data/SA2_2026"
shp_path = f"{extract_path}/SA2_2026_AUST_GDA2020.shp"

# Make data folder and download (skip if we already have it):
os.makedirs("data", exist_ok=True)

if not os.path.exists(zip_path):
    print("Downloading SA2 boundaries (~50MB)...")
    response = requests.get(url, stream=True)
    response.raise_for_status()

    with open(zip_path, "wb") as file:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            file.write(chunk)
else:
    print(f"Using existing {zip_path}")

if not os.path.exists(shp_path):
    print("Extracting...")
=======
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
>>>>>>> 03ee0ed58f892ecebe9b9ed780adc62be5781727
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        zip_ref.extractall(extract_path)

# Load shapefile
<<<<<<< HEAD
sa2 = gpd.read_file(shp_path)
=======
sa2 = gpd.read_file(
    f"{extract_path}/SA2_2021_AUST_GDA2020.shp"
)
>>>>>>> 03ee0ed58f892ecebe9b9ed780adc62be5781727

# Victoria only
vic_sa2 = sa2[sa2["STE_CODE21"] == "2"]

print(vic_sa2.head())
print(vic_sa2.columns)

# Plot Victoria SA2 boundaries
vic_sa2.plot(
    figsize=(10, 10),
    edgecolor="black"
)

<<<<<<< HEAD
plt.show()
=======
plt.title("Victoria SA2 Boundaries")
plt.axis("off")
plt.show()
>>>>>>> 03ee0ed58f892ecebe9b9ed780adc62be5781727
