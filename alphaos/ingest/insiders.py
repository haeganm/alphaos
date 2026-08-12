"""Form 4 insider transactions, from SEC's quarterly bulk data sets.

Why bulk rather than per-filing XML: the universe has ~900 names over 15
years, which is roughly half a million Form 4s. At the throttle this client
holds itself to, fetching them individually is 50-70 hours of wall clock. The
quarterly Insider Transactions Data Sets carry the same fields in ~64 zip
files, and NONDERIV_TRANS.tsv is in fact a superset of what a Form 4 XML
parser typically extracts -- it includes the direct/indirect ownership flag
and the acquired/disposed code that hand-rolled parsers usually skip.

What the data sets do NOT carry is an acceptance clock: SUBMISSION.tsv has
FILING_DATE, a calendar day with no time. For an event study that is not good
enough -- a Form 4 accepted at 20:30 ET was not tradable at that day's close.
Acceptance timestamps come from the submissions API instead
(ingest/events.py), joined on accession number.

Two rules carried over from C:\\InsiderTradingAlerts, both learned there the
hard way:

* Filter on the ISSUER cik, never the reporting owner's. A 10% owner can
  itself be an S&P 500 company, and filtering on the owner would file that
  trade under the wrong ticker entirely.
* Insider role comes from the relationship field, never from the job title.
  "See Remarks" and "N/A" are common, real officerTitle values.

Rows are kept raw: every transaction code, no aggregation, no value floor.
Clustering and code filtering are properties of a hypothesis, so they belong
in the spec and the trigger library, not in the ingest.
"""
from __future__ import annotations

import io
import os
import zipfile
from datetime import date, timedelta

import polars as pl

from . import http
from .. import paths

BASE = "https://www.sec.gov/files/structureddata/data/insider-transactions-data-sets"
START_YEAR = 2009          # one year of lead-in before the 2010 panel start

# Transaction codes worth keeping meaning for. P and S are the open-market
# trades; the rest are grants, exercises, gifts and tax withholding, which are
# compensation mechanics rather than opinions about value. All are stored; the
# distinction is made in the trigger library.
OPEN_MARKET = {"P", "S"}

_SUB_COLS = ["ACCESSION_NUMBER", "FILING_DATE", "PERIOD_OF_REPORT",
             "DOCUMENT_TYPE", "ISSUERCIK", "ISSUERTRADINGSYMBOL"]
_TRANS_COLS = ["ACCESSION_NUMBER", "TRANS_DATE", "TRANS_CODE", "TRANS_SHARES",
               "TRANS_PRICEPERSHARE", "TRANS_ACQUIRED_DISP_CD",
               "SHRS_OWND_FOLWNG_TRANS", "DIRECT_INDIRECT_OWNERSHIP"]
_OWNER_COLS = ["ACCESSION_NUMBER", "RPTOWNERCIK", "RPTOWNERNAME",
               "RPTOWNER_RELATIONSHIP", "RPTOWNER_TITLE"]


def quarters(start_year: int = START_YEAR, today: date | None = None) -> list[str]:
    today = today or date.today()
    out = []
    for y in range(start_year, today.year + 1):
        for q in (1, 2, 3, 4):
            if date(y, 3 * q - 2, 1) <= today:
                out.append(f"{y}q{q}")
    return out


def download_quarter(q: str) -> str | None:
    """Cache one quarterly zip. Returns the path, or None if SEC has no file
    for that quarter yet (the current quarter is published in arrears)."""
    dest = os.path.join(str(paths.INSIDER_CACHE), f"{q}_form345.zip")
    if os.path.exists(dest) and os.path.getsize(dest) > 100_000:
        return dest
    try:
        return http.download(f"{BASE}/{q}_form345.zip", dest, log_every_mb=1000)
    except Exception as exc:                          # noqa: BLE001
        print(f"  {q}: unavailable ({exc})")
        return None


def _read_tsv(zf: zipfile.ZipFile, name: str, cols: list[str]) -> pl.DataFrame:
    raw = zf.read(name)
    df = pl.read_csv(io.BytesIO(raw), separator="\t", infer_schema_length=0,
                     truncate_ragged_lines=True, quote_char=None)
    have = [c for c in cols if c in df.columns]
    return df.select(have)


