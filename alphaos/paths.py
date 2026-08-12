"""Every filesystem location, resolved absolutely from this file.

The scheduled task runs `pythonw.exe` with no working directory, so its cwd is
`C:\\Windows\\System32`. Relative literals like "data/panel.parquet" resolved to
nothing there, and the nightly job died before it could write the log that would
have explained why. Worse than crashing: a cwd-relative path can also *succeed*
in the wrong place, silently splitting the registry from the data it indexes.

registry.py already derived its own path from __file__ and was the only module
immune. This generalises that.

Callers may still override any path (tests pass tmp_path); these are the
defaults, and they are absolute.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
CACHE = DATA / "cache"
EMBARGO = DATA / "embargo"
CAMPAIGNS = ROOT / "campaigns"
REGISTRY_EXPORT = ROOT / "registry_export"

# artifacts
REGISTRY_DB = DATA / "alphaos.duckdb"
UNIVERSE = DATA / "universe.parquet"
PRICES = DATA / "prices.parquet"
FUNDAMENTALS = DATA / "fundamentals.parquet"
EVENTS = DATA / "events.parquet"
INSIDERS = DATA / "insiders.parquet"
FORM4_META = DATA / "form4_meta.parquet"
FACTORS = DATA / "factors.parquet"
PANEL = DATA / "panel.parquet"
EMBARGO_PANEL = EMBARGO / "panel.parquet"
DATA_VERSION = DATA / "data_version.json"
CONVICTION_SHEET = DATA / "conviction_sheet.md"
NIGHTLY_LOG = DATA / "nightly.log"

# caches
COMPANYFACTS_ZIP = CACHE / "companyfacts.zip"
WIKI_SNAPSHOTS = CACHE / "wiki_snapshots.json"
PRICE_CACHE = CACHE / "prices"
INSIDER_CACHE = CACHE / "insiders"


def ensure_dirs() -> None:
    for d in (DATA, CACHE, EMBARGO, PRICE_CACHE, INSIDER_CACHE, CAMPAIGNS,
              REGISTRY_EXPORT):
        os.makedirs(d, exist_ok=True)
