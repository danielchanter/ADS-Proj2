import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
script = HERE / "build_master_dataset.py"

# Pass everything straight through, so --no-osm, --no-sqm, --no-crime and the
# tolerance flags all work from here without this wrapper needing to know them.
cmd = [sys.executable, str(script), *sys.argv[1:]]

print("Building Victorian rental master dataset...")
subprocess.run(cmd, check=True)

print("\nDone.")
print("Final dataset:")
print(HERE.parent / "data" / "processed" / "vic_property_master.csv")
print("\nSource audit:")
print(HERE.parent / "data" / "processed" / "source_coverage.csv")
print("\nRent-derived columns (not for use as predictors):")
print(HERE.parent / "data" / "processed" / "vic_property_rent_ratios.csv")
