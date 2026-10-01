# preprocessing
```bash
python -m preprocessing 
```

Or from Python:

```python
from preprocessing import load_clean, select_features

clean = load_clean("sprint_3/vic_property_master.csv")
feats = select_features(clean) # which columns may be used as predictors
```

`config.py` holds every threshold and column role with the reason for each,
`clean.py` the row- and value-level rules, `features.py` the predictor
selection, `__main__.py` the entry point.

## What it does

12,717 listings in, **12,019 out** (94.5% kept), 117 columns, 67 of them usable
as predictors. Rows are dropped for four things only: an unusable rent (337 - no
price, a sale price in the rent field, or $18–$30 for a room), a listing more
than 365 days older than the snapshot (274), a duplicate property (65, matched
on normalised address rather than `listing_id`, which catches none), and a row
not priced per dwelling (22 — car spaces, vacant land, rooming houses).

A bad *feature* value never drops a row; it becomes missing, since capping 50
bedrooms at 12 asserts the property has 12. The one fill is `carspaces`, where
null means **none** rather than unknown and so is a structural zero.

Five columns are added: `address_key`, `rent_outlier_high`, and
`carspaces_missing` / `bedrooms_missing` / `bathrooms_missing`.

## Before modelling on the output

1. **Do not use `bond`.** It is 4.345 times the weekly rent on 83.5% of listings
   — Victorian law caps a bond at one month's rent — so a tree just divides by
   4.345. It takes 54% of total feature importance and the resulting R² restates
   bond law. `config.LEAKAGE_COLS` names it, the ratio columns, and
   `rent_outlier_high`, which is itself a threshold on the target.
2. **Do not select with `select_dtypes(include=np.number)`.** It selects on
   storage type, not meaning: it keeps `bond`, `listing_id` and the SA codes while
   discarding every categorical. Use `select_features()`, which excludes by role
   and gives a reason for every column it rejects.
3. **Group the split on `sa2_code_2021`.** Every external covariate is constant
   within an SA2, ~25 listings each, so a random split lets a model read an
   area's rent level off its training rows and be scored on its test rows.

Missing values and the two categoricals reach the CSV untouched: a fill or an
encoding has to be fit on training rows only, which makes it the model's job.
The target is not log-transformed either. `bond` and `days_listed` stay in the
CSV and are excluded at selection time, so a time-on-market model can take
`days_listed` deliberately.

