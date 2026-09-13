# Sprint 2 — Victorian Rental Data Pipeline

This folder contains the Sprint 2 data pipeline for building the enriched
Victorian rental-property dataset.

## Structure

```
sprint_2/
├── run/
│   ├── run.py
│   └── build_master_dataset.py
├── data/
│   ├── raw/
│   │   └── vic_rentals_all.csv
│   └── processed/
├── requirements.txt
├── .gitignore
└── README.md
```

## Run from the repository root

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r sprint_2/requirements.txt
python sprint_2/run/run.py
```

You can also run it while inside `sprint_2`:

```bash
python run/run.py
```

## Output

The final enriched dataset is written to:

```
sprint_2/data/processed/vic_property_master.csv
```

Additional outputs:
- `sa2_master.csv`
- `source_coverage.csv`

The script uses paths relative to the `sprint_2` folder, so it does not matter
what your terminal's current working directory is when you run it.
