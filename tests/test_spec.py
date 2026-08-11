"""Spec validation. The point is that a malformed or subtly-wrong spec is
rejected outright rather than quietly reinterpreted: a typo'd key that
silently disabled neutralisation would be indistinguishable from a discovery.
"""
import pytest

from alphaos import spec as S

BASE = {"type": "cross_sectional", "signal": {"feature": "Revenues"},
        "rebalance": "monthly", "horizon_days": 21, "predicted_sign": 1}


def test_accepts_a_well_formed_spec():
    out = S.validate(BASE)
    assert out["cost_bps"] == 10 and out["neutralize"] == []


def test_unknown_keys_are_rejected_not_ignored():
    with pytest.raises(S.SpecError, match="unknown keys"):
        S.validate(dict(BASE, neutralise=["sector"]))       # British spelling typo


def test_unknown_feature_and_op_rejected():
    with pytest.raises(S.SpecError, match="unknown feature"):
        S.validate(dict(BASE, signal={"feature": "Vibes"}))
    with pytest.raises(S.SpecError, match="unknown op"):
        S.validate(dict(BASE, signal={"op": "smooth", "x": {"feature": "Revenues"}}))


def test_predicted_sign_is_mandatory_and_binding():
    with pytest.raises(S.SpecError, match="predicted_sign"):
        S.validate({k: v for k, v in BASE.items() if k != "predicted_sign"})
    with pytest.raises(S.SpecError, match="predicted_sign"):
        S.validate(dict(BASE, predicted_sign=0))


def test_overlapping_holding_periods_are_refused():
    """A 63-day hold rebalanced monthly overlaps; the resulting return series is
    autocorrelated and every downstream statistic assumes it is not."""
    with pytest.raises(S.SpecError, match="do not overlap"):
        S.validate(dict(BASE, horizon_days=63))
    ok = S.validate(dict(BASE, rebalance="quarterly", horizon_days=63))
    assert ok["horizon_days"] == 63


def test_market_feature_cannot_be_given_a_quarterly_lag():
    with pytest.raises(S.SpecError, match="no quarterly lags"):
        S.validate(dict(BASE, signal={"feature": "mom_12_1", "lag": 2}))


def test_nested_expression_validates_recursively():
    good = {"op": "sub",
            "a": {"op": "yoy_growth", "x": {"feature": "InventoryNet"}},
            "b": {"op": "yoy_growth", "x": {"feature": "Revenues"}}}
    assert S.validate(dict(BASE, signal=good))["signal"] == good
    bad = dict(good, b={"op": "yoy_growth", "x": {"feature": "Nope"}})
    with pytest.raises(S.SpecError):
        S.validate(dict(BASE, signal=bad))


def test_binary_op_requires_both_operands():
    with pytest.raises(S.SpecError, match="takes exactly a, b"):
        S.validate(dict(BASE, signal={"op": "sub", "a": {"feature": "Revenues"}}))


EV = {"type": "event_study", "event": {"trigger": "8k_2.02"},
      "window": [1, 60], "predicted_sign": 1}


def test_event_spec_basics():
    out = S.validate(EV)
    assert out["benchmark"] == "spy" and out["condition"] is None
    with pytest.raises(S.SpecError, match="unknown trigger"):
        S.validate(dict(EV, event={"trigger": "8k_9.99"}))


def test_conditioned_window_must_start_after_the_conditioning_bar():
    """reaction_1d is only known at the close of day +1. A window starting at
    +1 would measure the condition itself and print a large fake effect."""
    cond = {"on": "reaction_1d", "select": "top", "fraction": 0.3}
    with pytest.raises(S.SpecError, match="day \\+2 or later"):
        S.validate(dict(EV, condition=cond, window=[1, 60]))
    assert S.validate(dict(EV, condition=cond, window=[2, 61]))["condition"] == cond


def test_describe_is_human_readable():
    text = S.describe(S.validate(dict(BASE, signal={
        "op": "sub",
        "a": {"op": "yoy_growth", "x": {"feature": "InventoryNet"}},
        "b": {"op": "yoy_growth", "x": {"feature": "Revenues"}}})))
    assert "yoy_growth(InventoryNet)" in text and "yoy_growth(Revenues)" in text
