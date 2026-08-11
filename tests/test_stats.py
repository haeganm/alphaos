"""Checks on the math that decides what counts as evidence.

The DSR checks are against closed forms computed by hand here, not against a
remembered table from the paper -- a test you can re-derive is worth more than
one you have to trust.
"""
import math

import numpy as np
import pytest

from alphaos import stats


def test_sharpe_is_per_period():
    r = np.array([0.01, -0.005, 0.02, 0.0, 0.015])
    assert stats.sharpe(r) == pytest.approx(r.mean() / r.std(ddof=1))


def test_psr_matches_closed_form_under_normality():
    # skew=0, kurt=3, benchmark=0  =>  PSR = Z[ SR*sqrt(T-1) / sqrt(1 + SR^2/2) ]
    sr, t = 0.12, 250
    expected = stats._N.cdf(sr * math.sqrt(t - 1) / math.sqrt(1 + 0.5 * sr ** 2))
    assert stats.probabilistic_sharpe(sr, t, 0.0, 3.0) == pytest.approx(expected)


def test_negative_skew_and_fat_tails_lower_psr():
    base = stats.probabilistic_sharpe(0.12, 250, 0.0, 3.0)
    skewed = stats.probabilistic_sharpe(0.12, 250, -1.5, 3.0)
    fat = stats.probabilistic_sharpe(0.12, 250, 0.0, 9.0)
    assert skewed < base and fat < base


def test_expected_max_sharpe_grows_with_trials():
    a = stats.expected_max_sharpe(10, 0.01)
    b = stats.expected_max_sharpe(1000, 0.01)
    assert 0 < a < b
    # one trial is no selection at all
    assert stats.expected_max_sharpe(1, 0.01) == 0.0


def test_dsr_degrades_with_trial_count():
    """The headline behaviour of the whole lab: same series, more trials, less
    credit. Sharpe 1.5 on trial 2000 must not read like Sharpe 1.5 on trial 5."""
    rng = np.random.default_rng(7)
    r = rng.normal(0.008, 0.05, 400)
    few = stats.deflated_sharpe(r, n_trials=5, sr_variance=0.01)
    many = stats.deflated_sharpe(r, n_trials=2000, sr_variance=0.01)
    assert few["sharpe"] == many["sharpe"]          # same series
    assert many["dsr"] < few["dsr"]                  # different evidence
    assert many["expected_max_sharpe"] > few["expected_max_sharpe"]


def test_single_trial_dsr_equals_psr():
    rng = np.random.default_rng(3)
    r = rng.normal(0.005, 0.04, 300)
    d = stats.deflated_sharpe(r, n_trials=1, sr_variance=float("nan"))
    assert d["dsr"] == pytest.approx(d["psr"])


def test_newey_west_penalises_autocorrelation():
    """Positively autocorrelated series must get a smaller t than its iid twin."""
    rng = np.random.default_rng(11)
    n = 500
    e = rng.normal(0, 1, n)
    ar = np.zeros(n)
    for i in range(1, n):
        ar[i] = 0.8 * ar[i - 1] + e[i]
    ar += 0.5
    naive_t = ar.mean() / (ar.std(ddof=1) / math.sqrt(n))
    assert abs(stats.newey_west_tstat(ar)) < abs(naive_t)


def test_block_bootstrap_ci_brackets_the_mean():
    rng = np.random.default_rng(5)
    x = rng.normal(0.01, 0.03, 600)
    lo, hi = stats.block_bootstrap_mean_ci(x, block=20)
    assert lo < x.mean() < hi
    # a zero-mean series should not produce a CI that excludes zero
    z = rng.normal(0.0, 0.03, 600)
    zlo, zhi = stats.block_bootstrap_mean_ci(z, block=20)
    assert zlo < 0 < zhi


def test_spearman_ic_signs():
    s = np.arange(20, dtype=float)
    assert stats.spearman_ic(s, s) == pytest.approx(1.0)
    assert stats.spearman_ic(s, -s) == pytest.approx(-1.0)
    assert math.isnan(stats.spearman_ic([1.0, 2.0], [1.0, 2.0]))  # too few names


def test_rankdata_averages_ties():
    r = stats._rankdata(np.array([10.0, 10.0, 20.0]))
    assert r.tolist() == [1.5, 1.5, 3.0]


def test_max_drawdown():
    r = np.array([0.1, -0.5, 0.2])
    assert stats.max_drawdown(r) == pytest.approx(-0.5)
