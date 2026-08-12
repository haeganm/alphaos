"""The embargo gate.

Its whole reason to exist is the touch counter. If that count can be dodged --
by a crash, by an unusual verdict, by an off-by-one -- the module is
ceremonial, and a family that has already spent its three looks silently gets
more.
"""
import json

import pytest

from alphaos import holdout, registry as R

SPEC = {"type": "cross_sectional", "signal": {"feature": "mom_12_1"},
        "rebalance": "monthly", "horizon_days": 21, "predicted_sign": 1}


@pytest.fixture()
def reg(tmp_path):
    r = R.Registry(str(tmp_path / "t.duckdb"))
    yield r
    r.close()


def _candidate(reg, family="fam", verdict=R.SURVIVED_DISCOVERY, spec=None):
    e = reg.register(campaign_id="c", family_id=family,
                     hypothesis="h", predicted_sign=1, falsification="f",
                     spec=spec or SPEC, data_version="dv1")
    reg.record_result(e, {"mean_net": 0.01}, sharpe=0.2)
    if verdict:
        reg.record_verdict(e, verdict, "battery")
    return e


def test_only_survivors_may_be_taken_to_the_embargo(reg):
    failed = _candidate(reg, verdict=R.FAILED)
    with pytest.raises(ValueError, match="SURVIVED_DISCOVERY"):
        holdout.query(failed, reg=reg)
    assert reg.get(failed)["holdout_touches"] == 0     # a refusal costs nothing


def test_unknown_experiment_raises(reg):
    with pytest.raises(KeyError):
        holdout.query("exp_nope", reg=reg)


def test_the_query_is_spent_even_when_the_harness_fails(reg, monkeypatch):
    """A crash mid-query must not hand back a free look. The data was touched
    as far as the family's independence is concerned."""
    e = _candidate(reg)

    def boom(*a, **k):
        raise RuntimeError("no panel on disk")
    monkeypatch.setattr(holdout.harness, "load_wide", boom)

    with pytest.raises(RuntimeError):
        holdout.query(e, reg=reg)
    assert reg.get(e)["holdout_touches"] == 1
    assert reg.family_holdout_touches("fam") == 1


def test_embargo_closes_after_three_family_touches(reg, monkeypatch):
    monkeypatch.setattr(holdout.harness, "load_wide",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("stub")))
    for i in range(holdout.MAX_FAMILY_TOUCHES):
        e = _candidate(reg, spec=dict(SPEC, cost_bps=i))
        with pytest.raises(RuntimeError):
            holdout.query(e, reg=reg)
    assert reg.family_holdout_touches("fam") == holdout.MAX_FAMILY_TOUCHES

    fourth = _candidate(reg, spec=dict(SPEC, cost_bps=99))
    with pytest.raises(holdout.EmbargoClosed):
        holdout.query(fourth, reg=reg)
    # a refused query does not consume another touch
    assert reg.family_holdout_touches("fam") == holdout.MAX_FAMILY_TOUCHES


def test_a_different_family_has_its_own_budget(reg, monkeypatch):
    monkeypatch.setattr(holdout.harness, "load_wide",
                        lambda **k: (_ for _ in ()).throw(RuntimeError("stub")))
    for i in range(holdout.MAX_FAMILY_TOUCHES):
        e = _candidate(reg, family="a", spec=dict(SPEC, cost_bps=i))
        with pytest.raises(RuntimeError):
            holdout.query(e, reg=reg)
    other = _candidate(reg, family="b")
    with pytest.raises(RuntimeError):       # runs, i.e. not EmbargoClosed
        holdout.query(other, reg=reg)


def test_query_discloses_a_verdict_and_nothing_else(reg, monkeypatch):
    """No t-stat, no Sharpe, no return series. Anything quantitative here lets
    a rejected candidate be tuned against the holdout."""
    e = _candidate(reg)
    monkeypatch.setattr(holdout.harness, "load_wide", lambda **k: "panel")
    monkeypatch.setattr(holdout.harness, "run_cross_sectional",
                        lambda spec, wide: {"mean_net": 0.02,
                                            "returns_signed": [0.02] * 40,
                                            "t_net": 9.9, "sharpe": 1.4})
    out = holdout.query(e, reg=reg)
    assert set(out) == {"passed", "touches", "family_touches"}
    assert isinstance(out["passed"], bool)
    assert reg.get(e)["verdict"] == R.VAULT


def test_event_gate_judges_the_same_statistic_discovery_judged(reg, monkeypatch):
    """For event studies the per-event average and the calendar-time effect can
    disagree in sign. If the gate read the per-event number while discovery
    read the calendar one, a spec could pass discovery and be rejected at the
    embargo on a different quantity entirely."""
    ev_spec = {"type": "event_study", "event": {"trigger": "8k_2.02"},
               "window": [1, 61], "predicted_sign": 1, "min_events": 30}
    e = reg.register(campaign_id="c", family_id="ev", hypothesis="h",
                     predicted_sign=1, falsification="f", spec=ev_spec,
                     data_version="dv1")
    reg.record_result(e, {"mean_bhar": 0.01}, sharpe=0.2)
    reg.record_verdict(e, R.SURVIVED_DISCOVERY, "battery")

    # per-event average positive, calendar-time effect negative
    monkeypatch.setattr(holdout.harness, "run_event_study",
                        lambda spec, **k: {"engine": "event_study",
                                           "mean_bhar": +0.02,
                                           "calendar_effect": -0.02,
                                           "returns_signed": [-0.001] * 60})
    out = holdout.query(e, reg=reg)
    assert out["passed"] is False, "must follow the calendar-time effect, not mean_bhar"
    assert reg.get(e)["verdict"] == R.FAILED


def test_a_failing_candidate_is_marked_failed_without_disclosing_why(reg, monkeypatch):
    e = _candidate(reg)
    monkeypatch.setattr(holdout.harness, "load_wide", lambda **k: "panel")
    monkeypatch.setattr(holdout.harness, "run_cross_sectional",
                        lambda spec, wide: {"mean_net": -0.02,
                                            "returns_signed": [-0.02] * 40})
    out = holdout.query(e, reg=reg)
    assert out["passed"] is False
    row = reg.get(e)
    assert row["verdict"] == R.FAILED
    assert "no statistics" in row["verdict_reason"]
    assert not any(ch.isdigit() for ch in row["verdict_reason"])
