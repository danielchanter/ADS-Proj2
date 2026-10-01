"""Write vic_property_clean.csv.

    python -m preprocessing
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import load_clean

HERE = Path(__file__).resolve().parent
REPO = HERE.parent

DEFAULT_SOURCE = REPO / "sprint_3" / "vic_property_master.csv"
DEFAULT_OUT = HERE / "vic_property_clean.csv"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE,
                        help=f"the property master table (default: "
                             f"{DEFAULT_SOURCE.relative_to(REPO).as_posix()})")
    parser.add_argument("-o", "--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--keep-per-room", action="store_true",
                        help="keep the rooming-house rows, whose rent prices one room "
                             "rather than the dwelling")
    args = parser.parse_args(argv)

    if not args.source.exists():
        sys.exit(f"no master table at {args.source}; pass one as an argument")

    clean = load_clean(args.source, drop_per_room_pricing=not args.keep_per_room)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    clean.to_csv(args.out, index=False)
    print(f"{args.source.name} -> {args.out.name}: "
          f"{len(clean):,} listings, {clean.shape[1]} columns")


if __name__ == "__main__":
    main()
