"""Failure-safety checks for the filing-event archive."""
import pytest

from alphaos.ingest import events


def test_total_fetch_outage_is_not_reported_as_a_success(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("SEC unavailable")

    monkeypatch.setattr(events, "fetch_filings", fail)
    out = tmp_path / "events.parquet"
    with pytest.raises(RuntimeError, match="all 2 CIK fetches failed"):
        events.build(["1", "2"], out, recent_only=True, merge=True)
    assert not out.exists()
