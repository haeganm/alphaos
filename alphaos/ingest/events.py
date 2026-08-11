"""Filing events with the instant they became knowable.

For an event study the only defensible timestamp is acceptanceDateTime -- the
moment EDGAR accepted the document and the public could read it. filingDate is
a calendar day with no clock, and a filing accepted at 20:30 ET is not tradable
at that day's close. AsOf's grading layer encodes the same rule; harness.py
ports it.

8-K item codes are the event vocabulary:
  2.02 results of operations (the earnings press release -- days ahead of the
       10-Q, which is why PEAD is measured from here and not from the 10-Q)
  1.01 material agreement      2.01 completed acquisition
  5.02 officer departure/appointment   4.01 auditor change
"""
from __future__ import annotations

import os
import sqlite3

import polars as pl

from . import http

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ASOF_DB = r"C:\AsOf\data\asof.db"
FORMS = {"8-K", "8-K/A", "10-K", "10-Q"}

_SCHEMA = {
    "cik": pl.Utf8, "form": pl.Utf8, "items": pl.Utf8,
    "filing_date": pl.Date, "accepted_utc": pl.Utf8,
    "report_date": pl.Date, "accn": pl.Utf8,
}


def _rows_from_block(cik: str, block: dict) -> list[tuple]:
    cols = ("accessionNumber", "filingDate", "reportDate", "acceptanceDateTime",
            "form", "items")
    if not all(c in block for c in ("accessionNumber", "form", "filingDate")):
        return []
    n = len(block["accessionNumber"])
    get = lambda c, i: (block.get(c) or [None] * n)[i]  # noqa: E731
    out = []
    for i in range(n):
        form = get("form", i)
        if form not in FORMS:
            continue
        out.append((
            cik, form, get("items", i) or "",
            get("filingDate", i), get("acceptanceDateTime", i),
            get("reportDate", i) or None, get("accessionNumber", i),
        ))
    return out


def fetch_filings(cik: str) -> list[tuple]:
    """All 8-K/10-K/10-Q filings for one CIK, including the paged history.

    filings.recent holds only the last ~1000 documents; anything older lives in
    the files listed under filings.files, which is most of the 2010-2018 span
    for an active filer.
    """
    cik = cik.zfill(10)
    doc = http.get_json(SUBMISSIONS.format(cik=cik))
    rows = _rows_from_block(cik, doc.get("filings", {}).get("recent", {}))
    for extra in doc.get("filings", {}).get("files", []):
        name = extra.get("name")
        if not name:
            continue
        rows.extend(_rows_from_block(
            cik, http.get_json(f"https://data.sec.gov/submissions/{name}")))
    return rows


def build(ciks: list[str], out_path: str = "data/events.parquet") -> pl.DataFrame:
    wanted = sorted({c.zfill(10) for c in ciks if c})
    rows: list[tuple] = []
    failed = 0
    for i, cik in enumerate(wanted, 1):
        try:
            rows.extend(fetch_filings(cik))
        except Exception as exc:                       # noqa: BLE001
            failed += 1
            if failed <= 5:
                print(f"  warn: CIK {cik}: {exc}")
        if i % 50 == 0:
            print(f"  {i}/{len(wanted)} CIKs, {len(rows):,} filings", flush=True)

    df = pl.DataFrame(rows, schema={
        "cik": pl.Utf8, "form": pl.Utf8, "items": pl.Utf8,
        "filing_date": pl.Utf8, "accepted_utc": pl.Utf8,
        "report_date": pl.Utf8, "accn": pl.Utf8}, orient="row")
    df = (df.with_columns([
              pl.col("filing_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
              pl.col("report_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
          ])
          .unique(subset=["cik", "accn"], keep="first")
          .drop_nulls("filing_date")
          .sort(["cik", "filing_date"]))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.write_parquet(out_path)
    no_accept = int(df.filter(pl.col("accepted_utc").is_null()).height)
    print(f"events: {df.height:,} filings, {df['cik'].n_unique()} CIKs, "
          f"{failed} CIK fetch failures, {no_accept} missing acceptance time")
    print(f"  by form: {df['form'].value_counts().sort('count', descending=True).to_dicts()}")
    return df


def with_item(df: pl.DataFrame, item: str) -> pl.DataFrame:
    """8-K filings carrying a given item code, e.g. '2.02'."""
    return df.filter(pl.col("form").str.starts_with("8-K") &
                     pl.col("items").str.contains(item, literal=True))


def asof_signals() -> pl.DataFrame:
    """AsOf's already-sealed 8-K signals, read-only.

    Not a data source for the panel -- events.build covers the same ground for
    the full universe. This exists so harness.py can cross-check its own
    event-study grading against 6,081 independently graded outcomes.
    """
    if not os.path.exists(ASOF_DB):
        return pl.DataFrame()
    con = sqlite3.connect(f"file:{ASOF_DB.replace(chr(92), '/')}?mode=ro", uri=True)
    try:
        rows = con.execute("""
            select s.id, s.symbol, s.kind, s.knownAt, o.horizonDays,
                   o.entryAt, o.exitAt, o.signalReturn, o.benchmarkReturn, o.excessReturn
            from signals s join signal_outcomes o on o.signalId = s.id
            where s.source = 'filing'
        """).fetchall()
    finally:
        con.close()
    return pl.DataFrame(rows, schema={
        "signal_id": pl.Utf8, "ticker": pl.Utf8, "kind": pl.Utf8, "known_at": pl.Utf8,
        "horizon_days": pl.Int32, "entry_at": pl.Utf8, "exit_at": pl.Utf8,
        "signal_return": pl.Float64, "benchmark_return": pl.Float64,
        "excess_return": pl.Float64}, orient="row")