def _parse_sec_date(col: str) -> pl.Expr:
    """SEC writes DD-MON-YYYY in these extracts, e.g. 15-NOV-2022."""
    return pl.col(col).str.strptime(pl.Date, "%d-%b-%Y", strict=False)


def parse_quarter(zip_path: str, issuer_ciks: set[str]) -> pl.DataFrame:
    with zipfile.ZipFile(zip_path) as zf:
        sub = _read_tsv(zf, "SUBMISSION.tsv", _SUB_COLS)
        trans = _read_tsv(zf, "NONDERIV_TRANS.tsv", _TRANS_COLS)
        owner = _read_tsv(zf, "REPORTINGOWNER.tsv", _OWNER_COLS)

    sub = (sub.filter(pl.col("DOCUMENT_TYPE").is_in(["4", "4/A"]))
              .with_columns(pl.col("ISSUERCIK").str.zfill(10).alias("cik"))
              .filter(pl.col("cik").is_in(list(issuer_ciks))))
    if sub.height == 0:
        return pl.DataFrame()

    # NONDERIV_TRANS has no owner key, so a joint filing cannot be mapped back
    # to one owner transaction-by-transaction. Keep the reporting group as one
    # deterministic identity, but preserve every relationship flag: choosing
    # an arbitrary first owner could turn an officer purchase into a non-officer
    # purchase depending on source row order.
    owner = owner.group_by("ACCESSION_NUMBER").agg([
        pl.col(c).drop_nulls().unique().sort().str.join("|").alias(c)
        for c in _OWNER_COLS if c != "ACCESSION_NUMBER"
    ])

    df = (trans.join(sub, on="ACCESSION_NUMBER", how="inner")
               .join(owner, on="ACCESSION_NUMBER", how="left"))
    return df.select([
        pl.col("cik"),
        pl.col("ISSUERTRADINGSYMBOL").alias("issuer_ticker"),
        pl.col("ACCESSION_NUMBER").alias("accn"),
        _parse_sec_date("FILING_DATE").alias("filing_date"),
        _parse_sec_date("TRANS_DATE").alias("txn_date"),
        pl.col("TRANS_CODE").alias("txn_code"),
        pl.col("TRANS_ACQUIRED_DISP_CD").alias("acq_disp"),
        pl.col("TRANS_SHARES").cast(pl.Float64, strict=False).alias("shares"),
        pl.col("TRANS_PRICEPERSHARE").cast(pl.Float64, strict=False).alias("price"),
        pl.col("SHRS_OWND_FOLWNG_TRANS").cast(pl.Float64, strict=False)
          .alias("shares_after"),
        pl.col("DIRECT_INDIRECT_OWNERSHIP").alias("ownership"),
        pl.col("RPTOWNERCIK").alias("owner_cik"),
        pl.col("RPTOWNERNAME").alias("owner_name"),
        # role from the relationship field, never from the title
        pl.col("RPTOWNER_RELATIONSHIP").alias("relationship"),
        pl.col("RPTOWNER_TITLE").alias("owner_title"),
    ])


def attach_acceptance(df: pl.DataFrame, meta_path=None) -> pl.DataFrame:
    """Join the acceptance clock from the submissions-API metadata.

    Filings with no acceptance stamp keep a null; harness.run_event_study
    already treats a missing stamp as "public after that day's close", which is
    the conservative reading.
    """
    meta_path = meta_path or paths.FORM4_META
    if not os.path.exists(str(meta_path)):
        print("  warn: no Form 4 acceptance metadata; timestamps will be null")
        return df.with_columns(pl.lit(None, dtype=pl.Utf8).alias("accepted_utc"))
    meta = (pl.read_parquet(meta_path)
              .select(["accn", "accepted_utc"])
              .unique(subset=["accn"], keep="first"))
    return df.join(meta, on="accn", how="left")


# ---------------------------------------------------------------------------
# trigger library
# ---------------------------------------------------------------------------

