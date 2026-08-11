"""Statistics for the lab. Pure functions, no I/O, no pandas dependency.

Two ideas carry most of the weight here:

1. Deflated Sharpe Ratio (Bailey & Lopez de Prado 2014). A Sharpe of 1.5 on
   trial 2000 is not the same evidence as a Sharpe of 1.5 on trial 5. DSR is
   the mechanical form of that sentence.
2. Moving-block bootstrap. Overlapping-horizon returns are autocorrelated;
   iid confidence intervals overstate power badly (BTCTA measured ~24x at a
   24h horizon). Every interval in this lab is blocked.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import numpy as np

GAMMA = 0.5772156649015329  # Euler-Mascheroni
_N = NormalDist()

# Moving-block bootstrap defaults, lifted from C:\BTCTA\btcta\config.py.
BOOT_N = 2000
BOOT_SEED = 42


# --------------------------------------------------------------------------
# Sharpe family
# --------------------------------------------------------------------------

def sharpe(returns) -> float:
    """Per-period Sharpe (NOT annualized). Everything downstream expects
    per-period units -- annualizing before deflating corrupts the DSR."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size < 2:
        return float("nan")
    sd = r.std(ddof=1)
    if sd == 0:
        return float("nan")
    return float(r.mean() / sd)


def _moments(returns):
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = r.size
    if n < 3:
        return n, float("nan"), float("nan")
    sd = r.std(ddof=1)
    if sd == 0:
        return n, float("nan"), float("nan")
    z = (r - r.mean()) / sd
    skew = float(np.mean(z ** 3))
    kurt = float(np.mean(z ** 4))  # raw kurtosis: normal == 3
    return n, skew, kurt


def probabilistic_sharpe(sr: float, n_obs: int, skew: float, kurt: float,
                         sr_benchmark: float = 0.0) -> float:
    """P(true SR > sr_benchmark) given non-normal returns.

    PSR = Z[ (SR - SR*) * sqrt(T-1) / sqrt(1 - g3*SR + (g4-1)/4 * SR^2) ]
    """
    if not all(math.isfinite(v) for v in (sr, skew, kurt)) or n_obs < 3:
        return float("nan")
    var = 1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr ** 2
    if var <= 0:
        # Heavy enough tails that the estimator variance is undefined. Refusing
        # to score is the honest answer; NaN propagates to a NO EDGE verdict.
        return float("nan")
    return float(_N.cdf((sr - sr_benchmark) * math.sqrt(n_obs - 1) / math.sqrt(var)))


def expected_max_sharpe(n_trials: int, sr_variance: float) -> float:
    """E[max SR] across n_trials whose true Sharpe is all zero.

    This is the bar a mined result has to clear just to be unremarkable. It
    grows with the number of things you tried and with how spread out their
    Sharpes were.
    """
    if n_trials <= 1 or not math.isfinite(sr_variance) or sr_variance <= 0:
        return 0.0
    z1 = _N.inv_cdf(1.0 - 1.0 / n_trials)
    z2 = _N.inv_cdf(1.0 - 1.0 / (n_trials * math.e))
    return float(math.sqrt(sr_variance) * ((1.0 - GAMMA) * z1 + GAMMA * z2))


def deflated_sharpe(returns, n_trials: int, sr_variance: float) -> dict:
    """DSR for a return series, discounted for how many trials produced it.

    sr_variance is the variance of the Sharpe ratios across the family's
    trials -- the registry knows it (registry.family_sharpe_variance). With a
    single trial the deflation term is zero and DSR degenerates to PSR vs 0,
    which is the correct behaviour, not a fallback.
    """
    n, skew, kurt = _moments(returns)
    sr = sharpe(returns)
    sr0 = expected_max_sharpe(n_trials, sr_variance)
    return {
        "sharpe": sr,
        "n_obs": n,
        "skew": skew,
        "kurtosis": kurt,
        "n_trials": int(n_trials),
        "sr_variance": float(sr_variance) if math.isfinite(sr_variance) else float("nan"),
        "expected_max_sharpe": sr0,
        "psr": probabilistic_sharpe(sr, n, skew, kurt, 0.0),
        "dsr": probabilistic_sharpe(sr, n, skew, kurt, sr0),
    }


# --------------------------------------------------------------------------
# Autocorrelation-aware inference
# --------------------------------------------------------------------------

