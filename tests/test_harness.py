"""Tests for the only code allowed to compute a return.

The entry-timing tests matter most. `entry_index` decides whether a filing was
tradable at a given close, and an off-by-one there is invisible downstream: a
leaked signal passes subperiod replication and factor neutralisation exactly as
happily as a real one. crossval_asof.py checks the same rule against an
independent implementation, but it needs the full data build and only covers the
event path, so these pin the boundary directly.
"""
from datetime import date, datetime, timezone

import numpy as np
import polars as pl
import pytest

from alphaos import harness, spec as spec_mod

BARS = [date(2024, 3, 6), date(2024, 3, 7), date(2024, 3, 8),
        date(2024, 3, 11), date(2024, 3, 12)]


def _utc(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=timezone.utc)


# --------------------------------------------------------------------------
# entry timing
# --------------------------------------------------------------------------

def test_filing_before_the_close_is_tradable_that_day():
    # 14:00 UTC = 09:00 ET, well before the 16:00 ET close
    assert harness.entry_index(BARS, _utc(2024, 3, 7, 14)) == 1


def test_filing_after_the_close_waits_for_the_next_session():
    # 2024-03-07 is EST (UTC-5), so the close is 21:00 UTC. 21:30 UTC is 16:30 ET.
    assert harness.entry_index(BARS, _utc(2024, 3, 7, 21, 30)) == 2


def test_filing_exactly_at_the_close_is_not_tradable_at_that_close():
    """Strictly after. A filing accepted at the closing bell did not inform the
    price that printed at that bell."""
    assert harness.entry_index(BARS, _utc(2024, 3, 7, 21)) == 2   # 21:00Z == 16:00 ET


def test_entry_rule_follows_daylight_saving():
    """US DST began 2024-03-10, so 16:00 ET moves from 21:00 UTC to 20:00 UTC.

    A hardcoded UTC offset passes every test either side of this date and is
    wrong for half the year -- silently entering a day late each summer.
    """
    # 2024-03-08 is EST: 20:30 UTC is 15:30 ET, still before that day's close
    assert harness.entry_index(BARS, _utc(2024, 3, 8, 20, 30)) == 2
    # 2024-03-11 is EDT: the same 20:30 UTC is now 16:30 ET, after the close,
    # so the identical wall-clock instant lands on a different session.
    assert harness.entry_index(BARS, _utc(2024, 3, 11, 20, 30)) == 4


def test_filing_after_the_last_bar_has_no_entry():
    assert harness.entry_index(BARS, _utc(2024, 4, 1, 12)) is None


def test_naive_acceptance_stamps_are_read_as_eastern():
    got = harness._parse_accepted("2024-03-07T16:30:00")
    assert got == _utc(2024, 3, 7, 21, 30)
    assert harness._parse_accepted("2024-03-07T20:30:00Z") == _utc(2024, 3, 7, 20, 30)
    assert harness._parse_accepted(None) is None


# --------------------------------------------------------------------------
# expression evaluation
# --------------------------------------------------------------------------

def _frame():
    return pl.DataFrame({
        "asof": [date(2020, 1, 1)] * 4,
        "ticker": ["A", "B", "C", "D"],
        "Revenues_q0": [110.0, 200.0, 50.0, 100.0],
        "Revenues_q4": [100.0, 100.0, 100.0, 100.0],
        "Revenues_q1": [105.0, 150.0, 80.0, 100.0],
        "Revenues_q5": [100.0, 100.0, 100.0, 100.0],
    })


def test_yoy_growth_is_change_over_the_absolute_prior():
    got = _frame().select(harness.eval_expr(
        {"op": "yoy_growth", "x": {"feature": "Revenues"}}).alias("g"))["g"].to_list()
    assert got == pytest.approx([0.10, 1.00, -0.50, 0.0])


def test_accel_is_the_change_in_the_growth_rate():
    """yoy(t) - yoy(t-1). Ticker A: 10% now vs 5% a quarter ago -> +5pp."""
    got = _frame().select(harness.eval_expr(
        {"op": "accel", "x": {"feature": "Revenues"}}).alias("g"))["g"].to_list()
    assert got[0] == pytest.approx(0.05)
    assert got[3] == pytest.approx(0.0)


def test_division_by_zero_yields_null_not_inf():
    df = pl.DataFrame({"asof": [date(2020, 1, 1)] * 2, "Assets_q0": [1.0, 2.0],
                       "Revenues_q0": [0.0, 4.0]})
    got = df.select(harness.eval_expr(
        {"op": "div", "a": {"feature": "Assets"}, "b": {"feature": "Revenues"}}
    ).alias("g"))["g"].to_list()
    assert got[0] is None and got[1] == pytest.approx(0.5)


