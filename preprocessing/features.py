"""Which columns of the cleaned table may be used as predictors, and why not
for the rest.

Exclusion is by role, not by dtype. select_dtypes(np.number) keeps bond,
listing_id and the SA codes while discarding every categorical column.

select_features() returns the surviving column names plus a column -> reason
map for everything it rejected, so a column added upstream shows up with a
stated reason instead of disappearing. Imputing, encoding and scaling are
deliberately absent: those are fit on training rows only, so they belong to the
model pipeline rather than here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import config as cfg


@dataclass
class Features:
    numeric: list = field(default_factory=list)
    categorical: list = field(default_factory=list)
    rejected: dict = field(default_factory=dict)      # column -> reason

    @property
    def columns(self):
        return self.numeric + self.categorical


# ---------------------------------------------------------------------
# REDUNDANCY
# ---------------------------------------------------------------------

def _preference_rank(col):
    for i, pattern in enumerate(cfg.PREFER_PATTERNS):
        if re.search(pattern, col):
            return i
    return len(cfg.PREFER_PATTERNS)


def prune_vintages(columns, families=cfg.VINTAGE_FAMILIES):
    """Keep only the most recent year of each repeated-vintage family.

    Runs before prune_correlated, because a correlation chain cannot express
    "these five columns are the same measurement". Matching is on the exact
    stem plus a year, so erp_2024/erp_2025 are vintages of erp while
    erp_change_number_2024_25 is a different quantity.
    """
    dropped = {}
    for stem in families:
        members = {c: int(m.group(1)) for c in columns
                   if (m := re.fullmatch(re.escape(stem) + cfg.VINTAGE_SUFFIX, c))}
        if len(members) < 2:
            continue
        latest = max(members, key=members.get)
        dropped.update({c: latest for c in members if c != latest})
    return [c for c in columns if c not in dropped], dropped


def prune_correlated(df, columns, threshold=cfg.CORRELATION_THRESHOLD):
    """Keep one column per group of mutually correlated columns.

    Returns (kept, dropped), dropped mapping each discarded column to the one
    that displaced it. Columns are visited in preference order
    (config.PREFER_PATTERNS, then alphabetically), which is what makes the
    survivor meaningful rather than alphabetical: of erp_2025 and
    vif_population_2026, correlated at 0.97, the ABS estimate is preferred.
    """
    cols = [c for c in columns if c in df.columns]
    numeric = df[cols].select_dtypes(include=np.number)

    constant = [c for c in numeric.columns if numeric[c].nunique(dropna=True) <= 1]
    numeric = numeric.drop(columns=constant)

    corr = numeric.corr(numeric_only=True).abs()
    kept, dropped = [], {}
    for col in sorted(numeric.columns, key=lambda c: (_preference_rank(c), c)):
        clash = next((k for k in kept
                      if corr.loc[col, k] >= threshold and not np.isnan(corr.loc[col, k])), None)
        if clash is None:
            kept.append(col)
        else:
            dropped[col] = clash

    dropped.update({c: "constant" for c in constant})
    return [c for c in cols if c in kept], dropped


# ---------------------------------------------------------------------
# SELECTION
# ---------------------------------------------------------------------

_ROLE_EXCLUSIONS = (
    (cfg.LEAKAGE_COLS, "derived from weekly_rent (target leakage)"),
    (cfg.ID_COLS, "identifier, not a measurement"),
    (cfg.NAME_COLS, "area name, duplicates the joined SA2 covariates"),
    (cfg.POST_LISTING_COLS, "known only after listing / consequence of the price"),
    (cfg.TEXT_COLS, "free text, superseded by the feat_* flags"),
    (cfg.DUPLICATE_OF_PROPERTY_TYPE, "99.95% identical to property_type"),
)


def select_features(df, prune=True, drop_patterns=cfg.DROP_PATTERNS):
    """The predictor columns of a cleaned listing table, as a Features."""
    feats = Features()
    feats.rejected[cfg.TARGET] = "target"

    for columns, reason in _ROLE_EXCLUSIONS:
        for col in columns:
            if col in df.columns:
                feats.rejected[col] = reason

    for col in df.columns:
        if col in feats.rejected:
            continue
        reason = next((why for pattern, why in drop_patterns if re.search(pattern, col)), None)
        if reason:
            feats.rejected[col] = reason

    available = [c for c in df.columns if c not in feats.rejected]
    categorical = [c for c in cfg.CATEGORICAL_COLS if c in available]
    numeric = [c for c in available
               if c not in categorical and pd.api.types.is_numeric_dtype(df[c])]

    # Name anything nobody assigned a role to, so a column added upstream shows
    # up in the listing instead of disappearing.
    for col in available:
        if col not in categorical and col not in numeric:
            feats.rejected[col] = f"unhandled {df[col].dtype} column, no role assigned"

    for col in [c for c in numeric if df[c].isna().all()]:
        feats.rejected[col] = "entirely missing"
    numeric = [c for c in numeric if c not in feats.rejected]

    if prune:
        numeric, old = prune_vintages(numeric)
        for col, latest in old.items():
            feats.rejected[col] = f"superseded vintage of {latest}"

        kept, dropped = prune_correlated(df, numeric)
        for col, clash in dropped.items():
            feats.rejected[col] = ("constant" if clash == "constant" else
                                   f"correlated above {cfg.CORRELATION_THRESHOLD} with {clash}")
        # Nothing may leave the candidate list without a reason attached.
        for col in numeric:
            if col not in kept and col not in dropped:
                feats.rejected[col] = f"dropped in pruning, dtype {df[col].dtype} not comparable"
        numeric = kept

    feats.numeric, feats.categorical = numeric, categorical
    return feats
