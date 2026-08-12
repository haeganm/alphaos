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
import os
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import polars as pl

from . import panel as panel_mod
from . import paths
from . import spec as spec_mod
from . import stats
from .ingest import prices as prices_mod

NY = ZoneInfo("America/New_York")
EPS = 1e-12


# ---------------------------------------------------------------------------
# wide panel
# ---------------------------------------------------------------------------

def load_wide(*, embargo: bool = False, lag_days: int = 0, all_data: bool = False,
              prices_path=paths.PRICES, universe_path=paths.UNIVERSE) -> pl.DataFrame:
    """One row per (rebalance, ticker): fundamentals by lag, market features,
    forward returns. Built from the panel, never from raw filings.

    lag_days must match the spec being run. It shifts the entry bar, so a frame
    built at one lag cannot be reused for a spec registered at another -- that
    mismatch previously meant discovery ran at lag 0 while the holdout ran at
    the registered lag, evaluating the same frozen spec two different ways.

    all_data concatenates discovery + embargo and is reserved for grading
    already-frozen specs (see panel.load_all).
    """
    p = panel_mod.load_all() if all_data else panel_mod.load(embargo=embargo)
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

    uni = pl.read_parquet(universe_path)
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
    return out.with_columns(pl.lit(lag_days).alias("_lag_days"))


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
                beta: np.ndarray, which: list[str]) -> tuple[np.ndarray, dict]:
    """Residual of the signal after removing the requested exposures.

    Without this, "my signal works" often means "my signal is short small-caps
    in a decade when large beat small".

    Returns coverage alongside the residual, because a silently-skipped
    neutraliser is worse than none at all: when mktcap or beta is entirely
    missing, the column collapses to a constant, lstsq accepts the
    rank-deficient design without complaint, and the result is still labelled
    "neutralised on size". Missing values are mean-imputed, which leaves those
    names carrying their full raw deviation and pushes them toward the extreme
    deciles, so the imputed fraction is reported too.
    """
    n = len(sig)
    cols = [np.ones(n)]
    cover: dict[str, float] = {}

    if "sector" in which:
        levels = sorted(set(sectors))
        for s in levels[1:]:                        # drop one level, keep intercept
            cols.append((sectors == s).astype(float))
        known = float(np.mean([s != "UNKNOWN" for s in sectors]))
        cover["sector"] = known if len(levels) > 1 else 0.0
    if "size" in which:
        ok = np.isfinite(size) & (size > 0)
        z = np.where(ok, np.log(np.maximum(size, EPS)), np.nan)
        cover["size"] = float(ok.mean())
        cols.append(np.nan_to_num(z, nan=float(np.nanmean(z)) if ok.any() else 0.0))
    if "beta" in which:
        ok = np.isfinite(beta)
        cover["beta"] = float(ok.mean())
        cols.append(np.where(ok, beta, float(np.nanmean(beta)) if ok.any() else 0.0))

    X = np.column_stack(cols)
    coef, *_ = np.linalg.lstsq(X, sig, rcond=None)
    return sig - X @ coef, cover


def _winsorize(x: np.ndarray, frac: float) -> np.ndarray:
    if frac <= 0:
        return x
    lo, hi = np.quantile(x, [frac, 1 - frac])
    return np.clip(x, lo, hi)


def _factor_windows(fac: pl.DataFrame, entries: list[date],
                    horizon: int) -> dict[date, dict]:
    """Compound daily factor returns over each holding period [entry_t, entry_t+1).

    Windows grossly longer than the nominal horizon are dropped. Previously a
    synthetic 400-day sentinel was appended to close the last window, pairing
    400 days of compounded market return with a 21-day strategy return -- an
    enormous leverage point in the alpha regression. The same thing happened
    either side of any skipped rebalance.
    """
    fd = fac["date"].to_list()
    names = [c for c in ("mkt_rf", "smb", "hml", "mom") if c in fac.columns]
    arrs = {n: np.asarray(fac[n].to_list(), dtype=float) for n in names}
    out = {}
    for i in range(len(entries) - 1):
        a = bisect.bisect_left(fd, entries[i])
        b = bisect.bisect_left(fd, entries[i + 1])
        n_days = b - a
        if n_days <= 0 or not (0.5 * horizon <= n_days <= 1.5 * horizon):
            continue
        out[entries[i]] = {n: float(np.nanprod(1.0 + arrs[n][a:b]) - 1.0) for n in names}
    return out


