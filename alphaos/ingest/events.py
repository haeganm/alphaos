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
from .. import paths

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik}.json"
ASOF_DB = r"C:\AsOf\data\asof.db"
FORMS = {"8-K", "8-K/A", "10-K", "10-Q"}

# A refresh may not shrink the archive by more than this. See build().
MAX_SHRINK = 0.20

_RAW_SCHEMA = {
    "cik": pl.Utf8, "form": pl.Utf8, "items": pl.Utf8,
    "filing_date": pl.Utf8, "accepted_utc": pl.Utf8,
    "report_date": pl.Utf8, "accn": pl.Utf8,
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


def fetch_filings(cik: str, *, recent_only: bool = False) -> list[tuple]:
    """8-K/10-K/10-Q filings for one CIK.

    filings.recent holds only the last ~1000 documents; anything older lives in
    the files listed under filings.files, which is most of the 2010-2018 span
    for an active filer. recent_only skips those pages -- for a nightly top-up
    the recent block already covers everything new, and fetching the full
    33-year history for 759 filers every night cost ~1,900 throttled requests
    to gain a handful of rows.
    """
    cik = cik.zfill(10)
    doc = http.get_json(SUBMISSIONS.format(cik=cik))
    rows = _rows_from_block(cik, doc.get("filings", {}).get("recent", {}))
    if recent_only:
        return rows
    for extra in doc.get("filings", {}).get("files", []):
        name = extra.get("name")
        if not name:
            continue
        rows.extend(_rows_from_block(
            cik, http.get_json(f"https://data.sec.gov/submissions/{name}")))
    return rows


def _normalize(df: pl.DataFrame) -> pl.DataFrame:
    return (df.with_columns([
                pl.col("filing_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
                pl.col("report_date").str.strptime(pl.Date, "%Y-%m-%d", strict=False),
            ])
            .unique(subset=["cik", "accn"], keep="first")
            .drop_nulls("filing_date")
            .sort(["cik", "filing_date"]))


def build(ciks: list[str], out_path=paths.EVENTS, *,
          recent_only: bool = False, merge: bool = False) -> pl.DataFrame:
    """Fetch filing metadata and write the archive.

    Guarded against the failure that matters: every per-CIK error is swallowed
    so one bad filer cannot abort a long run, which meant a total outage (SEC
    403, dead network at 04:30) produced zero rows, built an empty frame, and
    overwrote 297k good filings while reporting success. Every later event
    study then saw no events, which the skeptic reads as a legitimately dead
    signal rather than as missing data.

    So: merge onto what exists, refuse to write a materially smaller archive,
    and write through a temp file the way http.download already does.
    """
    wanted = sorted({c.zfill(10) for c in ciks if c})
    rows: list[tuple] = []
    failed = 0
    for i, cik in enumerate(wanted, 1):
        try:
            rows.extend(fetch_filings(cik, recent_only=recent_only))
        except Exception as exc:                       # noqa: BLE001
            failed += 1
            if failed <= 5:
                print(f"  warn: CIK {cik}: {exc}")
        if i % 50 == 0:
            print(f"  {i}/{len(wanted)} CIKs, {len(rows):,} filings "
                  f"({failed} failures)", flush=True)

    fetched = _normalize(pl.DataFrame(rows, schema=_RAW_SCHEMA, orient="row"))
    out_path = str(out_path)
    existing = None
    if os.path.exists(out_path):
        try:
            existing = pl.read_parquet(out_path)
        except Exception:                              # noqa: BLE001
            existing = None

    df = fetched
    if existing is not None and (merge or recent_only):
        df = (pl.concat([existing, fetched])
                .unique(subset=["cik", "accn"], keep="first")
                .sort(["cik", "filing_date"]))

    if existing is not None and existing.height:
        shrink = 1.0 - (df.height / existing.height)
        if shrink > MAX_SHRINK:
            raise RuntimeError(
                f"refusing to overwrite {out_path}: {existing.height:,} rows -> "
                f"{df.height:,} ({shrink:.0%} smaller, limit {MAX_SHRINK:.0%}). "
                f"{failed}/{len(wanted)} CIK fetches failed -- this looks like an "
                f"outage, not a real change. Existing file left untouched.")

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    tmp = out_path + ".part"
    df.write_parquet(tmp)
    os.replace(tmp, out_path)

    no_accept = int(df.filter(pl.col("accepted_utc").is_null()).height)
    print(f"events: {df.height:,} filings, {df['cik'].n_unique()} CIKs, "
          f"{failed} CIK fetch failures, {no_accept} missing acceptance time")
    return df


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