def test_growth_of_a_market_feature_is_refused():
    """mom_12_1 has no quarterly history; silently returning nulls would read
    as 'the signal had no coverage' rather than 'the spec is meaningless'."""
    with pytest.raises(spec_mod.SpecError, match="no quarterly history"):
        harness.eval_expr({"op": "yoy_growth", "x": {"feature": "mom_12_1"}})


# --------------------------------------------------------------------------
# cross-sectional mechanics
# --------------------------------------------------------------------------

def _panel(n_periods=24, n_names=60, seed=0, signal_is_predictive=True):
    rng = np.random.default_rng(seed)
    rows = []
    for p in range(n_periods):
        asof = date(2015 + p // 12, p % 12 + 1, 1)
        for j in range(n_names):
            sig = rng.normal()
            fwd = 0.02 * sig + rng.normal(scale=0.01) if signal_is_predictive \
                else rng.normal(scale=0.01)
            rows.append({
                "asof": asof, "ticker": f"T{j:03d}", "cik": f"{j:010d}",
                "gics_sector": "Industrials", "mom_12_1": sig,
                "mktcap": 1e9 + j, "beta_252": 1.0, "vol_63": 0.01,
                "adv_21": 1e7, "entry_close": 100.0, "entry_date": asof,
                "fwd_21": fwd, "_lag_days": 0,
            })
    return pl.DataFrame(rows)


SPEC = {"type": "cross_sectional", "signal": {"feature": "mom_12_1"},
        "rebalance": "monthly", "horizon_days": 21, "neutralize": [],
        "cost_bps": 0, "min_names": 20, "predicted_sign": 1}


def test_high_minus_low_orientation():
    """A signal that positively predicts returns must produce a positive
    high-minus-low spread. If this inverts, every verdict inverts."""
    res = harness.run_cross_sectional(SPEC, _panel())
    assert res["mean_net"] > 0
    assert res["mean_ic"] > 0


def test_returns_signed_flips_for_a_short_hypothesis():
    """A correctly-predicted negative spread is a profitable short. Scoring
    Sharpe on the raw spread would fail it on arithmetic, not evidence."""
    res = harness.run_cross_sectional(
        dict(SPEC, predicted_sign=-1), _panel(signal_is_predictive=True))
    assert res["mean_net"] > 0                      # raw spread is positive
    assert np.mean(res["returns_signed"]) < 0        # so the short loses
    assert res["sharpe"] < 0
    assert res["hit_rate"] < 0.5                     # hit rate is on the tradable series


def test_costs_reduce_the_net_spread():
    free = harness.run_cross_sectional(SPEC, _panel())
    paid = harness.run_cross_sectional(dict(SPEC, cost_bps=50), _panel())
    assert paid["mean_net"] < free["mean_net"]
    assert paid["mean_gross"] == pytest.approx(free["mean_gross"])
    assert paid["mean_cost"] > 0


def test_panel_built_at_a_different_lag_is_refused():
    """Discovery previously ran at lag 0 while the holdout ran at the spec's
    registered lag -- the same frozen spec evaluated two different ways."""
    with pytest.raises(ValueError, match="lag_days"):
        harness.run_cross_sectional(dict(SPEC, lag_days=5), _panel())


def test_shuffled_signal_destroys_the_edge():
    real = harness.run_cross_sectional(SPEC, _panel())
    placebo = harness.run_cross_sectional(SPEC, _panel(), shuffle_seed=7)
    assert abs(placebo["t_gross"]) < abs(real["t_net"]) / 2


def test_neutralize_reports_missing_data_instead_of_silently_skipping():
    """An all-null mktcap collapses the design column to a constant; lstsq
    accepts it and the result would still claim to be size-neutralised."""
    p = _panel().with_columns(pl.lit(None, dtype=pl.Float64).alias("mktcap"))
    res = harness.run_cross_sectional(dict(SPEC, neutralize=["size"]), p)
    assert res["neutralize_coverage"]["size"] == pytest.approx(0.0)


def test_skipped_period_resets_the_book():
    """Carrying holdings across a gap charged no turnover for the round trip
    and implied the portfolio earned a return that is not in the series."""
    p = _panel(n_periods=24)
    thin = p.filter(pl.col("asof") == date(2015, 3, 1)).head(5)
    p = pl.concat([p.filter(pl.col("asof") != date(2015, 3, 1)), thin]).sort("asof")
    res = harness.run_cross_sectional(dict(SPEC, cost_bps=10, min_names=20), p)
    assert res["skipped_periods"] == 1
    assert res["n_periods"] == 23
