import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
script = HERE / "build_master_dataset.py"

cmd = [sys.executable, str(script)]
if "--no-osm" in sys.argv:
    cmd.append("--no-osm")

print("Building Victorian rental master dataset...")
subprocess.run(cmd, check=True)

print("\nDone.")
print("Final dataset:")
print(HERE.parent / "data" / "processed" / "vic_property_master.csv")
print("\nSource audit:")
print(HERE.parent / "data" / "processed" / "source_coverage.csv")
print(HERE.parent / "data" / "processed" / "extra_source_manifest.csv")
