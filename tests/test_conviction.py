"""The conviction sheet is the only artifact that suggests trades, so its
failure modes are the ones that reach the outside world.
"""
import json
from datetime import date, timedelta

import pytest

from alphaos import conviction, registry as R

SPEC = {"type": "cross_sectional", "signal": {"feature": "mom_12_1"},
        "rebalance": "monthly", "horizon_days": 21, "neutralize": [],
        "cost_bps": 10, "min_names": 20, "predicted_sign": 1}


@pytest.fixture()
def reg(tmp_path):
    r = R.Registry(str(tmp_path / "t.duckdb"))
    yield r
    r.close()


def _vaulted(reg, results=None):
    e = reg.register(campaign_id="c", family_id="fam", hypothesis="h",
                     predicted_sign=1, falsification="f", spec=SPEC,
                     data_version="dv1")
    reg.record_result(e, results if results is not None else {"mean_net": 0.004},
                      sharpe=0.3, dsr=0.97)
    reg.record_verdict(e, R.VAULT, "passed embargo")
    return e


def test_empty_vault_says_so_plainly(reg, tmp_path):
    out = conviction.build(out_path=tmp_path / "sheet.md", reg=reg)
    assert "No strategies in the vault" in out
    assert (tmp_path / "sheet.md").exists()


def test_writes_into_a_directory_that_does_not_exist_yet(reg, tmp_path):
    dest = tmp_path / "nested" / "deeper" / "sheet.md"
    conviction.build(out_path=dest, reg=reg)
    assert dest.exists()


def test_a_vault_entry_without_results_does_not_crash_the_sheet(reg, tmp_path,
                                                               monkeypatch):
    """register and record_result are separate calls, so a crash between them
    leaves a resultless row. Formatting None with :.5f killed the whole nightly
    job over one cosmetic line."""
    e = reg.register(campaign_id="c", family_id="fam", hypothesis="h",
                     predicted_sign=1, falsification="f", spec=SPEC,
                     data_version="dv1")
    reg.record_verdict(e, R.VAULT, "passed")
    monkeypatch.setattr(conviction.harness, "load_wide",
                        lambda **k: _panel(date.today()))
    out = conviction.build(out_path=tmp_path / "s.md", reg=reg)
    assert "unavailable" in out


def _panel(asof):
    import polars as pl
    return pl.DataFrame({
        "asof": [asof] * 40,
        "ticker": [f"T{i:02d}" for i in range(40)],
        "gics_sector": ["Industrials"] * 40,
        "mom_12_1": [float(i) for i in range(40)],
        "mktcap": [1e9] * 40, "beta_252": [1.0] * 40,
        "entry_close": [100.0] * 40, "_lag_days": [0] * 40,
    })


def test_stale_book_is_refused_rather_than_published(reg, tmp_path, monkeypatch):
    """The sheet is stamped with today's date. Publishing a book built from a
    panel that stops years earlier is the failure that would first appear on
    the day something finally passed the embargo."""
    _vaulted(reg)
    old = date.today() - timedelta(days=400)
    monkeypatch.setattr(conviction.harness, "load_wide", lambda **k: _panel(old))
    out = conviction.build(out_path=tmp_path / "s.md", reg=reg)
    assert "Not published" in out
    assert str(old) in out


def test_fresh_book_publishes_both_legs(reg, tmp_path, monkeypatch):
    _vaulted(reg)
    monkeypatch.setattr(conviction.harness, "load_wide",
                        lambda **k: _panel(date.today()))
    out = conviction.build(out_path=tmp_path / "s.md", reg=reg)
    assert "| long |" in out and "| short |" in out


def test_long_leg_follows_the_predicted_sign(monkeypatch):
    """A short hypothesis holds the LOW decile long. Publishing the high decile
    as 'long' for a predicted_sign=-1 spec would invert the whole book."""
    p = _panel(date.today())
    up = conviction.current_book(SPEC, p)
    down = conviction.current_book(dict(SPEC, predicted_sign=-1), p)
    assert up["long"][0][0] != down["long"][0][0]
    assert {t for t, _ in up["long"]} == {t for t, _ in down["short"]}


def test_thin_cross_section_is_not_published(monkeypatch):
    import polars as pl
    p = _panel(date.today()).head(5)
    book = conviction.current_book(SPEC, p)
    assert book["long"] == [] and "requires" in book["note"]
