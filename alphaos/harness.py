"""The fixed test harness. Hand-written on purpose: nothing here is generated.

Two engines, one contract -- take a validated spec, return a results dict.

Cross-sectional: rank the universe on a signal at each rebalance, hold the top
decile against the bottom, net of turnover costs. Holding periods never
overlap (spec.py enforces it), so the return series is genuinely independent
period to period and Sharpe/t-stat/DSR mean what they say.

Event study: measure abnormal return in a window after a filing became public.
Entry timing follows AsOf's rule exactly, and it is the part most likely to be
got wrong: a filing accepted at 20:30 UTC is accepted after the 16:00 ET close,
so the first tradable price is the NEXT session's close. Comparing the
acceptance timestamp against a bare bar date instead -- the obvious
implementation -- buys at a close that had already printed, and manufactures
alpha that survives every robustness test because the leak is in the timestamp,
not the statistics.

Everything reports coverage. This universe loses roughly a quarter of its 2010
names to delisting (see README), and a result that only exists in thin
cross-sections is a data artifact, not a discovery.
"""
from __future__ import annotations

import bisect
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from . import panel as panel_mod
from . import spec as spec_mod
from . import stats
from .ingest import prices as prices_mod

NY = ZoneInfo("America/New_York")
EPS = 1e-12


# ---------------------------------------------------------------------------
# wide panel
# ---------------------------------------------------------------------------

def load_wide(*, embargo: bool = False, lag_days: int = 0,
              prices_path: str = "data/prices.parquet") -> pl.DataFrame:
    """One row per (rebalance, ticker): fundamentals by lag, market features,
    forward returns. Built from the panel, never from raw filings."""
    p = panel_mod.load(embargo=embargo)
    tags = p["tag"].unique().to_list()
    base = (p.select(["asof", "ticker", "cik", "gics_sector"])
             .unique(subset=["asof", "ticker"]))
    for tag in tags:
        sub = (p.filter(pl.col("tag") == tag)
                .select(["asof", "ticker"] + [f"q{i}" for i in range(panel_mod.N_LAGS)])
                .rename({f"q{i}": f"{tag}_q{i}" for i in range(panel_mod.N_LAGS)}))
        base = base.join(sub, on=["asof", "ticker"], how="left")

    px = pl.read_parquet(prices_path)
    dates = sorted(base["asof"].unique().to_list())
    mkt = panel_mod.market_table(px, dates, lag_days=lag_days)

    uni = pl.read_parquet("data/universe.parquet")
    if "price_ticker" in uni.columns:
        m = dict(uni.select(["ticker", "price_ticker"]).unique().rows())
        base = base.with_columns(pl.col("ticker").replace(m).alias("price_ticker"))
    else:
        base = base.with_columns(pl.col("ticker").alias("price_ticker"))

    out = base.join(mkt, left_on=["asof", "price_ticker"], right_on=["asof", "ticker"],
                    how="left")
    if "SharesOutstanding_q0" in out.columns:
        out = out.with_columns(
            (pl.col("SharesOutstanding_q0") * pl.col("entry_close")).alias("mktcap"))
    else:
        out = out.with_columns(pl.lit(None, dtype=pl.Float64).alias("mktcap"))
    return out


# ---------------------------------------------------------------------------
# expression evaluation
# ---------------------------------------------------------------------------

def _safe_div(a: pl.Expr, b: pl.Expr) -> pl.Expr:
    return pl.when(b.abs() < EPS).then(None).otherwise(a / b)


