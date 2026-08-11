"""Daily bars from Yahoo, cached per ticker.

Cache design lifted from C:\\Projects\\mrm-momentum (parquet per ticker, TTL by
mtime, corrupt file self-heals by deleting itself).

Two gotchas carried over from that project, both learned the hard way:
* yfinance's start/end date range silently truncates daily history to roughly
  14 months. Ask for period="max" and slice locally instead.
* Yahoo spells share classes with a dash: BRK.B is BRK-B. The universe module
  already normalises to that spelling; this module assumes it.

Prices are split/dividend adjusted (auto_adjust), which is what return
calculations want. Delisted names frequently return nothing at all -- that is
the $0 survivorship residual, so the count is reported rather than swallowed.
"""
from __future__ import annotations

import contextlib
import os
import time
import warnings

import polars as pl

CACHE_DIR = "data/cache/prices"
TTL_HOURS = 20
BENCHMARKS = ["SPY", "QQQ", "DIA"]
SECTOR_ETFS = ["XLK", "XLF", "XLE", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"]

# GICS sector name -> sector ETF, for event-study benchmarking.
SECTOR_ETF_MAP = {
    "Information Technology": "XLK", "Financials": "XLF", "Energy": "XLE",
    "Health Care": "XLV", "Industrials": "XLI", "Consumer Discretionary": "XLY",
    "Consumer Staples": "XLP", "Utilities": "XLU", "Materials": "XLB",
    "Real Estate": "XLRE", "Communication Services": "XLC",
}


def _path(ticker: str) -> str:
    return os.path.join(CACHE_DIR, f"{ticker.replace('/', '_')}.parquet")


def is_fresh(ticker: str, ttl_hours: float = TTL_HOURS) -> bool:
    p = _path(ticker)
    if not os.path.exists(p):
        return False
    return (time.time() - os.path.getmtime(p)) < ttl_hours * 3600


def read_cached(ticker: str) -> pl.DataFrame | None:
    p = _path(ticker)
    if not os.path.exists(p):
        return None
    try:
        return pl.read_parquet(p)
    except Exception:
        with contextlib.suppress(OSError):
            os.remove(p)          # corrupt file: drop it, next fetch rebuilds
        return None


def _write(ticker: str, df: pl.DataFrame):
    os.makedirs(CACHE_DIR, exist_ok=True)
    df.write_parquet(_path(ticker))


def _download(batch: list[str]) -> dict[str, pl.DataFrame]:
    import yfinance as yf
    out: dict[str, pl.DataFrame] = {}
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        raw = yf.download(batch, period="max", interval="1d", auto_adjust=True,
                          group_by="ticker", progress=False, threads=True,
                          repair=True)
    if raw is None or raw.empty:
        return out
    for t in batch:
        try:
            sub = raw[t] if len(batch) > 1 else raw
        except (KeyError, TypeError):
            continue
        sub = sub.dropna(how="all")
        if sub.empty:
            continue
        df = pl.DataFrame({
            "date": [d.date() for d in sub.index],
            "ticker": [t] * len(sub),
            "close": sub["Close"].astype("float64").tolist(),
            "volume": sub["Volume"].fillna(0).astype("float64").tolist(),
        }).drop_nulls("close")
        if df.height:
            out[t] = df
    return out


def fetch_all(tickers: list[str], *, batch_size: int = 40,
              ttl_hours: float = TTL_HOURS) -> tuple[int, list[str]]:
    """Populate the cache. Returns (n_ok, missing_tickers)."""
    todo = [t for t in dict.fromkeys(tickers) if not is_fresh(t, ttl_hours)]
    have = [t for t in dict.fromkeys(tickers) if is_fresh(t, ttl_hours)]
    print(f"prices: {len(have)} cached, {len(todo)} to fetch")
    missing: list[str] = []
    ok = len(have)
    for i in range(0, len(todo), batch_size):
        batch = todo[i:i + batch_size]
        got = _download(batch)
        for t in batch:
            if t in got:
                _write(t, got[t])
                ok += 1
            else:
                missing.append(t)
        print(f"  {min(i + batch_size, len(todo))}/{len(todo)} fetched "
              f"({len(missing)} with no data)", flush=True)
    return ok, missing


def load_panel(tickers: list[str] | None = None) -> pl.DataFrame:
    """All cached bars as one long frame: date, ticker, close, volume."""
    if not os.path.isdir(CACHE_DIR):
        return pl.DataFrame(schema={"date": pl.Date, "ticker": pl.Utf8,
                                    "close": pl.Float64, "volume": pl.Float64})
    wanted = set(tickers) if tickers is not None else None
    frames = []
    for fn in os.listdir(CACHE_DIR):
        if not fn.endswith(".parquet"):
            continue
        t = fn[:-8]
        if wanted is not None and t not in wanted:
            continue
        df = read_cached(t)
        if df is not None and df.height:
            frames.append(df)
    if not frames:
        return pl.DataFrame(schema={"date": pl.Date, "ticker": pl.Utf8,
                                    "close": pl.Float64, "volume": pl.Float64})
    return pl.concat(frames).sort(["ticker", "date"])


def build(tickers: list[str], out_path: str = "data/prices.parquet") -> pl.DataFrame:
    ok, missing = fetch_all(list(tickers) + BENCHMARKS + SECTOR_ETFS)
    df = load_panel()
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.write_parquet(out_path)
    span = (df["date"].min(), df["date"].max()) if df.height else (None, None)
    print(f"prices: {df.height:,} bars, {df['ticker'].n_unique()} tickers, "
          f"{span[0]} to {span[1]}; {len(missing)} tickers returned no data")
    if missing:
        print(f"  no data (mostly delisted): {', '.join(sorted(missing)[:15])}"
              f"{' ...' if len(missing) > 15 else ''}")
    return df
