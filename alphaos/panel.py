"""The point-in-time feature panel: what was knowable about each company on
each rebalance date, and nothing else.

Three problems have to be solved before a fundamentals panel is usable, and
skipping any of them quietly fabricates alpha:

1. Restatements. A 2014 figure restated in 2016 must not appear in a 2015
   cross-section. Resolved by taking, for each period, the latest value filed
   strictly before the as-of date.

2. Year-to-date reporting. 10-Qs report cumulative figures: Q3's "revenue" row
   often covers 270 days, not 90. And Q4 usually has no quarterly row at all --
   only the 10-K's annual figure. Both are solved by the same operation:
   within a fiscal year, consecutive YTD periods differ by exactly one
   quarter, so quarterly = YTD(n) - YTD(n-1). A derived value only becomes
   knowable when both components are, so it inherits the later filing date.

3. Period-length mixing. An annual revenue figure compared against a quarterly
   one reads as ~300% growth. Duration facts are separated by length; balance
   sheet facts (instants) pass through untouched.

The output is a snapshot table: for each (as-of date, company, tag), the last
eight quarterly values that were public at that moment, as q0..q7. Every spec
transform (yoy_growth, accel, ...) is a cheap arithmetic on those columns, so
no experiment ever touches raw filings and no experiment can re-introduce
lookahead on its own.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import date, timedelta

import polars as pl

N_LAGS = 8
EMBARGO_START = date(2022, 7, 1)   # discovery data ends here; see holdout.py
PANEL_PATH = "data/panel.parquet"
EMBARGO_PANEL_PATH = "data/embargo/panel.parquet"
FORWARD_PATH = "data/forward_returns.parquet"
VERSION_PATH = "data/data_version.json"

DURATION_TAGS = {"Revenues", "CostOfRevenue", "NetIncomeLoss",
                 "OperatingIncomeLoss", "OperatingCashFlow"}


# ---------------------------------------------------------------------------
# quarterly series
# ---------------------------------------------------------------------------

def quarterly_series(fund: pl.DataFrame) -> pl.DataFrame:
    """Collapse raw XBRL facts into one quarterly series per (cik, tag).

    Returns cik, tag, period_end, value, filed, derived.
    """
    instants = (fund.filter(pl.col("period_days").is_null())
                    .select(["cik", "tag", "end", "value", "filed"])
                    .rename({"end": "period_end"})
                    .with_columns(pl.lit(False).alias("derived")))

    dur = fund.filter(pl.col("period_days").is_not_null() &
                      pl.col("tag").is_in(list(DURATION_TAGS)))
    if dur.height == 0:
        return instants.sort(["cik", "tag", "period_end", "filed"])

    # Latest filed value per (cik, tag, start, end) -- restatements collapse to
    # the newest version of that exact period; PIT filtering happens later.
    dur = (dur.sort("filed")
              .group_by(["cik", "tag", "start", "end"])
              .last()
              .select(["cik", "tag", "start", "end", "period_days", "value", "filed"]))

    # Already-quarterly rows.
    direct = (dur.filter(pl.col("period_days").is_between(65, 115))
                 .select(["cik", "tag", "end", "value", "filed"])
                 .rename({"end": "period_end"})
                 .with_columns(pl.lit(False).alias("derived")))

    # YTD chains: same fiscal-year start, increasing end. Differencing adjacent
    # entries yields the quarter that the longer period added -- this recovers
    # Q4 (annual minus 9-month) and quarterly cash flow in one operation.
    chain = dur.sort(["cik", "tag", "start", "end"])
    derived = (chain.with_columns([
                    pl.col("value").shift(1).over(["cik", "tag", "start"]).alias("prev_value"),
                    pl.col("end").shift(1).over(["cik", "tag", "start"]).alias("prev_end"),
                    pl.col("filed").shift(1).over(["cik", "tag", "start"]).alias("prev_filed"),
                    pl.col("period_days").shift(1).over(["cik", "tag", "start"]).alias("prev_days"),
                ])
                .filter(pl.col("prev_value").is_not_null() &
                        (pl.col("period_days") - pl.col("prev_days")).is_between(65, 115))
                .with_columns([
                    (pl.col("value") - pl.col("prev_value")).alias("value"),
                    # knowable only once both halves are public
                    pl.max_horizontal("filed", "prev_filed").alias("filed"),
                ])
                .select(["cik", "tag", "end", "value", "filed"])
                .rename({"end": "period_end"})
                .with_columns(pl.lit(True).alias("derived")))

    out = pl.concat([instants, direct, derived])
    # Prefer a directly reported quarter over a derived one for the same period.
    return (out.sort(["cik", "tag", "period_end", "derived", "filed"])
               .unique(subset=["cik", "tag", "period_end", "filed"], keep="first")
               .sort(["cik", "tag", "period_end", "filed"]))


# ---------------------------------------------------------------------------
# as-of snapshots
# ---------------------------------------------------------------------------

def rebalance_dates(start: date, end: date, freq: str = "monthly") -> list[date]:
    step = {"monthly": 1, "quarterly": 3}[freq]
    out, y, m = [], start.year, start.month
    while True:
        d = date(y, m, 1)
        if d > end:
            break
        if d >= start:
            out.append(d)
        m += step
        while m > 12:
            m -= 12
            y += 1
    return out


def snapshot(qs: pl.DataFrame, asof: date, n_lags: int = N_LAGS) -> pl.DataFrame:
    """The last n_lags quarterly values per (cik, tag) public before `asof`.

    Strictly before: a fact filed on the rebalance morning is not assumed
    tradable at that rebalance.
    """
    known = qs.filter(pl.col("filed") < asof)
    if known.height == 0:
        return pl.DataFrame()
    latest = (known.sort("filed")
                   .group_by(["cik", "tag", "period_end"])
                   .last()
                   .select(["cik", "tag", "period_end", "value"]))
    ranked = (latest.sort(["cik", "tag", "period_end"], descending=[False, False, True])
                    .with_columns(pl.int_range(pl.len()).over(["cik", "tag"]).alias("lag"))
                    .filter(pl.col("lag") < n_lags))
    wide = ranked.pivot(on="lag", index=["cik", "tag"], values="value")
    rename = {str(i): f"q{i}" for i in range(n_lags)}
    wide = wide.rename({k: v for k, v in rename.items() if k in wide.columns})
    for i in range(n_lags):                       # guarantee stable schema
        if f"q{i}" not in wide.columns:
            wide = wide.with_columns(pl.lit(None, dtype=pl.Float64).alias(f"q{i}"))
    return wide.with_columns(pl.lit(asof).alias("asof")).select(
        ["asof", "cik", "tag"] + [f"q{i}" for i in range(n_lags)])


def build_snapshots(qs: pl.DataFrame, dates: list[date]) -> pl.DataFrame:
    frames = []
    for i, d in enumerate(dates, 1):
        s = snapshot(qs, d)
        if s.height:
            frames.append(s)
        if i % 24 == 0:
            print(f"  snapshots {i}/{len(dates)}", flush=True)
    return pl.concat(frames) if frames else pl.DataFrame()


# ---------------------------------------------------------------------------
# forward returns
# ---------------------------------------------------------------------------

HORIZONS = (21, 63, 126)


def market_table(prices: pl.DataFrame, dates: list[date], *, lag_days: int = 0,
                 horizons=HORIZONS, benchmark: str = "SPY") -> pl.DataFrame:
    """Per (rebalance, ticker): entry price, trailing features, forward returns.

    Entry is the first close that actually existed on or after the rebalance
    date (plus lag_days), never the calendar date itself -- a rebalance landing
    on a holiday must not quietly hand the signal an extra day of return.

    Trailing features use only bars strictly before entry:
      mom_12_1  return from t-252 to t-21, the standard momentum definition
                that skips the short-term reversal month
      beta_252  OLS beta of daily returns on the benchmark, 252 bars
      vol_63    realised daily vol, 63 bars
      adv_21    average dollar volume, a liquidity screen and cost proxy
    """
    import numpy as np

    px = prices.sort(["ticker", "date"])
    parts = px.partition_by("ticker", as_dict=True)

    bench = parts.get((benchmark,))
    if bench is None:
        bench = parts.get(benchmark)
    bench_ret: dict[date, float] = {}
    if bench is not None:
        bd = bench["date"].to_list()
        bc = np.asarray(bench["close"].to_list(), dtype=float)
        br = np.concatenate([[np.nan], bc[1:] / bc[:-1] - 1.0])
        bench_ret = dict(zip(bd, br))

    rows = []
    for key, df in parts.items():
        ticker = key[0] if isinstance(key, tuple) else key
        d_arr = df["date"].to_list()
        c_arr = np.asarray(df["close"].to_list(), dtype=float)
        v_arr = np.asarray(df["volume"].to_list(), dtype=float)
        n = len(d_arr)
        if n < 30:
            continue
        ret = np.concatenate([[np.nan], c_arr[1:] / c_arr[:-1] - 1.0])
        bret = np.asarray([bench_ret.get(d, np.nan) for d in d_arr], dtype=float)

        j = 0
        for rd in dates:
            target = rd + timedelta(days=lag_days)
            while j < n and d_arr[j] < target:
                j += 1
            if j >= n:
                break
            i = j
            rec = {"asof": rd, "ticker": ticker, "entry_date": d_arr[i],
                   "entry_close": float(c_arr[i])}

            # trailing windows end at i-1: nothing from the entry bar onward
            rec["mom_12_1"] = (float(c_arr[i - 21] / c_arr[i - 252] - 1.0)
                               if i >= 252 else None)
            if i >= 64:
                w = ret[i - 63:i]
                rec["vol_63"] = float(np.nanstd(w, ddof=1))
                rec["adv_21"] = float(np.nanmean(c_arr[i - 21:i] * v_arr[i - 21:i]))
            else:
                rec["vol_63"] = rec["adv_21"] = None
            if i >= 252:
                a, b = ret[i - 252:i], bret[i - 252:i]
                ok = np.isfinite(a) & np.isfinite(b)
                if ok.sum() > 100 and np.var(b[ok]) > 0:
                    rec["beta_252"] = float(np.cov(a[ok], b[ok])[0, 1] / np.var(b[ok], ddof=1))
                else:
                    rec["beta_252"] = None
            else:
                rec["beta_252"] = None

            for h in horizons:
                k = i + h
                rec[f"fwd_{h}"] = float(c_arr[k] / c_arr[i] - 1.0) if k < n else None
            rows.append(rec)

    schema = {"asof": pl.Date, "ticker": pl.Utf8, "entry_date": pl.Date,
              "entry_close": pl.Float64, "mom_12_1": pl.Float64, "vol_63": pl.Float64,
              "adv_21": pl.Float64, "beta_252": pl.Float64}
    schema.update({f"fwd_{h}": pl.Float64 for h in horizons})
    return pl.DataFrame(rows, schema=schema)


# ---------------------------------------------------------------------------
# assembly
# ---------------------------------------------------------------------------

def _membership_rows(universe: pl.DataFrame, dates: list[date]) -> pl.DataFrame:
    rows = []
    for d in dates:
        m = universe.filter((pl.col("start") <= d) &
                            (pl.col("end").is_null() | (pl.col("end") > d)))
        for t, c, s in zip(m["ticker"], m["cik"], m["gics_sector"]):
            rows.append({"asof": d, "ticker": t, "cik": c, "gics_sector": s})
    return pl.DataFrame(rows, schema={"asof": pl.Date, "ticker": pl.Utf8,
                                      "cik": pl.Utf8, "gics_sector": pl.Utf8})


def data_version(paths: list[str]) -> str:
    """Content hash of the built artifacts. Registered with every experiment;
    a bump invalidates prior verdicts because the evidence changed."""
    h = hashlib.sha256()
    for p in sorted(paths):
        if os.path.exists(p):
            st = os.stat(p)
            h.update(f"{os.path.basename(p)}:{st.st_size}".encode())
            with open(p, "rb") as fh:
                h.update(fh.read(1 << 20))
    return f"{date.today().isoformat()}-{h.hexdigest()[:10]}"


def build(fundamentals_path="data/fundamentals.parquet",
          universe_path="data/universe.parquet",
          prices_path="data/prices.parquet",
          start=date(2010, 1, 1), end: date | None = None,
          freq: str = "monthly") -> dict:
    end = end or date.today()
    fund = pl.read_parquet(fundamentals_path)
    uni = pl.read_parquet(universe_path)
    px = pl.read_parquet(prices_path)

    print("building quarterly series...")
    qs = quarterly_series(fund)
    print(f"  {qs.height:,} quarterly observations "
          f"({int(qs.filter(pl.col('derived'))['derived'].len()):,} derived from YTD chains)")

    dates = rebalance_dates(start, end, freq)
    print(f"building {len(dates)} as-of snapshots...")
    snaps = build_snapshots(qs, dates)

    members = _membership_rows(uni, dates)
    panel = members.join(snaps, on=["asof", "cik"], how="inner")

    # Returns are deliberately NOT joined in here. harness.py builds them from
    # prices at run time, because the entry date depends on the spec's lag_days
    # and two copies of "the forward return" is exactly the kind of drift that
    # ends with a backtest disagreeing with itself.
    print("computing market table (lag 0, for inspection)...")
    fwd = market_table(px, dates)

    disc = panel.filter(pl.col("asof") < EMBARGO_START)
    emb = panel.filter(pl.col("asof") >= EMBARGO_START)
    os.makedirs("data/embargo", exist_ok=True)
    disc.write_parquet(PANEL_PATH)
    emb.write_parquet(EMBARGO_PANEL_PATH)
    fwd.write_parquet(FORWARD_PATH)

    dv = data_version([fundamentals_path, universe_path, prices_path, PANEL_PATH])
    meta = {
        "data_version": dv,
        "built_at": date.today().isoformat(),
        "rows_discovery": disc.height,
        "rows_embargo": emb.height,
        "embargo_start": EMBARGO_START.isoformat(),
        "rebalances": len(dates),
        "tags": sorted(panel["tag"].unique().to_list()),
        "date_range": [str(panel["asof"].min()), str(panel["asof"].max())],
    }
    with open(VERSION_PATH, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
    print(f"panel: {disc.height:,} discovery rows, {emb.height:,} embargoed "
          f"(from {EMBARGO_START}), data_version={dv}")
    return meta


def load(embargo: bool = False) -> pl.DataFrame:
    """Load the panel. embargo=True is reserved for holdout.py."""
    return pl.read_parquet(EMBARGO_PANEL_PATH if embargo else PANEL_PATH)


def current_version() -> str:
    with open(VERSION_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)["data_version"]
