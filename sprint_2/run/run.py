import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
script = HERE / "build_master_dataset.py"

print("Building Victorian rental master dataset...")
subprocess.run([sys.executable, str(script)], check=True)
print("\nDone.")
print("Final dataset:")
print(HERE.parent / "data" / "processed" / "vic_property_master.csv")
