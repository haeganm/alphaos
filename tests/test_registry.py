"""The ledger's integrity guarantees. If these fail, no number in this repo
means anything, because the trial count behind it can no longer be trusted.
"""
import pytest

from alphaos import registry as R


@pytest.fixture()
def reg(tmp_path):
    r = R.Registry(str(tmp_path / "t.duckdb"))
    yield r
    r.close()


def _reg(reg, spec, family="fam", sign=-1, campaign="camp"):
    return reg.register(campaign_id=campaign, family_id=family,
                        hypothesis="inventory builds while revenue slows -> underperformance",
                        predicted_sign=sign,
                        falsification="no negative spread at 63d after costs",
                        spec=spec, data_version="dv1")


def test_register_requires_a_real_hypothesis(reg):
    with pytest.raises(ValueError):
        reg.register(campaign_id="c", family_id="f", hypothesis="   ",
                     predicted_sign=-1, falsification="x", spec={"a": 1}, data_version="dv1")
    with pytest.raises(ValueError):
        reg.register(campaign_id="c", family_id="f", hypothesis="h",
                     predicted_sign=0, falsification="x", spec={"a": 1}, data_version="dv1")


def test_spec_hash_is_key_order_independent(reg):
    assert R.spec_hash({"a": 1, "b": 2}) == R.spec_hash({"b": 2, "a": 1})
    assert R.spec_hash({"a": 1}) != R.spec_hash({"a": 2})


def test_results_cannot_be_overwritten(reg):
    """A second look at the same spec is a second trial, not a revision."""
    e = _reg(reg, {"s": 1})
    reg.record_result(e, {"ic": 0.01}, sharpe=0.1, dsr=0.6)
    with pytest.raises(ValueError):
        reg.record_result(e, {"ic": 0.09}, sharpe=0.9, dsr=0.99)
    assert reg.get(e)["sharpe"] == 0.1


def test_frozen_fields_survive_the_full_lifecycle(reg):
    e = _reg(reg, {"s": 1})
    before = {k: reg.get(e)[k] for k in R.FROZEN_FIELDS}
    reg.record_result(e, {"ic": 0.01}, sharpe=0.2, dsr=0.5)
    reg.record_verdict(e, R.SURVIVED_DISCOVERY, "passed battery")
    reg.record_holdout_touch(e)
    reg.record_verdict(e, R.VAULT, "passed embargo")
    after = {k: reg.get(e)[k] for k in R.FROZEN_FIELDS}
    assert before == after


def test_trial_count_uses_distinct_specs(reg):
    _reg(reg, {"s": 1})
    _reg(reg, {"s": 2})
    _reg(reg, {"s": 2})            # same spec again: no new search breadth
    assert reg.family_trial_count("fam") == 2
    _reg(reg, {"s": 3}, family="other")
    assert reg.family_trial_count("fam") == 2


def test_abandoned_trials_still_count(reg):
    a = _reg(reg, {"s": 1})
    _reg(reg, {"s": 2})
    reg.abandon(a, "looked wrong midway")
    assert reg.get(a)["status"] == R.ABANDONED
    assert reg.family_trial_count("fam") == 2   # abandonment does not un-try it


def test_family_sharpe_variance_feeds_the_dsr(reg):
    for i, s in enumerate([0.1, 0.2, 0.3]):
        e = _reg(reg, {"s": i})
        reg.record_result(e, {}, sharpe=s, dsr=0.5)
    assert reg.family_sharpe_variance("fam") == pytest.approx(0.01)


def test_every_mutation_is_logged(reg):
    e = _reg(reg, {"s": 1})
    reg.record_result(e, {}, sharpe=0.1, dsr=0.5)
    reg.record_verdict(e, R.FAILED, "sign flipped")
    events = reg.con.execute(
        "select event from registry_events where exp_id=? order by seq", [e]).fetchall()
    assert [x[0] for x in events] == ["register", "result", "verdict"]


def test_verdict_can_be_demoted_later(reg):
    e = _reg(reg, {"s": 1})
    reg.record_result(e, {}, sharpe=0.4, dsr=0.9)
    reg.record_verdict(e, R.VAULT, "survived")
    reg.record_verdict(e, R.DEMOTED, "forward IC decayed to zero")
    assert reg.get(e)["verdict"] == R.DEMOTED
    n = reg.con.execute("select count(*) from registry_events where exp_id=? and event='verdict'",
                        [e]).fetchone()[0]
    assert n == 2      # both decisions preserved, not just the latest


def test_unknown_verdict_rejected(reg):
    e = _reg(reg, {"s": 1})
    with pytest.raises(ValueError):
        reg.record_verdict(e, "LOOKS_GREAT", "vibes")