def eval_expr(node: dict, shift: int = 0) -> pl.Expr:
    """Compile a spec expression to a polars expression.

    `shift` moves every leaf back by whole quarters, which is what makes
    growth operators compositional: yoy_growth of a ratio is the ratio now
    against the same ratio four quarters ago.
    """
    if "const" in node:
        return pl.lit(float(node["const"]))
    if "feature" in node:
        feat = node["feature"]
        lag = node.get("lag", 0) + shift
        if feat in spec_mod.MARKET_FEATURES:
            if shift:
                raise spec_mod.SpecError(
                    f"{feat} is a market feature with no quarterly history; "
                    f"it cannot appear inside a growth operator")
            return pl.col(feat)
        if lag >= panel_mod.N_LAGS:
            return pl.lit(None, dtype=pl.Float64)
        return pl.col(f"{feat}_q{lag}")

    op = node["op"]
    if op == "level":
        return eval_expr(node["x"], shift)
    if op == "yoy_growth":
        cur, prior = eval_expr(node["x"], shift), eval_expr(node["x"], shift + 4)
        return _safe_div(cur - prior, prior.abs())
    if op == "qoq_growth":
        cur, prior = eval_expr(node["x"], shift), eval_expr(node["x"], shift + 1)
        return _safe_div(cur - prior, prior.abs())
    if op == "accel":
        now = eval_expr({"op": "yoy_growth", "x": node["x"]}, shift)
        was = eval_expr({"op": "yoy_growth", "x": node["x"]}, shift + 1)
        return now - was
    if op == "log":
        x = eval_expr(node["x"], shift)
        return pl.when(x > EPS).then(x.log()).otherwise(None)
    if op == "neg":
        return -eval_expr(node["x"], shift)
    if op == "abs":
        return eval_expr(node["x"], shift).abs()
    if op == "zscore_xs":
        x = eval_expr(node["x"], shift)
        return _safe_div(x - x.mean().over("asof"), x.std().over("asof"))
    if op == "rank_xs":
        x = eval_expr(node["x"], shift)
        return _safe_div(x.rank().over("asof") - 1.0, x.count().over("asof") - 1.0)
    a, b = eval_expr(node["a"], shift), eval_expr(node["b"], shift)
    if op == "add":
        return a + b
    if op == "sub":
        return a - b
    if op == "mul":
        return a * b
    if op == "div":
        return _safe_div(a, b)
    raise spec_mod.SpecError(f"unknown op {op!r}")


# ---------------------------------------------------------------------------
# cross-sectional engine
# ---------------------------------------------------------------------------

def _neutralize(sig: np.ndarray, sectors: np.ndarray, size: np.ndarray,
                beta: np.ndarray, which: list[str]) -> np.ndarray:
    """Residual of the signal after removing the requested exposures.

    Without this, "my signal works" often means "my signal is short small-caps
    in a decade when large beat small".
    """
    n = len(sig)
    cols = [np.ones(n)]
    if "sector" in which:
        for s in sorted(set(sectors))[1:]:          # drop one level, keep intercept
            cols.append((sectors == s).astype(float))
    if "size" in which:
        z = np.where(np.isfinite(size) & (size > 0), np.log(np.maximum(size, EPS)), np.nan)
        cols.append(np.nan_to_num(z, nan=float(np.nanmean(z)) if np.isfinite(z).any() else 0.0))
    if "beta" in which:
        b = np.where(np.isfinite(beta), beta, np.nan)
        cols.append(np.nan_to_num(b, nan=float(np.nanmean(b)) if np.isfinite(b).any() else 0.0))
    X = np.column_stack(cols)
    coef, *_ = np.linalg.lstsq(X, sig, rcond=None)
    return sig - X @ coef


def _winsorize(x: np.ndarray, frac: float) -> np.ndarray:
    if frac <= 0:
        return x
    lo, hi = np.quantile(x, [frac, 1 - frac])
    return np.clip(x, lo, hi)


def _factor_windows(fac: pl.DataFrame, entries: list[date]) -> dict[date, dict]:
    """Compound daily factor returns over each holding period [entry_t, entry_t+1)."""
    fd = fac["date"].to_list()
    names = [c for c in ("mkt_rf", "smb", "hml", "mom", "rf") if c in fac.columns]
    arrs = {n: np.asarray(fac[n].to_list(), dtype=float) for n in names}
    out = {}
    for i in range(len(entries) - 1):
        a = bisect.bisect_left(fd, entries[i])
        b = bisect.bisect_left(fd, entries[i + 1])
        if b <= a:
            continue
        out[entries[i]] = {n: float(np.nanprod(1.0 + arrs[n][a:b]) - 1.0) for n in names}
    return out