def newey_west_tstat(x, lags: int | None = None) -> float:
    """t-stat for mean(x) != 0 with a HAC (Newey-West) standard error.

    Plain OLS t-stats on overlapping-horizon returns are inflated. Default lag
    is the standard 4*(n/100)^(2/9) rule.
    """
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    n = a.size
    if n < 3:
        return float("nan")
    if lags is None:
        lags = int(math.floor(4 * (n / 100.0) ** (2.0 / 9.0)))
    lags = max(0, min(lags, n - 2))
    e = a - a.mean()
    s = float(np.dot(e, e) / n)
    for l in range(1, lags + 1):
        cov = float(np.dot(e[l:], e[:-l]) / n)
        s += 2.0 * (1.0 - l / (lags + 1.0)) * cov
    if s <= 0:
        return float("nan")
    se = math.sqrt(s / n)
    if se == 0:
        return float("nan")
    return float(a.mean() / se)


def default_block(n: int) -> int:
    """The one block-length rule, used everywhere.

    Four different conventions had grown across the harness, the holdout and
    this module, so discovery and embargo computed the decisive interval on the
    same series with different blocks. n**(1/3) is the standard choice for the
    moving-block bootstrap; the floor of 5 keeps short series from degenerating
    to an iid bootstrap, which is the error this whole approach exists to avoid.
    """
    return max(5, int(round(n ** (1.0 / 3.0))))


def block_bootstrap_mean_ci(x, block: int | None = None, n_boot: int = BOOT_N,
                            seed: int = BOOT_SEED, alpha: float = 0.05):
    """Moving-block bootstrap CI for the mean of an autocorrelated series.

    Technique lifted from BTCTA's block_bootstrap_bss_ci; the statistic is the
    mean of a return/CAR series rather than a Brier skill score, because that
    is what this lab actually measures. Block defaults to 2x the natural
    dependence length the caller passes, floored at 5.
    """
    a = np.asarray(x, dtype=float)
    a = a[np.isfinite(a)]
    n = a.size
    if block is None:
        block = default_block(n)
    block = max(1, min(int(block), n))
    if n <= block or n < 10:
        return float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    n_blocks = int(math.ceil(n / block))
    starts = rng.integers(0, n - block + 1, size=(n_boot, n_blocks))
    offs = np.arange(block)
    idx = (starts[:, :, None] + offs).reshape(n_boot, -1)[:, :n]
    means = a[idx].mean(axis=1)
    lo = float(np.percentile(means, 100 * alpha / 2))
    hi = float(np.percentile(means, 100 * (1 - alpha / 2)))
    return lo, hi


# --------------------------------------------------------------------------
# Cross-sectional helpers
# --------------------------------------------------------------------------

def spearman_ic(signal, forward_return) -> float:
    """Rank IC for one cross-section. NaN if fewer than 5 usable names."""
    s = np.asarray(signal, dtype=float)
    f = np.asarray(forward_return, dtype=float)
    ok = np.isfinite(s) & np.isfinite(f)
    if ok.sum() < 5:
        return float("nan")
    s, f = s[ok], f[ok]
    rs = _rankdata(s)
    rf = _rankdata(f)
    rs = rs - rs.mean()
    rf = rf - rf.mean()
    denom = math.sqrt(float(np.dot(rs, rs)) * float(np.dot(rf, rf)))
    if denom == 0:
        return float("nan")
    return float(np.dot(rs, rf) / denom)


def _rankdata(a):
    """Average-rank of a 1-D array (ties shared), no scipy import needed."""
    order = np.argsort(a, kind="mergesort")
    ranks = np.empty(a.size, dtype=float)
    ranks[order] = np.arange(1, a.size + 1, dtype=float)
    # average tied ranks
    uniq, inv, counts = np.unique(a, return_inverse=True, return_counts=True)
    if counts.max() > 1:
        sums = np.zeros(uniq.size)
        np.add.at(sums, inv, ranks)
        ranks = (sums / counts)[inv]
    return ranks


def max_drawdown(returns) -> float:
    """Max drawdown of the compounded series, as a negative fraction."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if r.size == 0:
        return float("nan")
    curve = np.cumprod(1.0 + r)
    peak = np.maximum.accumulate(curve)
    return float((curve / peak - 1.0).min())