def run_cross_sectional(spec: dict, wide: pl.DataFrame, *,
                        factors_path=paths.FACTORS,
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

    # The frame carries the lag it was built at; running a spec against a frame
    # built at a different lag silently evaluates different entry dates.
    if "_lag_days" in wide.columns and wide.height:
        built_lag = int(wide["_lag_days"][0])
        if built_lag != s["lag_days"]:
            raise ValueError(
                f"panel was built with lag_days={built_lag} but this spec "
                f"registered lag_days={s['lag_days']}; rebuild with "
                f"load_wide(lag_days={s['lag_days']})")

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
                      pl.col(fwd_col).is_not_null()).sort("ticker")
        members = int(df.filter(pl.col("asof") == d).height)
        n = g.height
        if n < s["min_names"]:
            # Reset the book. Carrying prev_w across a gap charged no turnover
            # for the round trip and implied the portfolio was held through a
            # period whose return is not in the series.
            per_period.append({"asof": d, "n": n, "members": members, "skipped": True})
            prev_w = {}
            continue

        sig = _winsorize(np.asarray(g["signal"].to_list(), dtype=float), s["winsorize"])
        fwd = np.asarray(g[fwd_col].to_list(), dtype=float)
        if rng is not None:
            sig = rng.permutation(sig)
        cover: dict = {}
        if s["neutralize"]:
            sig, cover = _neutralize(
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
            "entry_date": g["entry_date"].min(), "neutralize_coverage": cover,
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
        "hit_rate": float(np.mean(signed > 0)),      # of the tradable series
        "mean_turnover": float(np.mean([p["turnover"] for p in used])),
        "mean_cost": float(np.mean([p["cost"] for p in used])),
        "max_drawdown": stats.max_drawdown(signed),
        "mean_names": float(np.mean([p["n"] for p in used])),
        "coverage": float(np.mean([p["n"] / max(p["members"], 1) for p in used])),
        "skipped_periods": len(per_period) - len(used),
        "neutralize_coverage": {
            k: float(np.mean([p["neutralize_coverage"].get(k, 0.0) for p in used]))
            for k in (s["neutralize"] or [])},
        "returns": [float(x) for x in net],
        "returns_signed": [float(x) for x in signed],
        "period_dates": [str(p["asof"]) for p in used],
    }
    lo, hi = stats.block_bootstrap_mean_ci(signed)   # one shared block rule
    res["mean_net_ci"] = [lo, hi]

    try:
        res.update(_factor_alpha(net, entries, factors_path, horizon))
    except Exception as exc:                        # noqa: BLE001
        res["factor_alpha_error"] = str(exc)
    return res


def _factor_alpha(net: np.ndarray, entries: list[date], factors_path,
                  horizon: int) -> dict:
    """Alpha after Fama-French 3 + momentum. A spread that loses its t-stat
    here is a factor bet someone already charges 5bps for."""
    import statsmodels.api as sm

    fac = pl.read_parquet(factors_path)
    windows = _factor_windows(fac, entries, horizon)
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


_SERIES_CACHE: dict = {}


def _prepared_series(prices_path) -> dict:
    """Per-ticker (dates, closes, returns), built once and cached.

    Rebuilding this from the 5.6M-bar parquet on every call cost ~10s a time,
    and the skeptic runs a placebo for every event study, so a four-hypothesis
    campaign paid for it eight times.
    """
    key = (str(prices_path), os.path.getmtime(prices_path))
    hit = _SERIES_CACHE.get(key)
    if hit is not None:
        return hit
    px = pl.read_parquet(prices_path).sort(["ticker", "date"])
    out = {}
    for k, d in px.partition_by("ticker", as_dict=True).items():
        t = k[0] if isinstance(k, tuple) else k
        closes = np.asarray(d["close"].to_list(), dtype=float)
        rets = np.concatenate([[np.nan], closes[1:] / closes[:-1] - 1.0])
        dates = d["date"].to_list()
        out[t] = (dates, closes, rets, {dd: j for j, dd in enumerate(dates)})
    _SERIES_CACHE.clear()          # only ever keep the current build
    _SERIES_CACHE[key] = out
    return out


