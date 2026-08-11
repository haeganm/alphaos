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

import numpy as np

from . import harness, registry as R, spec as spec_mod

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

    import json
    spec = spec_mod.validate(json.loads(row["spec_json"]))
    if spec["type"] == "cross_sectional":
        wide = harness.load_wide(embargo=True, lag_days=spec["lag_days"])
        res = harness.run_cross_sectional(spec, wide)
        effect = res.get("mean_net", float("nan"))
        series = res.get("returns_signed", [])
    else:
        res = harness.run_event_study(spec, embargo=True)
        effect = res.get("mean_bhar", float("nan"))
        series = res.get("returns_signed", [])

    from . import stats
    lo, hi = stats.block_bootstrap_mean_ci(np.asarray(series, dtype=float))
    passed = bool(
        np.isfinite(effect)
        and np.sign(effect) == spec["predicted_sign"]
        and np.isfinite(lo) and np.isfinite(hi)
        and lo > 0                      # signed series: positive means it worked
    )

    touches = reg.record_holdout_touch(exp_id)
    reg.record_verdict(
        exp_id, R.VAULT if passed else R.FAILED,
        "passed embargo" if passed else "failed embargo (no statistics disclosed)")
    return {"passed": passed, "touches": touches,
            "family_touches": reg.family_holdout_touches(family)}
