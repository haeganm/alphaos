"""The lookahead tests.

If any of these fail, every number this lab has ever produced is suspect, and
no amount of robustness testing downstream would have caught it: a leaked
signal passes subperiod replication and factor neutralisation exactly as
happily as a real one, because the leak is in the data, not the statistics.
"""
from datetime import date

import polars as pl
import pytest

from alphaos import panel


def _facts(rows):
    return pl.DataFrame(rows, schema={
        "cik": pl.Utf8, "tag": pl.Utf8, "xbrl_tag": pl.Utf8, "alias_rank": pl.Int8,
        "unit": pl.Utf8, "start": pl.Date, "end": pl.Date, "period_days": pl.Int32,
        "value": pl.Float64, "filed": pl.Date, "form": pl.Utf8, "accn": pl.Utf8,
        "fy": pl.Int32, "fp": pl.Utf8}, orient="row")


def test_fact_is_invisible_until_the_day_after_it_is_filed():
    f = _facts([("0001", "Assets", "Assets", 0, "USD", None, date(2015, 3, 31), None,
                 500.0, date(2015, 5, 1), "10-Q", "a1", 2015, "Q1")])
    qs = panel.quarterly_series(f)

    assert panel.snapshot(qs, date(2015, 4, 30)).is_empty()   # before filing
    assert panel.snapshot(qs, date(2015, 5, 1)).is_empty()    # ON the filing date
    got = panel.snapshot(qs, date(2015, 5, 2))                 # the day after
    assert got["q0"][0] == 500.0


def test_restatement_does_not_travel_backwards():
    """A 2016 restatement of a 2015 figure must not appear in a 2015 snapshot."""
    f = _facts([
        ("0001", "Assets", "Assets", 0, "USD", None, date(2015, 3, 31), None,
         500.0, date(2015, 5, 1), "10-Q", "a1", 2015, "Q1"),
        ("0001", "Assets", "Assets", 0, "USD", None, date(2015, 3, 31), None,
         900.0, date(2016, 5, 1), "10-K/A", "a2", 2015, "Q1"),
    ])
    qs = panel.quarterly_series(f)
    assert panel.snapshot(qs, date(2015, 8, 1))["q0"][0] == 500.0   # as known then
    assert panel.snapshot(qs, date(2016, 8, 1))["q0"][0] == 900.0   # after restatement


def test_duration_restatements_keep_their_earlier_vintages():
    """The instant path preserved restatement history; the duration path did not.

    Q1 revenue 100 filed in May, H1 250 filed in August, then Q1 restated to 120
    in September. Collapsing duration facts to the newest version before
    differencing left the August snapshot with NO revenue at all -- the only
    surviving Q1 row was stamped September and filtered out as not-yet-known.
    An August observer knew Q1 = 100 and could derive Q2 = 150.
    """
    rows = [
        ("0001", "Revenues", "Revenues", 0, "USD", date(2015, 1, 1), date(2015, 3, 31),
         90, 100.0, date(2015, 5, 1), "10-Q", "a1", 2015, "Q1"),
        ("0001", "Revenues", "Revenues", 0, "USD", date(2015, 1, 1), date(2015, 6, 30),
         181, 250.0, date(2015, 8, 1), "10-Q", "a2", 2015, "Q2"),
        ("0001", "Revenues", "Revenues", 0, "USD", date(2015, 1, 1), date(2015, 3, 31),
         90, 120.0, date(2015, 9, 1), "10-Q/A", "a3", 2015, "Q1"),
    ]
    qs = panel.quarterly_series(_facts(rows))

    aug = panel.snapshot(qs, date(2015, 8, 15))
    assert not aug.is_empty(), "August observer must still see Q1 and derived Q2"
    assert aug["q0"][0] == pytest.approx(150.0)      # Q2 = 250 - 100, as known then
    assert aug["q1"][0] == pytest.approx(100.0)      # Q1 as originally filed

    # After the restatement both figures move to their corrected values.
    oct_ = panel.snapshot(qs, date(2015, 10, 1))
    assert oct_["q0"][0] == pytest.approx(130.0)     # Q2 = 250 - 120
    assert oct_["q1"][0] == pytest.approx(120.0)


def test_ytd_chain_becomes_quarterly_and_inherits_the_later_filing_date():
    """Q2 = H1 - Q1. It is only knowable once the H1 filing exists, so the
    derived quarter must carry the H1 filing date, not Q1's."""
    f = _facts([
        ("0001", "Revenues", "Revenues", 0, "USD", date(2015, 1, 1), date(2015, 3, 31),
         90, 100.0, date(2015, 5, 1), "10-Q", "a1", 2015, "Q1"),
        ("0001", "Revenues", "Revenues", 0, "USD", date(2015, 1, 1), date(2015, 6, 30),
         181, 250.0, date(2015, 8, 1), "10-Q", "a2", 2015, "Q2"),
    ])
    qs = panel.quarterly_series(f)
    q2 = qs.filter(pl.col("period_end") == date(2015, 6, 30))
    assert q2["value"][0] == pytest.approx(150.0)
    assert q2["filed"][0] == date(2015, 8, 1)
    assert bool(q2["derived"][0])
    # and it must not be visible before that filing
    assert panel.snapshot(qs, date(2015, 7, 15)).filter(
        pl.col("tag") == "Revenues")["q0"][0] == 100.0


def test_annual_minus_nine_months_recovers_q4():
    """Most filers never report a standalone Q4; without this the fourth
    quarter of every year silently disappears from the panel."""
    rows = []
    for end, days, val, filed in [
        (date(2015, 3, 31), 90, 100.0, date(2015, 5, 1)),
        (date(2015, 6, 30), 181, 250.0, date(2015, 8, 1)),
        (date(2015, 9, 30), 273, 400.0, date(2015, 11, 1)),
        (date(2015, 12, 31), 365, 600.0, date(2016, 2, 1)),
    ]:
        rows.append(("0001", "Revenues", "Revenues", 0, "USD", date(2015, 1, 1),
                     end, days, val, filed, "10-K", f"a{days}", 2015, "FY"))
    qs = panel.quarterly_series(_facts(rows))
    q4 = qs.filter(pl.col("period_end") == date(2015, 12, 31))
    assert q4["value"][0] == pytest.approx(200.0)      # 600 - 400
    assert q4["filed"][0] == date(2016, 2, 1)


def test_annual_and_quarterly_are_never_mixed():
    """An annual figure sitting in a quarterly series reads as ~300% growth."""
    f = _facts([
        ("0001", "Revenues", "Revenues", 0, "USD", date(2015, 1, 1), date(2015, 12, 31),
         365, 600.0, date(2016, 2, 1), "10-K", "a1", 2015, "FY"),
    ])
    qs = panel.quarterly_series(f)
    # a lone annual duration with no chain to difference against yields nothing
    assert qs.filter(pl.col("tag") == "Revenues").is_empty()


def test_snapshot_orders_lags_newest_first():
    rows = [("0001", "Assets", "Assets", 0, "USD", None, date(2015, m, 28), None,
             float(m), date(2015, m + 1, 1), "10-Q", f"a{m}", 2015, "Q")
            for m in (3, 6, 9)]
    qs = panel.quarterly_series(_facts(rows))
    snap = panel.snapshot(qs, date(2015, 11, 1))
    assert snap["q0"][0] == 9.0 and snap["q1"][0] == 6.0 and snap["q2"][0] == 3.0