# Fixed, not spec-tunable. Every knob a hypothesis can turn multiplies the
# family's effective trial count, and "3 insiders in 30 days" is the
# conventional definition in the literature rather than one this lab searched
# for. Changing it is a deliberate harness edit, visible in the diff.
CLUSTER_MIN_INSIDERS = 3
CLUSTER_WINDOW_DAYS = 30

_EVENT_SCHEMA = {"cik": pl.Utf8, "form": pl.Utf8, "items": pl.Utf8,
                 "filing_date": pl.Date, "accepted_utc": pl.Utf8,
                 "report_date": pl.Date, "accn": pl.Utf8}


def _open_market_buys(df: pl.DataFrame) -> pl.DataFrame:
    """Code P acquisitions only.

    P is an open-market purchase: the insider chose to buy with their own
    money. Everything else in the code list -- A grants, M option exercises,
    F tax withholding, G gifts -- is compensation mechanics and carries no
    opinion about value. Sales (S) are excluded from the trigger library
    entirely: insiders sell constantly for liquidity and diversification, at
    roughly fourteen sales per purchase in this data, and prior work in this
    repo has already established that short-side equity strategies fail here.
    """
    buys = df.filter((pl.col("txn_code") == "P") &
                     (pl.col("acq_disp") == "A") &
                     pl.col("shares").is_not_null() & (pl.col("shares") > 0))

    # Resolve each transaction to its EARLIEST disclosure. Form 4/A amendments
    # re-report a trade that was already public, and the raw table keeps both
    # rows by design -- but an event dated by the amendment would claim the
    # market learned something it had known for a while. In this data 227
    # open-market transaction identities appear under more than one accession,
    # 169 of them on different filing dates, with gaps running to 1,185 days.
    #
    # Deduping on the transaction's identity rather than on document type also
    # catches duplicates between two originals, and it leaves a trade that was
    # genuinely first disclosed in an amendment dated correctly, because that
    # amendment is then the earliest occurrence of its identity.
    ident = ["cik", "owner_key", "txn_date", "txn_code", "shares", "price"]
    return (buys
            # a null reporting-owner must not collapse distinct filers together
            .with_columns(pl.coalesce([pl.col("owner_cik"), pl.col("accn")])
                            .alias("owner_key"))
            .sort(["filing_date", "accepted_utc", "accn"], nulls_last=True)
            .unique(subset=ident, keep="first")
            .drop("owner_key")
            .sort(["cik", "filing_date"]))


def event_table(trigger: str, path=None) -> pl.DataFrame:
    """Insider events shaped like the 8-K archive, so the event engine needs no
    special case beyond choosing this source."""
    path = str(path or paths.INSIDERS)
    if not os.path.exists(path):
        return pl.DataFrame(schema=_EVENT_SCHEMA)
    df = _open_market_buys(pl.read_parquet(path)).drop_nulls("filing_date")
    if df.height == 0:
        return pl.DataFrame(schema=_EVENT_SCHEMA)

    if trigger == "insider_buy_officer":
        # Role comes from the relationship flags, never the job title:
        # "See Remarks" and "N/A" are common, real officerTitle values.
        rel = pl.col("relationship").fill_null("")
        df = df.filter(rel.str.contains("(?i)officer") | rel.str.contains("(?i)director"))
        ev = (df.group_by(["cik", "accn"]).agg([
                  pl.col("filing_date").min(),
                  pl.col("accepted_utc").min(),
              ]))
    elif trigger == "insider_cluster_buy":
        ev = _clusters(df)
    else:
        raise ValueError(f"unknown insider trigger {trigger!r}")

    return (ev.with_columns([
                pl.lit("INSIDER").alias("form"),
                pl.lit(trigger).alias("items"),
                pl.lit(None, dtype=pl.Date).alias("report_date"),
            ])
            .select(list(_EVENT_SCHEMA))
            .drop_nulls("filing_date")
            .sort(["cik", "filing_date"]))


