"""Insider trigger construction.

The dating rule is the whole risk here. A cluster of purchases is not knowable
to an outside observer until its LAST component filing becomes public, however
early the underlying trades were executed -- Form 4 allows two business days,
and transaction dates routinely precede filing dates by more than that. Dating
a cluster by its first purchase, or by transaction date rather than filing
acceptance, is a lookahead of days to weeks that no downstream robustness test
would catch.
"""
from datetime import date

import polars as pl
import pytest

from alphaos.ingest import insiders

COLS = {"cik": pl.Utf8, "issuer_ticker": pl.Utf8, "accn": pl.Utf8,
        "filing_date": pl.Date, "txn_date": pl.Date, "txn_code": pl.Utf8,
        "acq_disp": pl.Utf8, "shares": pl.Float64, "price": pl.Float64,
        "shares_after": pl.Float64, "ownership": pl.Utf8, "owner_cik": pl.Utf8,
        "owner_name": pl.Utf8, "relationship": pl.Utf8, "owner_title": pl.Utf8,
        "accepted_utc": pl.Utf8}


def _txn(owner, filed, *, code="P", acq="A", cik="0000000001", rel="Officer",
         title="CEO", txn=None, accepted=None):
    return {"cik": cik, "issuer_ticker": "AAA", "accn": f"a-{owner}-{filed}",
            "filing_date": filed, "txn_date": txn or filed, "txn_code": code,
            "acq_disp": acq, "shares": 1000.0, "price": 10.0,
            "shares_after": 5000.0, "ownership": "D", "owner_cik": owner,
            "owner_name": f"Owner {owner}", "relationship": rel,
            "owner_title": title,
            "accepted_utc": accepted or f"{filed}T21:30:00Z"}


def _frame(rows, tmp_path):
    p = tmp_path / "insiders.parquet"
    pl.DataFrame(rows, schema=COLS).write_parquet(p)
    return p


def test_cluster_fires_on_the_third_distinct_insider(tmp_path):
    p = _frame([
        _txn("o1", date(2015, 3, 2)),
        _txn("o2", date(2015, 3, 5)),
        _txn("o3", date(2015, 3, 9)),
    ], tmp_path)
    ev = insiders.event_table("insider_cluster_buy", p)
    assert ev.height == 1
    assert ev["filing_date"][0] == date(2015, 3, 9)   # the completing filing


def test_cluster_carries_the_completing_filings_acceptance_stamp(tmp_path):
    """Not the first purchase's. This is the lookahead that matters."""
    p = _frame([
        _txn("o1", date(2015, 3, 2), accepted="2015-03-02T14:00:00Z"),
        _txn("o2", date(2015, 3, 5), accepted="2015-03-05T14:00:00Z"),
        _txn("o3", date(2015, 3, 9), accepted="2015-03-09T22:15:00Z"),
    ], tmp_path)
    ev = insiders.event_table("insider_cluster_buy", p)
    assert ev["accepted_utc"][0] == "2015-03-09T22:15:00Z"


def test_transaction_dates_never_set_the_event_date(tmp_path):
    """Trades executed in January but filed in March are public in March."""
    p = _frame([
        _txn("o1", date(2015, 3, 2), txn=date(2015, 1, 5)),
        _txn("o2", date(2015, 3, 5), txn=date(2015, 1, 6)),
        _txn("o3", date(2015, 3, 9), txn=date(2015, 1, 7)),
    ], tmp_path)
    ev = insiders.event_table("insider_cluster_buy", p)
    assert ev["filing_date"][0] == date(2015, 3, 9)


def test_one_insider_buying_repeatedly_is_not_a_cluster(tmp_path):
    p = _frame([_txn("o1", date(2015, 3, d)) for d in (2, 5, 9, 12)], tmp_path)
    assert insiders.event_table("insider_cluster_buy", p).is_empty()


def test_purchases_outside_the_window_do_not_count(tmp_path):
    p = _frame([
        _txn("o1", date(2015, 1, 2)),
        _txn("o2", date(2015, 2, 20)),
        _txn("o3", date(2015, 3, 9)),        # >30d after o1
    ], tmp_path)
    assert insiders.event_table("insider_cluster_buy", p).is_empty()


def test_overlapping_clusters_collapse_to_one_event(tmp_path):
    rows = [_txn(f"o{i}", date(2015, 3, i)) for i in range(1, 10)]
    ev = insiders.event_table("insider_cluster_buy", _frame(rows, tmp_path))
    assert ev.height == 1, "a sustained buying episode is one event, not a run"


def test_only_open_market_purchases_count(tmp_path):
    """Grants (A), option exercises (M) and tax withholding (F) are
    compensation mechanics, not opinions about value."""
    p = _frame([
        _txn("o1", date(2015, 3, 2), code="A"),
        _txn("o2", date(2015, 3, 5), code="M"),
        _txn("o3", date(2015, 3, 9), code="F"),
    ], tmp_path)
    assert insiders.event_table("insider_cluster_buy", p).is_empty()


def test_sales_never_produce_events(tmp_path):
    p = _frame([_txn(f"o{i}", date(2015, 3, i), code="S", acq="D")
                for i in (2, 5, 9)], tmp_path)
    assert insiders.event_table("insider_cluster_buy", p).is_empty()
    assert insiders.event_table("insider_buy_officer", p).is_empty()


def test_officer_trigger_reads_the_relationship_not_the_title(tmp_path):
    """"See Remarks" is a real, common officerTitle. Role must come from the
    relationship flags."""
    p = _frame([
        _txn("o1", date(2015, 3, 2), rel="Officer", title="See Remarks"),
        _txn("o2", date(2015, 3, 3), rel="TenPercentOwner", title="CEO"),
    ], tmp_path)
    ev = insiders.event_table("insider_buy_officer", p)
    assert ev.height == 1
    assert ev["filing_date"][0] == date(2015, 3, 2)


def test_events_arrive_in_the_shape_the_engine_expects(tmp_path):
    p = _frame([_txn(f"o{i}", date(2015, 3, i)) for i in (2, 5, 9)], tmp_path)
    ev = insiders.event_table("insider_cluster_buy", p)
    assert set(ev.columns) == set(insiders._EVENT_SCHEMA)
    assert ev["form"][0] == "INSIDER" and ev["items"][0] == "insider_cluster_buy"


def test_missing_file_yields_an_empty_table_not_a_crash(tmp_path):
    assert insiders.event_table("insider_cluster_buy", tmp_path / "nope.parquet").is_empty()
