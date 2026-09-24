import argparse
import subprocess
import sys
from pathlib import Path

from pipeline.common import OUTPUT_DIR

HERE = Path(__file__).resolve().parent
script = HERE / "build_master_dataset.py"

# Pass everything straight through, so --no-osm, --no-sqm, --no-crime and the
# tolerance flags all work from here without this wrapper needing to know them.
# Only --output-dir is read here, so the paths printed below are the real ones.
ap = argparse.ArgumentParser(add_help=False)
ap.add_argument("--output-dir", default=str(OUTPUT_DIR))
outdir = Path(ap.parse_known_args()[0].output_dir)

cmd = [sys.executable, str(script), *sys.argv[1:]]

print("Building Victorian rental master dataset...")
subprocess.run(cmd, check=True)

print("\nDone.")
print("Final dataset:")
print(outdir / "vic_property_master.csv")
print("\nSource audit:")
print(outdir / "source_coverage.csv")
print("\nRent-derived columns (not for use as predictors):")
print(outdir / "vic_property_rent_ratios.csv")