def _clusters(buys: pl.DataFrame) -> pl.DataFrame:
    """Emit an event when the Nth distinct insider buys inside the window.

    The event is dated by the filing that COMPLETES the cluster, and carries
    that filing's acceptance timestamp -- the cluster is not knowable to an
    outside observer until its last component becomes public, however early
    the underlying transactions were executed. Dating it by the first purchase,
    or by transaction date rather than filing acceptance, would be a lookahead
    of days to weeks.

    Overlapping clusters are collapsed: once one fires, the window restarts, so
    a single sustained buying episode is one event rather than a run of them.
    """
    rows = []
    per = (buys.select(["cik", "filing_date", "accepted_utc", "owner_cik", "accn"])
               # Several insiders can file on the same day. Acceptance time is
               # the only honest ordering; null timestamps sort last so the
               # conservative after-close fallback completes the cluster.
               .sort(["cik", "filing_date", "accepted_utc", "accn"],
                     nulls_last=True))
    for key, g in per.partition_by("cik", as_dict=True).items():
        cik = key[0] if isinstance(key, tuple) else key
        dates = g["filing_date"].to_list()
        owners = g["owner_cik"].to_list()
        accepted = g["accepted_utc"].to_list()
        accns = g["accn"].to_list()
        last_fire = None
        for i in range(len(dates)):
            if last_fire is not None and (dates[i] - last_fire).days < CLUSTER_WINDOW_DAYS:
                continue
            lo = dates[i] - timedelta(days=CLUSTER_WINDOW_DAYS)
            distinct = {owners[j] for j in range(i + 1)
                        if dates[j] >= lo and owners[j] is not None
                        and (last_fire is None or dates[j] > last_fire)}
            if len(distinct) >= CLUSTER_MIN_INSIDERS:
                rows.append({"cik": cik, "filing_date": dates[i],
                             "accepted_utc": accepted[i], "accn": accns[i]})
                last_fire = dates[i]
    return pl.DataFrame(rows, schema={"cik": pl.Utf8, "filing_date": pl.Date,
                                      "accepted_utc": pl.Utf8, "accn": pl.Utf8})


def build(ciks: list[str], out_path=None, *, only_quarter: str | None = None,
          start_year: int = START_YEAR) -> pl.DataFrame:
    out_path = str(out_path or paths.INSIDERS)
    os.makedirs(str(paths.INSIDER_CACHE), exist_ok=True)
    issuer_ciks = {c.zfill(10) for c in ciks if c}

    qs = [only_quarter] if only_quarter else quarters(start_year)
    frames = []
    for q in qs:
        path = download_quarter(q)
        if path is None:
            continue
        part = parse_quarter(path, issuer_ciks)
        if part.height:
            frames.append(part)
        print(f"  {q}: {part.height:,} transactions", flush=True)

    fetched = pl.concat(frames) if frames else pl.DataFrame()
    if fetched.height == 0:
        raise RuntimeError("no insider transactions parsed; refusing to write")

    df = attach_acceptance(fetched)
    if only_quarter and os.path.exists(out_path):
        df = (pl.concat([pl.read_parquet(out_path), df], how="diagonal_relaxed")
                .unique(subset=["accn", "owner_cik", "txn_date", "txn_code",
                                "shares", "price"], keep="last"))
    df = df.sort(["cik", "filing_date", "accn"])

    tmp = out_path + ".part"
    df.write_parquet(tmp)
    os.replace(tmp, out_path)

    buys = df.filter(pl.col("txn_code") == "P").height
    sells = df.filter(pl.col("txn_code") == "S").height
    no_ts = int(df.filter(pl.col("accepted_utc").is_null()).height)
    print(f"insiders: {df.height:,} transactions, {df['cik'].n_unique()} issuers, "
          f"{buys:,} open-market buys, {sells:,} sells, "
          f"{no_ts:,} without an acceptance stamp")
    return df


def latest_available_quarter(today: date | None = None, lookback: int = 4) -> str:
    """Newest published SEC bulk quarter, allowing for publication lag."""
    candidates = quarters(max(START_YEAR, (today or date.today()).year - 1), today)
    for q in reversed(candidates[-lookback:]):
        if download_quarter(q) is not None:
            return q
    raise RuntimeError(f"no insider transaction quarter available in the last {lookback}")
