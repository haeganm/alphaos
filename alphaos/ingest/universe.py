"""S&P 500 membership over time -- who was actually in the index on a date.

Testing a cross-sectional signal against today's constituents is the classic
survivorship mistake: the 2012 cross-section would contain only companies that
survived to 2026, and every one of them is a winner by construction.

Wikipedia's "selected changes" table was removed from the page, so membership
is reconstructed from the page's own revision history: one snapshot per
quarter, parsed from the rendered constituents table of that revision. A
ticker is a member across the span of snapshots it appears in. Departures are
therefore located to within a quarter, which is fine for monthly rebalancing
and is the granularity recorded in the data.

Snapshots are cached to data/cache/wiki_snapshots.json -- rebuilds are free
and, more importantly, reproducible.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from urllib.parse import quote

import polars as pl
from bs4 import BeautifulSoup

from . import http
from .. import paths

API = "https://en.wikipedia.org/w/api.php"
PAGE = "List of S&P 500 companies"

# Index name -> Wikipedia page. The same revision-history reconstruction works
# for any index whose page carries a constituents table, which is what makes a
# wider universe reachable at all: EDGAR blanks a company's `tickers` field the
# moment it stops filing (Carefusion and Monsanto both return []), so there is
# no free path from a dead CIK back to its historical ticker. Wikipedia's
# history is the only free record of who was in an index and under what symbol.
#
# The S&P 600 is deliberately absent. Its page has no usable history before
# 2018-08 and the current table defeats _parse_constituents; a 2018+ universe
# would overlap the embargo and could not support a discovery window.
INDEX_PAGES = {
    "sp500": "List of S&P 500 companies",
    "sp400": "List of S&P 400 companies",
}
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SNAPSHOT_CACHE = paths.WIKI_SNAPSHOTS
START_YEAR = 2010
# ponytail: quarterly snapshots. Monthly costs 3x the calls for ~6 weeks better
# resolution on departures; raise to monthly if a signal turns out to be
# sensitive to index-deletion timing.
MONTHS = (1, 4, 7, 10)


def _norm_ticker(t: str) -> str:
    """Wikipedia writes BRK.B, Yahoo wants BRK-B. One spelling everywhere."""
    t = re.sub(r"\[.*?\]", "", t)
    return re.sub(r"\s+", "", t).upper().replace(".", "-")


def _quarter_starts() -> list[date]:
    today = date.today()
    out = []
    for y in range(START_YEAR, today.year + 1):
        for m in MONTHS:
            d = date(y, m, 1)
            if d <= today:
                out.append(d)
    return out


def _first_revision_at_or_after(d: date, page: str = PAGE) -> dict | None:
    r = http.get_json(
        f"{API}?action=query&prop=revisions&titles={quote(page)}"
        f"&rvlimit=1&rvdir=newer&rvstart={d.isoformat()}T00:00:00Z"
        f"&rvprop=ids|timestamp&format=json&formatversion=2")
    pages = r.get("query", {}).get("pages", [])
    if not pages or "revisions" not in pages[0]:
        return None
    rev = pages[0]["revisions"][0]
    return {"revid": rev["revid"], "timestamp": rev["timestamp"]}


def _parse_constituents(html: str) -> list[dict]:
    """Pull (ticker, sector, cik) from a revision's constituents table.

    Column names drifted over 16 years ("Ticker symbol" -> "Symbol"), and old
    revisions have no CIK column, so everything is located by header text.
    """
    soup = BeautifulSoup(html, "html.parser")
    best, best_rows = None, 0
    for t in soup.find_all("table"):
        rows = t.find_all("tr")
        if len(rows) < 50:
            continue
        head = " ".join(th.get_text(" ", strip=True).lower()
                        for th in rows[0].find_all(["th", "td"]))
        if "symbol" in head or "ticker" in head:
            if len(rows) > best_rows:
                best, best_rows = t, len(rows)
    if best is None:
        return []

    rows = best.find_all("tr")
    header = [th.get_text(" ", strip=True).lower() for th in rows[0].find_all(["th", "td"])]

    def col(*names):
        for n in names:
            for i, h in enumerate(header):
                if n in h:
                    return i
        return None

    i_sym = col("symbol", "ticker")
    i_sec = col("gics sector")
    i_cik = col("cik")
    if i_sym is None:
        return []

    out = []
    for tr in rows[1:]:
        c = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if len(c) <= i_sym:
            continue
        tkr = _norm_ticker(c[i_sym])
        if not tkr or len(tkr) > 6 or not re.match(r"^[A-Z][A-Z\-]*$", tkr):
            continue
        cik = None
        if i_cik is not None and len(c) > i_cik:
            cik = re.sub(r"\D", "", c[i_cik]) or None
        out.append({
            "ticker": tkr,
            "gics_sector": c[i_sec] if i_sec is not None and len(c) > i_sec else None,
            "cik": cik,
        })
    return out


def fetch_snapshots(cache_path=SNAPSHOT_CACHE, refresh: bool = False,
                    wiki_page: str = PAGE) -> list[dict]:
    """One parsed constituents snapshot per quarter since START_YEAR."""
    cache = {}
    if os.path.exists(cache_path) and not refresh:
        with open(cache_path, "r", encoding="utf-8") as fh:
            cache = json.load(fh)

    snapshots = []
    for q in _quarter_starts():
        key = q.isoformat()
        if key in cache:
            snapshots.append(cache[key])
            continue
        rev = _first_revision_at_or_after(q, wiki_page)
        if rev is None:
            continue
        page = http.get_json(
            f"{API}?action=parse&oldid={rev['revid']}&prop=text&format=json&formatversion=2")
        members = _parse_constituents(page.get("parse", {}).get("text", ""))
        if len(members) < 100:
            print(f"  warn: {key} rev {rev['revid']} parsed {len(members)} rows, skipping")
            continue
        snap = {"asof": key, "revid": rev["revid"], "timestamp": rev["timestamp"],
                "members": members}
        cache[key] = snap
        snapshots.append(snap)
        print(f"  {key}: {len(members)} constituents (rev {rev['revid']})", flush=True)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(cache, fh)
    return sorted(snapshots, key=lambda s: s["asof"])


def build_membership(snapshots: list[dict]) -> pl.DataFrame:
    """Runs of consecutive snapshots containing a ticker become intervals.

    An interval that reaches the last snapshot stays open (end = null). One
    that stops earlier ends at the first snapshot where the ticker is absent,
    i.e. departure is known only to the quarter -- honest, not precise.
    """
    dates = [datetime.fromisoformat(s["asof"]).date() for s in snapshots]
    sets = [{m["ticker"] for m in s["members"]} for s in snapshots]
    meta: dict[str, dict] = {}
    for s in snapshots:                       # later snapshots win: freshest sector/CIK
        for m in s["members"]:
            prev = meta.get(m["ticker"], {})
            meta[m["ticker"]] = {
                "gics_sector": m.get("gics_sector") or prev.get("gics_sector"),
                "cik": m.get("cik") or prev.get("cik"),
            }

    intervals = []
    for tkr in sorted(meta):
        run_start = None
        for i, present in enumerate(tkr in s for s in sets):
            if present and run_start is None:
                run_start = dates[i]
            elif not present and run_start is not None:
                intervals.append({"ticker": tkr, "start": run_start, "end": dates[i]})
                run_start = None
        if run_start is not None:
            intervals.append({"ticker": tkr, "start": run_start, "end": None})

    df = pl.DataFrame(intervals, schema={"ticker": pl.Utf8, "start": pl.Date, "end": pl.Date})
    return df.with_columns([
        pl.col("ticker").replace_strict(
            {t: (m.get("gics_sector") or "UNKNOWN") for t, m in meta.items()},
            default="UNKNOWN").alias("gics_sector"),
        pl.col("ticker").replace_strict(
            {t: m.get("cik") for t, m in meta.items()},
            default=None, return_dtype=pl.Utf8).alias("cik"),
    ]).sort(["ticker", "start"])


def attach_ciks(df: pl.DataFrame) -> pl.DataFrame:
    """Fill missing CIKs from SEC's current-registrant map. Names that left the
    index years ago often stay null -- that gap is the survivorship residual we
    accept at $0, and coverage_report prints it every build."""
    data = http.get_json(SEC_TICKERS_URL)
    m = {_norm_ticker(row["ticker"]): str(row["cik_str"]).zfill(10) for row in data.values()}
    return df.with_columns(
        pl.coalesce([
            pl.when(pl.col("cik").is_not_null()).then(pl.col("cik").str.zfill(10)),
            pl.col("ticker").replace_strict(m, default=None, return_dtype=pl.Utf8),
        ]).alias("cik"))


def resolve_price_tickers(df: pl.DataFrame, price_spans: dict[str, tuple[date, date]]
                          ) -> tuple[pl.DataFrame, list[tuple[str, str]]]:
    """Add price_ticker: which symbol's price series represents this name.

    A company that renames keeps its CIK, so SEC's current-registrant map turns
    WLP into ELV and the full history is recoverable under the new symbol.

    The trap is that a CIK also survives bankruptcy and re-listing, where the
    price series is NOT economically continuous -- splicing post-emergence
    prices onto a pre-bankruptcy membership would invent a recovery that no
    holder experienced. So a rename is only accepted when the target's price
    history actually reaches back to the start of the old name's membership.
    Discontinuous cases fail that test on their own and stay missing.
    """
    data = http.get_json(SEC_TICKERS_URL)
    cik2cur = {str(r["cik_str"]).zfill(10): _norm_ticker(r["ticker"]) for r in data.values()}

    have = set(price_spans)
    first_seen = (df.group_by("ticker").agg(pl.col("start").min())
                    .to_dict(as_series=False))
    starts = dict(zip(first_seen["ticker"], first_seen["start"]))

    mapping: dict[str, str] = {}
    accepted: list[tuple[str, str]] = []
    for tkr, cik in df.select(["ticker", "cik"]).unique().rows():
        if tkr in have or not cik:
            continue
        cur = cik2cur.get(cik)
        if not cur or cur == tkr or cur not in have:
            continue
        target_start = price_spans[cur][0]
        member_start = starts.get(tkr)
        if member_start is not None and target_start <= member_start:
            mapping[tkr] = cur
            accepted.append((tkr, cur))
    out = df.with_columns(
        pl.col("ticker").replace(mapping).alias("price_ticker"))
    return out, sorted(accepted)


def members_on(df: pl.DataFrame, d: date, index: str | None = None) -> list[str]:
    """Tickers in the universe on date d. The only sanctioned way to ask.

    Deduplicated: with several indices unioned, a name caught mid-promotion can
    legitimately match twice on a boundary date, and a duplicated ticker would
    quietly double-weight that name in any cross-section built from this list.
    """
    m = df.filter((pl.col("start") <= d) &
                  (pl.col("end").is_null() | (pl.col("end") > d)))
    if index is not None:
        m = m.filter(pl.col("index") == index)
    return m["ticker"].unique().to_list()


def coverage_report(df: pl.DataFrame) -> dict:
    n = df["ticker"].n_unique()
    with_cik = df.filter(pl.col("cik").is_not_null())["ticker"].n_unique()
    return {
        "distinct_tickers": n,
        "with_cik": with_cik,
        "cik_coverage_pct": round(100 * with_cik / max(n, 1), 1),
        "members_by_year": {y: len(members_on(df, date(y, 6, 30)))
                            for y in range(START_YEAR, date.today().year + 1)},
        "ever_left": int(df.filter(pl.col("end").is_not_null()).height),
    }


def build_index(index: str, refresh: bool = False) -> pl.DataFrame:
    """Membership intervals for one index, tagged with which index they are."""
    page = INDEX_PAGES[index]
    # sp500 keeps the original cache filename so the 66 snapshots already
    # fetched are not re-downloaded.
    cache = (paths.WIKI_SNAPSHOTS if index == "sp500"
             else paths.CACHE / f"wiki_snapshots_{index}.json")
    snaps = fetch_snapshots(cache_path=cache, refresh=refresh, wiki_page=page)
    df = build_membership(snaps).with_columns(pl.lit(index).alias("index"))
    rep = coverage_report(df)
    print(f"{index}: {rep['distinct_tickers']} distinct tickers over {len(snaps)} "
          f"snapshots, {rep['ever_left']} departures recorded")
    print(f"  members by year: {rep['members_by_year']}")
    return df


def build(out_path=paths.UNIVERSE, refresh: bool = False,
          indices=("sp500", "sp400")) -> pl.DataFrame:
    """Union of the requested indices, one row per (ticker, membership span).

    A name that graduates from the MidCap to the S&P 500 appears once per
    index with non-overlapping spans, which is correct: it was a mid-cap then
    and a large-cap now, and a campaign that splits on `index` must see that
    change rather than a single lifetime label.
    """
    frames = [build_index(i, refresh=refresh) for i in indices]
    df = attach_ciks(pl.concat(frames)).sort(["ticker", "start"])
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.write_parquet(out_path)
    rep = coverage_report(df)
    print(f"universe: {rep['distinct_tickers']} distinct tickers across "
          f"{len(frames)} indices, {rep['cik_coverage_pct']}% with CIK")
    print(f"  members by year: {rep['members_by_year']}")
    return df
