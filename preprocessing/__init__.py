from __future__ import annotations

from . import config
from .clean import clean_listings, load_master
from .features import (
    Features, prune_correlated, prune_vintages, select_features,
)

__all__ = [
    "config", "Features", "clean_listings", "load_clean", "load_master",
    "prune_correlated", "prune_vintages", "select_features",
]


def load_clean(path, **kwargs):
    """Read the master table and clean it in one call."""
    return clean_listings(load_master(path), **kwargs)