def run_event_study(spec: dict, *, events_path=paths.EVENTS,
                    prices_path=paths.PRICES,
                    universe_path=paths.UNIVERSE,
                    embargo: bool = False, all_data: bool = False,
                    shuffle_seed: int | None = None) -> dict:
    s = spec_mod.validate(spec)
    w0, w1 = s["window"]
    trig = s["event"]["trigger"]
    lag = s["lag_days"]

    if trig.startswith("insider_"):
        # Insider events are derived from Form 4 transactions rather than the
        # 8-K archive, but arrive shaped identically so nothing downstream
        # changes -- including the acceptance-time entry rule.
        from .ingest import insiders as insiders_mod
        ev = insiders_mod.event_table(trig)
    else:
        ev = pl.read_parquet(events_path)
    if trig.startswith("8k_"):
        ev = ev.filter(pl.col("form").str.starts_with("8-K") &
                       pl.col("items").str.contains(trig.split("_", 1)[1], literal=True))
    elif trig == "10q_filed":
        ev = ev.filter(pl.col("form") == "10-Q")
    elif trig == "10k_filed":
        ev = ev.filter(pl.col("form") == "10-K")

    cut = panel_mod.EMBARGO_START
    if not all_data:
        ev = ev.filter(pl.col("filing_date") >= cut if embargo
                       else pl.col("filing_date") < cut)
    if s.get("start"):
        ev = ev.filter(pl.col("filing_date") >= date.fromisoformat(s["start"]))
    if s.get("end"):
        ev = ev.filter(pl.col("filing_date") <= date.fromisoformat(s["end"]))

    uni = pl.read_parquet(universe_path)
    series = _prepared_series(prices_path)

    # cik -> membership intervals, sector, price ticker
    uni_rows = uni.rows(named=True)
    by_cik: dict[str, list[dict]] = {}
    for r in uni_rows:
        if r["cik"]:
            by_cik.setdefault(r["cik"], []).append(r)

    records: list[dict] = []
    skipped = 0
    # For an unconditioned study the placebo has to break the link between the
    # window and the filing itself. Keeping the same names and the same rough
    # period but jittering the entry to a date that is NOT a filing date
    # measures the universe's baseline drift over an identical window -- which
    # is the thing an event study must beat, and is exactly the +0.33% baseline
    # the earnings campaign turned up. Without this the "placebo" for an
    # unconditioned study was a bit-identical rerun of the experiment.
    jitter = (np.random.default_rng(shuffle_seed)
              if shuffle_seed is not None and not s.get("condition") else None)

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

        dts, closes, r_stock, _ = series[tkr]
        i = entry_index(dts, known)
        # lag_days models an implementation delay: the filing is known at the
        # same instant, but the position is opened that many sessions later.
        if i is not None and lag:
            i += lag
        if i is not None and jitter is not None:
            off = int(jitter.integers(40, 250)) * (1 if jitter.integers(0, 2) else -1)
            i += off
        if i is None or i + w1 >= len(dts) or i < 1:
            skipped += 1
            continue

        if s["benchmark"] == "sector":
            etf = prices_mod.SECTOR_ETF_MAP.get(rec.get("gics_sector") or "", "SPY")
            bench_t = etf if etf in series else "SPY"
        else:
            bench_t = "SPY"
        if bench_t not in series:
            skipped += 1
            continue
        _, bclose, r_bench, bidx = series[bench_t]
        js = [bidx.get(dts[k]) for k in range(i, min(i + w1 + 1, len(dts)))]
        if any(j is None for j in js):
            skipped += 1
            continue

        seg_stock = r_stock[i + w0: i + w1 + 1]
        seg_bench = np.asarray([r_bench[j] for j in js[w0:w1 + 1]], dtype=float)
        # Both legs must be checked. Leaving the benchmark unvalidated let a
        # missing benchmark bar become a zero abnormal return via nansum while
        # still counting as a full observation.
        if (len(seg_stock) != len(seg_bench)
                or not np.isfinite(seg_stock).all()
                or not np.isfinite(seg_bench).all()):
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
        rs, rb = r_stock[i + 1], r_bench[js[1]]
        if np.isfinite(rs) and np.isfinite(rb):
            reaction = float(rs - rb)

        records.append({
            "ticker": rec["ticker"], "date": fd, "accn": row.get("accn") or "",
            "car": float(np.nansum(excess)),
            "bhar": float((s1 / s0 - 1.0) - (b1 / b0 - 1.0)),
            "excess": excess, "reaction": reaction,
            # calendar dates the window covers, for calendar-time aggregation
            "win_dates": dts[i + w0: i + w1 + 1],
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

    # Order by date before any blocked resampling. After conditioning, records
    # are ordered by reaction rank within a quarter, so moving blocks would
    # have preserved "similar surprise" adjacency rather than temporal
    # adjacency -- the wrong dependence structure entirely for the CI that
    # decides the verdict.
    records.sort(key=lambda r: (r["date"], r["ticker"], r["accn"]))
    arr = np.asarray([r["bhar"] for r in records], dtype=float)
    car_arr = np.asarray([r["car"] for r in records], dtype=float)

    # Calendar-time aggregation, and it is the statistic that decides the
    # verdict. Treating each event as an independent observation is badly
    # wrong here: 8-K item 2.02 produces 48,276 events on only 4,014 distinct
    # dates (twelve a day, arriving in earnings seasons), and with a 61-day
    # window every event overlaps every other event filed within three months,
    # so the whole 2010-2022 discovery span holds roughly fifty independent
    # blocks. Event-level inference was therefore claiming tens of thousands of
    # observations where the data supplies dozens -- the same error BTCTA
    # measured at ~24x when it moved to blocked resampling.
    #
    # The fix is the standard calendar-time portfolio (Fama 1998): average
    # every active event's excess return within each calendar day, and do
    # inference on that genuinely sequential daily series. Same-day events
    # become one observation because they ARE one observation, and the
    # remaining overlap is handled by Newey-West at the window length.
    by_sum: dict = {}
    by_n: dict = {}
    for r in records:
        for d, x in zip(r["win_dates"], r["excess"]):
            if np.isfinite(x):
                by_sum[d] = by_sum.get(d, 0.0) + float(x)
                by_n[d] = by_n.get(d, 0) + 1
    cal_dates = sorted(by_sum)
    cal = np.asarray([by_sum[d] / by_n[d] for d in cal_dates], dtype=float)
    win_len = w1 - w0 + 1
    cal_signed = cal * s["predicted_sign"]
    t_calendar = stats.newey_west_tstat(cal_signed, lags=win_len)
    # scaled to the window so it is comparable with the per-event BHAR
    calendar_effect = float(cal.mean() * win_len)
    clo, chi = stats.block_bootstrap_mean_ci(cal_signed)
    signed = cal_signed                      # what DSR and the skeptic score
    path_sum = np.zeros(w1 - w0 + 1)
    path_n = np.zeros(w1 - w0 + 1)
    for r in records:
        cum = np.nancumsum(r["excess"])
        path_sum[:len(cum)] += cum
        path_n[:len(cum)] += 1

    top10 = np.sort(np.abs(arr))[-10:].sum()
    n_names = len({r["ticker"] for r in records})

    return {
        "engine": "event_study",
        "spec_description": spec_mod.describe(s),
        "trigger": trig, "window": [w0, w1], "benchmark": s["benchmark"],
        "condition": cond,
        "n_events": n, "n_before_condition": n_all, "skipped": skipped,
        "n_distinct_names": n_names,
        # effect size, per event
        "mean_bhar": float(arr.mean()), "median_bhar": float(np.median(arr)),
        "mean_car": float(car_arr.mean()),
        # inference, on the calendar-time series -- this is what the skeptic
        # scores. t_bhar_naive is retained only so the gap between the two is
        # visible in the record; it is not used for any verdict.
        "t_bhar": t_calendar,
        "t_bhar_naive": stats.newey_west_tstat(arr),
        "calendar_effect": calendar_effect,
        "n_calendar_days": int(cal.size),
        "mean_bhar_ci": [clo * win_len, chi * win_len],
        "hit_rate": float(np.mean(signed > 0)),      # of the tradable series
        "top10_share": float(top10 / max(np.abs(arr).sum(), EPS)),
        "car_path": [float(x) for x in (path_sum / np.maximum(path_n, 1))],
        "returns": [float(x) for x in arr],
        "returns_signed": [float(x) for x in signed],
        "sharpe": stats.sharpe(signed),
        "first_event": str(min(r["date"] for r in records)),
        "last_event": str(max(r["date"] for r in records)),
    }


# ---------------------------------------------------------------------------

def run(spec: dict, *, wide: pl.DataFrame | None = None, embargo: bool = False,
        all_data: bool = False) -> dict:
    """Dispatch. The only entry point any experiment should use."""
    s = spec_mod.validate(spec)
    if s["type"] == "cross_sectional":
        if wide is None:
            wide = load_wide(embargo=embargo, all_data=all_data,
                             lag_days=s["lag_days"])
        return run_cross_sectional(s, wide)
    return run_event_study(s, embargo=embargo, all_data=all_data)
