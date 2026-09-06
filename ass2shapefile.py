import geopandas as gpd

sa2 = gpd.read_file(
    "SA2_2026_AUST_SHP_GDA2020/SA2_2026_AUST_GDA2020.shp"
)

print(sa2.head())

vic_sa2 = sa2[sa2["STE_CODE26"] == "2"]

import matplotlib.pyplot as plt

vic_sa2.plot(
    figsize=(10, 10),
    edgecolor="black"
)

plt.show()