"""The verdict gate.

A skeptic that stops killing things looks exactly like success, so the
fatal/non-fatal partition is worth pinning explicitly: flip one check to
non-fatal and every subsequent verdict silently becomes wrong.
"""
import numpy as np
import pytest

from alphaos import registry as R, skeptic

SPEC = {"type": "event_study", "event": {"trigger": "8k_2.02"},
        "window": [2, 61], "predicted_sign": 1, "min_events": 30}


@pytest.fixture()
def reg(tmp_path):
    r = R.Registry(str(tmp_path / "t.duckdb"))
    yield r
    r.close()


def _results(mean=0.01, n=400, seed=0, **over):
    rng = np.random.default_rng(seed)
    arr = rng.normal(mean, 0.02, n)
    base = {
        "engine": "event_study", "mean_bhar": float(arr.mean()),
        # the verdict is scored on the calendar-time portfolio, not the
        # per-event average; the fixture keeps them consistent
        "calendar_effect": float(arr.mean()),
        "t_bhar": 8.0, "mean_bhar_ci": [0.005, 0.015],
        "returns": arr.tolist(), "returns_signed": arr.tolist(),
        "top10_share": 0.02, "n_distinct_names": 200, "sharpe": 0.4,
    }
    base.update(over)
    return base


def _crit(reg, results, spec=None):
    return skeptic.critique(spec or SPEC, results, reg=reg, family_id="fam")


def test_a_harness_error_fails_immediately(reg):
    out = _crit(reg, {"engine": "event_study", "error": "too few events"})
    assert out["verdict"] == R.FAILED
    assert "too few events" in out["reason"]


def test_wrong_sign_is_fatal_however_strong(reg):
    """Predicted up, went down, with a huge t. This is a falsified hypothesis,
    not a discovery -- accepting it would mean predicting A, observing not-A,
    and claiming a win either way."""
    out = _crit(reg, _results(mean=-0.01, t_bhar=-30.0,
                              mean_bhar_ci=[-0.015, -0.005]))
    assert out["verdict"] == R.FAILED
    killers = [c["check"] for c in out["checks"] if c["fatal"] and not c["passed"]]
    assert "predicted_sign" in killers


def test_concentration_share_is_always_a_share():
    """Partially cancelling returns made abs(sum) tiny and printed shares over
    100%, killing hypotheses on arithmetic rather than evidence."""
    cancelling = [0.10, -0.09, 0.08, -0.07, 0.05, -0.04, 0.03, -0.02]
    assert 0.0 <= skeptic.concentration_share(cancelling) <= 1.0
    # one period carrying everything
    assert skeptic.concentration_share([0.5, 0.0, 0.0]) == pytest.approx(1.0)
    # evenly spread across four periods
    assert skeptic.concentration_share([0.01] * 4) == pytest.approx(0.25)
    # degenerate input is treated as maximally concentrated, not as a pass
    assert skeptic.concentration_share([]) == 1.0
    assert skeptic.concentration_share([0.0, 0.0]) == 1.0


def test_cross_sectional_critique_requires_the_panel(reg):
    """Reruns need it; failing at the boundary beats crashing inside polars."""
    with pytest.raises(ValueError, match="requires `wide`"):
        skeptic.critique(
            {"type": "cross_sectional", "signal": {"feature": "mom_12_1"},
             "rebalance": "monthly", "horizon_days": 21, "predicted_sign": 1},
            {"engine": "cross_sectional", "mean_net": 0.01, "t_net": 3.0,
             "mean_net_ci": [0.005, 0.015], "returns": [0.01] * 40,
             "returns_signed": [0.01] * 40, "coverage": 0.9, "mean_names": 300},
            reg=reg, family_id="fam", wide=None)


def test_low_coverage_is_reported_but_not_fatal(reg):
    """Half the index having no inventory is the signal's natural domain, not
    missing data. The survivorship guard is the high-coverage retest."""
    out = _crit(reg, _results())
    cov = [c for c in out["checks"] if c["check"] == "coverage"]
    assert all(not c["fatal"] for c in cov)


def test_every_other_check_is_fatal(reg):
    """If a check stops being fatal the battery quietly becomes a report."""
    out = _crit(reg, _results())
    non_fatal = {c["check"] for c in out["checks"] if not c["fatal"]}
    assert non_fatal <= {"coverage"}


def test_a_strong_clean_result_can_survive(reg):
    """The battery must be passable, or NO EDGE stops being informative."""
    out = _crit(reg, _results(mean=0.02, n=800, t_bhar=12.0,
                              mean_bhar_ci=[0.012, 0.028]))
    assert out["verdict"] == R.SURVIVED_DISCOVERY, out["reason"]


def test_an_unrunnable_placebo_is_fatal(reg, monkeypatch):
    """Missing placebo evidence is not evidence that the placebo passed."""
    monkeypatch.setattr(
        skeptic.harness, "run_event_study",
        lambda *a, **k: {"engine": "event_study", "error": "too few events"})
    out = _crit(reg, _results(mean=0.02, n=800, t_bhar=12.0,
                              mean_bhar_ci=[0.012, 0.028]))
    placebo = next(c for c in out["checks"] if c["check"] == "placebo")
    assert not placebo["passed"]


def test_dsr_hurdle_rises_with_family_trials(reg):
    """Same result, more prior trials in the family, less credit.

    A marginal effect on purpose: a strong one saturates the DSR at 1.0 and the
    comparison shows nothing. Marginal is also where the deflation has to work,
    since that is where mined results live.
    """
    res = _results(mean=0.002, n=400, t_bhar=2.1, mean_bhar_ci=[0.0002, 0.0038])
    lonely = _crit(reg, res)["dsr"]
    for i in range(40):
        e = reg.register(campaign_id="c", family_id="fam", hypothesis="h",
                         predicted_sign=1, falsification="f",
                         spec={"variant": i}, data_version="dv")
        reg.record_result(e, {}, sharpe=0.05 * (i % 7))
    crowded = _crit(reg, res)["dsr"]
    assert 0.0 < crowded < lonely < 1.0


def test_skeptic_reruns_do_not_raise_the_hurdle(reg):
    """family_trial_count and family_sharpe_variance must agree on what a trial
    is. Placebo Sharpes leaking into the variance inflated the hurdle 52% on
    the live ledger while being excluded from N."""
    for i in range(6):
        e = reg.register(campaign_id="c", family_id="fam", hypothesis="h",
                         predicted_sign=1, falsification="f",
                         spec={"variant": i}, data_version="dv")
        reg.record_result(e, {}, sharpe=0.10)
    baseline = reg.family_sharpe_variance("fam")

    for i in range(20):
        e = reg.register(campaign_id="c", family_id="fam", hypothesis="placebo",
                         predicted_sign=1, falsification="f",
                         spec={"placebo": i}, data_version="dv",
                         trial_kind="skeptic")
        reg.record_result(e, {}, sharpe=float(i))     # wild noise
    assert reg.family_sharpe_variance("fam") == pytest.approx(baseline)
    assert reg.family_trial_count("fam") == 6