def run_cross_sectional(spec: dict, wide: pl.DataFrame, *,
                        factors_path: str = "data/factors.parquet",
                        shuffle_seed: int | None = None) -> dict:
    """shuffle_seed permutes the signal within each cross-section, destroying
    its link to returns while preserving every other property of the test. It
    is the placebo the skeptic runs: if a shuffled signal still 'works', the
    finding belongs to the machinery, not the data."""
    s = spec_mod.validate(spec)
    horizon = s["horizon_days"]
    fwd_col = f"fwd_{horizon}"
    interval = spec_mod.INTERVALS[s["rebalance"]]
    step = max(1, interval // 21)

    df = wide.with_columns(eval_expr(s["signal"]).alias("signal"))
    all_dates = sorted(df["asof"].unique().to_list())
    if s.get("start"):
        all_dates = [d for d in all_dates if d >= date.fromisoformat(s["start"])]
    if s.get("end"):
        all_dates = [d for d in all_dates if d <= date.fromisoformat(s["end"])]
    use_dates = all_dates[::step]                  # non-overlapping holding periods

    rng = np.random.default_rng(shuffle_seed) if shuffle_seed is not None else None
    per_period, prev_w = [], {}
    for d in use_dates:
        g = df.filter((pl.col("asof") == d) &
                      pl.col("signal").is_finite() &
                      pl.col(fwd_col).is_not_null())
        members = int(df.filter(pl.col("asof") == d).height)
        n = g.height
        if n < s["min_names"]:
            per_period.append({"asof": d, "n": n, "members": members, "skipped": True})
            continue

        sig = _winsorize(np.asarray(g["signal"].to_list(), dtype=float), s["winsorize"])
        fwd = np.asarray(g[fwd_col].to_list(), dtype=float)
        if rng is not None:
            sig = rng.permutation(sig)
        if s["neutralize"]:
            sig = _neutralize(
                sig,
                np.asarray(g["gics_sector"].fill_null("UNKNOWN").to_list()),
                np.asarray(g["mktcap"].to_list(), dtype=float),
                np.asarray(g["beta_252"].to_list(), dtype=float),
                s["neutralize"])

        order = np.argsort(sig)
        k = max(1, n // 10)
        lo_idx, hi_idx = order[:k], order[-k:]
        tickers = g["ticker"].to_list()
        w = {}
        for i in hi_idx:
            w[tickers[i]] = 1.0 / len(hi_idx)
        for i in lo_idx:
            w[tickers[i]] = w.get(tickers[i], 0.0) - 1.0 / len(lo_idx)

        gross = float(fwd[hi_idx].mean() - fwd[lo_idx].mean())
        traded = sum(abs(w.get(t, 0.0) - prev_w.get(t, 0.0))
                     for t in set(w) | set(prev_w))
        cost = traded * s["cost_bps"] / 1e4
        prev_w = w

        per_period.append({
            "asof": d, "n": n, "members": members, "skipped": False,
            "gross": gross, "cost": cost, "net": gross - cost, "turnover": traded,
            "ic": stats.spearman_ic(sig, fwd),
            "long_ret": float(fwd[hi_idx].mean()), "short_ret": float(fwd[lo_idx].mean()),
            "entry_date": g["entry_date"].min(),
        })

    used = [p for p in per_period if not p["skipped"]]
    if len(used) < 8:
        return {"engine": "cross_sectional", "error": "too few usable rebalances",
                "n_periods": len(used), "per_period": per_period}

    net = np.asarray([p["net"] for p in used], dtype=float)
    # What you would actually earn. A hypothesis predicting a negative spread is
    # traded short-high/long-low, so its return series is -net. Scoring Sharpe
    # and DSR on the raw high-minus-low series instead would give every
    # correctly-predicted negative hypothesis a negative Sharpe and fail it on
    # arithmetic rather than evidence.
    signed = net * s["predicted_sign"]
    gross_arr = np.asarray([p["gross"] for p in used], dtype=float)
    ic = np.asarray([p["ic"] for p in used], dtype=float)
    entries = [p["entry_date"] for p in used]

    res = {
        "engine": "cross_sectional",
        "spec_description": spec_mod.describe(s),
        "n_periods": len(used),
        "period_first": str(used[0]["asof"]), "period_last": str(used[-1]["asof"]),
        "mean_net": float(np.mean(net)), "mean_gross": float(np.mean(gross_arr)),
        "t_net": stats.newey_west_tstat(net),
        # Gross t is the placebo's yardstick. A shuffled signal reshuffles the
        # deciles every month, pays full turnover costs, and so reliably loses
        # money -- which is a mechanical artifact of trading, not evidence that
        # the shuffled signal predicts anything. Judging a placebo on net would
        # flag that drag as a finding (and for a short hypothesis it inverts
        # into an apparent profit).
        "t_gross": stats.newey_west_tstat(gross_arr),
        "sharpe": stats.sharpe(signed),
        "mean_signed": float(np.mean(signed)),
        "mean_ic": float(np.nanmean(ic)), "t_ic": stats.newey_west_tstat(ic[np.isfinite(ic)]),
        "hit_rate": float(np.mean(net > 0)),
        "mean_turnover": float(np.mean([p["turnover"] for p in used])),
        "mean_cost": float(np.mean([p["cost"] for p in used])),
        "max_drawdown": stats.max_drawdown(signed),
        "mean_names": float(np.mean([p["n"] for p in used])),
        "coverage": float(np.mean([p["n"] / max(p["members"], 1) for p in used])),
        "skipped_periods": len(per_period) - len(used),
        "returns": [float(x) for x in net],
        "returns_signed": [float(x) for x in signed],
        "period_dates": [str(p["asof"]) for p in used],
    }
    lo, hi = stats.block_bootstrap_mean_ci(net, block=max(3, int(len(net) ** (1 / 3))))
    res["mean_net_ci"] = [lo, hi]

    try:
        res.update(_factor_alpha(net, entries, factors_path))
    except Exception as exc:                        # noqa: BLE001
        res["factor_alpha_error"] = str(exc)
    return res


def _factor_alpha(net: np.ndarray, entries: list[date], factors_path: str) -> dict:
    """Alpha after Fama-French 3 + momentum. A spread that loses its t-stat
    here is a factor bet someone already charges 5bps for."""
    import statsmodels.api as sm

    fac = pl.read_parquet(factors_path)
    windows = _factor_windows(fac, entries + [entries[-1] + timedelta(days=400)])
    rows = [(net[i], windows[e]) for i, e in enumerate(entries) if e in windows]
    if len(rows) < 12:
        return {"factor_alpha_error": "too few overlapping factor windows"}
    y = np.asarray([r[0] for r in rows], dtype=float)
    cols = ["mkt_rf", "smb", "hml", "mom"]
    X = np.column_stack([[r[1].get(c, 0.0) for r in rows] for c in cols])
    ok = np.all(np.isfinite(X), axis=1) & np.isfinite(y)
    if ok.sum() < 12:
        return {"factor_alpha_error": "too few finite factor observations"}
    model = sm.OLS(y[ok], sm.add_constant(X[ok])).fit(
        cov_type="HAC", cov_kwds={"maxlags": 3})
    return {
        "ff4_alpha": float(model.params[0]),
        "ff4_alpha_t": float(model.tvalues[0]),
        "ff4_betas": {c: float(b) for c, b in zip(cols, model.params[1:])},
        "ff4_r2": float(model.rsquared),
    }


# ---------------------------------------------------------------------------
# event study engine
# ---------------------------------------------------------------------------

def session_end_utc(d: date) -> datetime:
    """16:00 America/New_York on date d, in UTC. DST-aware on purpose."""
    return datetime.combine(d, time(16, 0), tzinfo=NY).astimezone(timezone.utc)


def entry_index(bar_dates: list[date], known_at: datetime) -> int | None:
    """First bar whose session close happens strictly after the filing was
    public. This is the whole point-in-time discipline of an event study."""
    i = bisect.bisect_left(bar_dates, known_at.astimezone(NY).date())
    while i < len(bar_dates):
        if session_end_utc(bar_dates[i]) > known_at:
            return i
        i += 1
    return None


def _parse_accepted(s: str | None) -> datetime | None:
    if not s:
        return None
    t = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(t)
    except ValueError:
        return None
    if dt.tzinfo is None:
        # EDGAR acceptance stamps without a zone are Eastern.
        dt = dt.replace(tzinfo=NY)
    return dt.astimezone(timezone.utc)


def run_event_study(spec: dict, *, events_path: str = "data/events.parquet",
                    prices_path: str = "data/prices.parquet",
                    universe_path: str = "data/universe.parquet",
                    embargo: bool = False, shuffle_seed: int | None = None) -> dict:
    s = spec_mod.validate(spec)
    w0, w1 = s["window"]
    trig = s["event"]["trigger"]

    ev = pl.read_parquet(events_path)
    if trig.startswith("8k_"):
        ev = ev.filter(pl.col("form").str.starts_with("8-K") &
                       pl.col("items").str.contains(trig.split("_", 1)[1], literal=True))
    elif trig == "10q_filed":
        ev = ev.filter(pl.col("form") == "10-Q")
    elif trig == "10k_filed":
        ev = ev.filter(pl.col("form") == "10-K")

    cut = panel_mod.EMBARGO_START
    ev = ev.filter(pl.col("filing_date") >= cut if embargo else pl.col("filing_date") < cut)
    if s.get("start"):
        ev = ev.filter(pl.col("filing_date") >= date.fromisoformat(s["start"]))

    uni = pl.read_parquet(universe_path)
    px = pl.read_parquet(prices_path)
    parts = {(k[0] if isinstance(k, tuple) else k): v
             for k, v in px.sort(["ticker", "date"]).partition_by("ticker", as_dict=True).items()}

    series: dict[str, tuple[list[date], np.ndarray]] = {}
    for t, d in parts.items():
        closes = np.asarray(d["close"].to_list(), dtype=float)
        series[t] = (d["date"].to_list(), closes)

    def rets(t):
        dts, c = series[t]
        return dts, np.concatenate([[np.nan], c[1:] / c[:-1] - 1.0]), c

    spy = rets("SPY") if "SPY" in series else None

    # cik -> membership intervals, sector, price ticker
    uni_rows = uni.rows(named=True)
    by_cik: dict[str, list[dict]] = {}
    for r in uni_rows:
        if r["cik"]:
            by_cik.setdefault(r["cik"], []).append(r)

    records: list[dict] = []
    skipped = 0

    for row in ev.rows(named=True):
        recs = by_cik.get(row["cik"])
        if not recs:
            skipped += 1
            continue
        fd = row["filing_date"]
        rec = next((r for r in recs
                    if r["start"] <= fd and (r["end"] is None or r["end"] > fd)), None)
        if rec is None:
            skipped += 1
            continue
        tkr = rec.get("price_ticker") or rec["ticker"]
        if tkr not in series:
            skipped += 1
            continue
        known = _parse_accepted(row["accepted_utc"])
        if known is None:
            # No acceptance stamp: assume it became public after that day's
            # close rather than guessing an intraday time in our favour.
            known = session_end_utc(fd) + timedelta(seconds=1)

        dts, r_stock, closes = rets(tkr)
        i = entry_index(dts, known)
        if i is None or i + w1 >= len(dts):
            skipped += 1
            continue

        bench_t = tkr
        if s["benchmark"] == "sector":
            etf = prices_mod.SECTOR_ETF_MAP.get(rec.get("gics_sector") or "", "SPY")
            bench_t = etf if etf in series else "SPY"
        else:
            bench_t = "SPY"
        if bench_t not in series:
            skipped += 1
            continue
        bd, r_bench, bclose = rets(bench_t)
        # align benchmark bars to the stock's dates
        bidx = {d: j for j, d in enumerate(bd)}
        js = [bidx.get(dts[k]) for k in range(i, min(i + w1 + 1, len(dts)))]
        if any(j is None for j in js):
            skipped += 1
            continue

        seg_stock = r_stock[i + w0: i + w1 + 1]
        seg_bench = np.asarray([r_bench[j] for j in js[w0:w1 + 1]], dtype=float)
        if len(seg_stock) != len(seg_bench) or not np.isfinite(seg_stock).all():
            skipped += 1
            continue

        excess = seg_stock - seg_bench
        # The buy-and-hold leg must start at the close BEFORE the window's first
        # return bar, not at the entry bar. With a window of [2, 61] those differ
        # by the day-1 reaction -- and when that same reaction is the selection
        # criterion, including it measures the condition instead of the drift and
        # prints a large fake effect. CAR excludes it; BHAR must too.
        s0, s1 = closes[i + w0 - 1], closes[i + w1]
        b0, b1 = bclose[js[w0 - 1]], bclose[js[w1]]

        # Reaction is the first full session after the filing was public. It is
        # only used to split events, and the outcome window starts at +2.
        reaction = None
        if i + 1 < len(dts) and js[1] is not None:
            rs, rb = r_stock[i + 1], r_bench[js[1]]
            if np.isfinite(rs) and np.isfinite(rb):
                reaction = float(rs - rb)

        records.append({
            "ticker": rec["ticker"], "date": fd,
            "car": float(np.nansum(excess)),
            "bhar": float((s1 / s0 - 1.0) - (b1 / b0 - 1.0)),
            "excess": excess, "reaction": reaction,
        })

    cond = s.get("condition")
    n_all = len(records)
    if cond:
        records = [r for r in records if r["reaction"] is not None]
        if shuffle_seed is not None:
            # Placebo: keep the same events and the same selected count, but
            # sever the link between the reaction and the event it belongs to.
            rng = np.random.default_rng(shuffle_seed)
            shuffled = rng.permutation([r["reaction"] for r in records])
            for r, v in zip(records, shuffled):
                r["reaction"] = float(v)
        # Rank within calendar quarter: earnings arrive in seasons, and ranking
        # a 2012 reaction against a 2020 one would sort on regime, not surprise.
        buckets: dict[tuple, list[dict]] = {}
        for r in records:
            buckets.setdefault((r["date"].year, (r["date"].month - 1) // 3), []).append(r)
        picked = []
        for group in buckets.values():
            group.sort(key=lambda r: r["reaction"])
            k = max(1, int(round(len(group) * cond["fraction"])))
            picked.extend(group[-k:] if cond["select"] == "top" else group[:k])
        records = picked

    n = len(records)
    if n < s["min_events"]:
        return {"engine": "event_study", "error": "too few events",
                "n_events": n, "n_before_condition": n_all,
                "min_events": s["min_events"], "skipped": skipped}

    arr = np.asarray([r["bhar"] for r in records], dtype=float)
    signed = arr * s["predicted_sign"]      # see run_cross_sectional
    car_arr = np.asarray([r["car"] for r in records], dtype=float)
    path_sum = np.zeros(w1 - w0 + 1)
    path_n = np.zeros(w1 - w0 + 1)
    for r in records:
        cum = np.nancumsum(r["excess"])
        path_sum[:len(cum)] += cum
        path_n[:len(cum)] += 1

    top10 = np.sort(np.abs(arr))[-10:].sum()
    lo, hi = stats.block_bootstrap_mean_ci(arr, block=max(5, n // 50))
    n_names = len({r["ticker"] for r in records})

    return {
        "engine": "event_study",
        "spec_description": spec_mod.describe(s),
        "trigger": trig, "window": [w0, w1], "benchmark": s["benchmark"],
        "condition": cond,
        "n_events": n, "n_before_condition": n_all, "skipped": skipped,
        "n_distinct_names": n_names,
        "mean_bhar": float(arr.mean()), "median_bhar": float(np.median(arr)),
        "t_bhar": stats.newey_west_tstat(arr),
        "mean_bhar_ci": [lo, hi],
        "mean_car": float(car_arr.mean()),
        "hit_rate": float(np.mean(arr > 0)),
        "top10_share": float(top10 / max(np.abs(arr).sum(), EPS)),
        "car_path": [float(x) for x in (path_sum / np.maximum(path_n, 1))],
        "returns": [float(x) for x in arr],
        "returns_signed": [float(x) for x in signed],
        "sharpe": stats.sharpe(signed),
        "first_event": str(min(r["date"] for r in records)),
        "last_event": str(max(r["date"] for r in records)),
    }


# ---------------------------------------------------------------------------

def run(spec: dict, *, wide: pl.DataFrame | None = None, embargo: bool = False) -> dict:
    """Dispatch. The only entry point any experiment should use."""
    s = spec_mod.validate(spec)
    if s["type"] == "cross_sectional":
        if wide is None:
            wide = load_wide(embargo=embargo, lag_days=s["lag_days"])
        return run_cross_sectional(s, wide)
    return run_event_study(s, embargo=embargo)
