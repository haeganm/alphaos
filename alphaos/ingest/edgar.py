"""Point-in-time fundamentals from SEC XBRL company facts.

Every XBRL fact carries the date it was *filed*. That single field is what
makes this dataset honest: a signal computed for 2015-03-01 may only use facts
filed strictly before 2015-03-01, regardless of which fiscal period they
describe. Joining on period_end instead -- the intuitive thing -- leaks roughly
one to three months of future information into every observation and will
manufacture alpha out of nothing.

Restatements are kept, not collapsed. A restated 2014 figure filed in 2016 is
a different row from the original filed in 2015, so an as-of join naturally
sees the number that was actually visible at the time.

Two shapes of fact, and mixing them is a real bug:
* duration (Revenues, NetIncomeLoss): has start+end. A 10-K value covers ~365
  days, a 10-Q value ~90. Comparing an annual figure to a quarterly one
  silently produces a 4x "growth" spike, so period length is recorded.
* instant (Assets, InventoryNet): end only.
"""
from __future__ import annotations

import json
import os
import zipfile
from datetime import date

import polars as pl

BULK_URL = "https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"
BULK_ZIP = "data/cache/companyfacts.zip"

# Canonical name -> XBRL tags in preference order. Companies switch tags over
# time (ASC 606 moved most filers off SalesRevenueNet in 2018), so a canonical
# series has to be stitched from aliases; alias_rank records which one supplied
# a given row so the panel can prefer the most specific available.
TAG_ALIASES: dict[str, list[str]] = {
    "Revenues": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
    ],
    "CostOfRevenue": ["CostOfGoodsAndServicesSold", "CostOfRevenue", "CostOfGoodsSold"],
    "InventoryNet": ["InventoryNet"],
    "NetIncomeLoss": ["NetIncomeLoss", "ProfitLoss"],
    "OperatingIncomeLoss": ["OperatingIncomeLoss"],
    "Assets": ["Assets"],
    "StockholdersEquity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
    ],
    "Cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ],
    "LongTermDebt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "AccountsReceivable": ["AccountsReceivableNetCurrent", "ReceivablesNetCurrent"],
    "OperatingCashFlow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
    ],
    "SharesOutstanding": ["CommonStockSharesOutstanding", "EntityCommonStockSharesOutstanding"],
}

# tag -> (canonical, rank)
_LOOKUP: dict[str, tuple[str, int]] = {}
for _canon, _tags in TAG_ALIASES.items():
    for _i, _t in enumerate(_tags):
        _LOOKUP.setdefault(_t, (_canon, _i))

_SCHEMA = {
    "cik": pl.Utf8, "tag": pl.Utf8, "xbrl_tag": pl.Utf8, "alias_rank": pl.Int8,
    "unit": pl.Utf8, "start": pl.Date, "end": pl.Date, "period_days": pl.Int32,
    "value": pl.Float64, "filed": pl.Date, "form": pl.Utf8, "accn": pl.Utf8,
    "fy": pl.Int32, "fp": pl.Utf8,
}


def download_bulk(dest: str = BULK_ZIP) -> str:
    from . import http
    if os.path.exists(dest) and os.path.getsize(dest) > 100 << 20:
        print(f"companyfacts.zip already cached ({os.path.getsize(dest) >> 20} MB)")
        return dest
    print("downloading companyfacts.zip (~1.3 GB)...")
    return http.download(BULK_URL, dest)


def _facts_from_json(cik: str, doc: dict) -> list[tuple]:
    rows = []
    facts = doc.get("facts", {})
    for ns in ("us-gaap", "dei"):
        for xtag, body in facts.get(ns, {}).items():
            hit = _LOOKUP.get(xtag)
            if hit is None:
                continue
            canon, rank = hit
            for unit, entries in body.get("units", {}).items():
                if unit not in ("USD", "shares"):
                    continue
                for e in entries:
                    filed = e.get("filed")
                    end = e.get("end")
                    val = e.get("val")
                    if filed is None or end is None or val is None:
                        continue
                    start = e.get("start")
                    try:
                        d_end = date.fromisoformat(end)
                        d_filed = date.fromisoformat(filed)
                        d_start = date.fromisoformat(start) if start else None
                    except ValueError:
                        continue
                    rows.append((
                        cik, canon, xtag, rank, unit, d_start, d_end,
                        (d_end - d_start).days if d_start else None,
                        float(val), d_filed, e.get("form"), e.get("accn"),
                        e.get("fy"), e.get("fp"),
                    ))
    return rows


def build(ciks: list[str], out_path: str = "data/fundamentals.parquet",
          zip_path: str = BULK_ZIP) -> pl.DataFrame:
    """Extract whitelisted facts for the given CIKs from the bulk archive."""
    download_bulk(zip_path)
    wanted = {c.zfill(10) for c in ciks if c}
    rows: list[tuple] = []
    found = missing = 0

    with zipfile.ZipFile(zip_path) as zf:
        names = set(zf.namelist())
        for i, cik in enumerate(sorted(wanted), 1):
            member = f"CIK{cik}.json"
            if member not in names:
                missing += 1
                continue
            try:
                with zf.open(member) as fh:
                    doc = json.load(fh)
            except (json.JSONDecodeError, zipfile.BadZipFile):
                missing += 1
                continue
            rows.extend(_facts_from_json(cik, doc))
            found += 1
            if i % 100 == 0:
                print(f"  {i}/{len(wanted)} CIKs, {len(rows):,} facts", flush=True)

    df = pl.DataFrame(rows, schema=_SCHEMA, orient="row")
    # Same fact restated identically in many filings: keep the earliest filing
    # that carried each distinct value, since that is when it became knowable.
    df = (df.sort("filed")
            .unique(subset=["cik", "tag", "xbrl_tag", "start", "end", "value"], keep="first")
            .sort(["cik", "tag", "end", "filed"]))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.write_parquet(out_path)
    print(f"fundamentals: {df.height:,} facts, {found} CIKs found, {missing} missing")
    return df


def quarterly(df: pl.DataFrame, tag: str, *, tol: int = 25) -> pl.DataFrame:
    """Duration facts covering roughly one quarter (~90 +/- tol days).

    Filtering by period length is what keeps a 10-K annual revenue figure from
    being compared against a 10-Q quarterly one.
    """
    return df.filter((pl.col("tag") == tag) &
                     pl.col("period_days").is_between(90 - tol, 90 + tol))


def instant(df: pl.DataFrame, tag: str) -> pl.DataFrame:
    """Balance-sheet facts: a level at a point in time, no duration."""
    return df.filter((pl.col("tag") == tag) & pl.col("period_days").is_null())


def annual(df: pl.DataFrame, tag: str, *, tol: int = 30) -> pl.DataFrame:
    return df.filter((pl.col("tag") == tag) &
                     pl.col("period_days").is_between(365 - tol, 365 + tol))
