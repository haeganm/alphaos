"""The embargo gate. Returns a verdict and nothing else.

The embargoed slice (2022-07-01 onward) exists to answer one question once:
does this survive on data the search never touched? It answers PASS or FAIL.
It does not return a t-statistic, a Sharpe, a return series or a chart,
because any of those would let a rejected candidate be tuned against the
holdout, and a holdout you can tune against is just more training data with a
ceremonial name.

On a single machine this is enforced by discipline plus accounting rather than
by permissions, so every touch is counted and attributed. Three touches
against one family closes the embargo for that family permanently -- at that
point the slice has been used enough to have leaked into the search, and
pretending otherwise would be the lie this whole module exists to prevent.

The embargo is a cheap gate, not the real test. The real test is forward
grading on data that did not exist when the spec was frozen, which is the one
holdout that cannot be peeked at. See conviction.py and scripts/nightly.py.
"""
from __future__ import annotations

import json

import numpy as np

from . import harness, registry as R, skeptic, spec as spec_mod, stats

MAX_FAMILY_TOUCHES = 3


class EmbargoClosed(RuntimeError):
    pass


def query(exp_id: str, *, reg: R.Registry) -> dict:
    """Evaluate a frozen spec on embargoed data. Verdict only.

    Returns {"passed": bool, "touches": int, "family_touches": int}. Deliberately
    no statistics: callers cannot learn how close it was.
    """
    row = reg.get(exp_id)
    if row is None:
        raise KeyError(exp_id)
    if row["verdict"] != R.SURVIVED_DISCOVERY:
        raise ValueError(
            f"{exp_id} has verdict {row['verdict']!r}; only SURVIVED_DISCOVERY "
            f"candidates may be taken to the embargo")

    family = row["family_id"]
    used = reg.family_holdout_touches(family)
    if used >= MAX_FAMILY_TOUCHES:
        raise EmbargoClosed(
            f"family {family!r} has spent all {MAX_FAMILY_TOUCHES} embargo queries; "
            f"the slice is no longer independent of this family's search")

    spec = spec_mod.validate(json.loads(row["spec_json"]))

    # Spend the query BEFORE running it. If the harness throws midway, the look
    # still happened as far as the family's independence is concerned, and a
    # crash must not hand back a free query.
    touches = reg.record_holdout_touch(exp_id)

    if spec["type"] == "cross_sectional":
        wide = harness.load_wide(embargo=True, lag_days=spec["lag_days"])
        res = harness.run_cross_sectional(spec, wide)
    else:
        res = harness.run_event_study(spec, embargo=True)

    # The gate must judge the SAME quantity discovery judged. Reading the
    # per-event average here while the interval came from the calendar-time
    # series would let a spec pass discovery on one statistic and be promoted
    # or rejected on another -- and for event studies those two can disagree in
    # sign. One definition, shared with the skeptic.
    effect, _t = skeptic.primary_statistic(res)
    series = res.get("returns_signed", [])

    # An embargo query happens long after registration, so the build underneath
    # may have moved. That does not invalidate the query -- the embargo slice is
    # meant to be evaluated later -- but a silent change of evidence is exactly
    # the thing this repo refuses to allow, so it is recorded on the verdict.
    harness.stamp_data_version(res)
    graded_on = res.get("graded_on_data_version", "unknown")
    drifted = graded_on != row["data_version"]

    lo, hi = stats.block_bootstrap_mean_ci(np.asarray(series, dtype=float))
    passed = bool(
        np.isfinite(effect)
        and np.sign(effect) == spec["predicted_sign"]
        and np.isfinite(lo) and np.isfinite(hi)
        and lo > 0                      # signed series: positive means it worked
    )
    # Into the audit trail, not the verdict reason: that string is kept free of
    # numbers so it cannot leak anything about the result, and a build hash is
    # still a number to anyone reading it.
    if drifted:
        reg.note(exp_id, "holdout_data_version_drift",
                 {"registered_on": row["data_version"], "graded_on": graded_on})
    reg.record_verdict(
        exp_id, R.VAULT if passed else R.FAILED,
        "passed embargo" if passed else "failed embargo (no statistics disclosed)")
    return {"passed": passed, "touches": touches,
            "family_touches": reg.family_holdout_touches(family)}
