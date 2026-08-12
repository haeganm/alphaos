"""Operational guarantees for the unattended nightly grader."""
import json

from alphaos import registry as R
from scripts import nightly


SPEC = {"type": "event_study", "event": {"trigger": "10q_filed"},
        "window": [1, 20], "predicted_sign": 1, "min_events": 30}


class _Registry:
    def __init__(self, row):
        self.row = row
        self.forward = None

    def by_verdict(self, verdict):
        return [self.row] if verdict == R.VAULT else []

    def record_forward(self, exp_id, result):
        self.forward = result

    def record_verdict(self, *args, **kwargs):
        pass


def _row():
    return {"id": "exp_test", "verdict": R.VAULT,
            "registered_at": "2026-08-11T18:00:00Z",
            "spec_json": json.dumps(SPEC)}


def test_forward_grade_starts_strictly_after_registration(monkeypatch):
    seen = {}

    def run(spec, **kwargs):
        seen.update(spec)
        return {"returns_signed": [0.01, 0.02, 0.03]}

    monkeypatch.setattr(nightly.harness, "run_event_study", run)
    monkeypatch.setattr(nightly, "log", lambda message: None)
    reg = _Registry(_row())

    assert nightly.forward_grade(reg, "dv") is True
    assert seen["start"] == "2026-08-12"


def test_forward_grade_reports_strategy_failures(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("broken grader")

    monkeypatch.setattr(nightly.harness, "run_event_study", fail)
    monkeypatch.setattr(nightly, "log", lambda message: None)
    assert nightly.forward_grade(_Registry(_row()), "dv") is False
