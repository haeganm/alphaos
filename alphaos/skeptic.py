"""The adversary. Its job is to kill findings, and its default answer is no.

Ordered cheapest-and-deadliest first, because most candidates die to costs or
to a sign flip and there is no reason to spend a bootstrap on them.

The sign check deserves special mention: a signal that "works" backwards is a
falsified hypothesis, not a discovery. Accepting it would be the purest form
of data mining -- you predicted A, got not-A, and claimed a win either way. The
predicted sign is registered before the run precisely so this check has teeth.

A NO EDGE verdict is the expected outcome. The lab is calibrated so that
finding nothing is the normal, correct, informative result.
"""
from __future__ import annotations

import numpy as np

from . import harness, registry as R, spec as spec_mod, stats

# Deliberately strict. A finding has to survive all of them, not most of them.
MIN_ABS_T = 2.0
MIN_DSR = 0.95
MAX_TOP10_SHARE = 0.25          # event studies: no result carried by ten events
MAX_BEST_PERIOD_SHARE = 0.50    # cross-sectional: nor by one lucky month
MIN_COVERAGE = 0.60
PLACEBO_MAX_ABS_T = 2.5
MIN_HIGH_COVERAGE_RATIO = 0.0   # sign must hold post-2016; magnitude may shrink


def _primary(results: dict) -> tuple[float, float]:
    """(effect, t-stat) in whichever units this engine reports."""
    if results.get("engine") == "event_study":
        return results.get("mean_bhar", float("nan")), results.get("t_bhar", float("nan"))
    return results.get("mean_net", float("nan")), results.get("t_net", float("nan"))


def _check(name: str, passed: bool, detail: str, fatal: bool = True) -> dict:
    return {"check": name, "passed": bool(passed), "fatal": fatal, "detail": detail}


def critique(spec: dict, results: dict, *, reg: R.Registry, family_id: str,
             exp_id: str | None = None, wide=None, data_version: str = "",
             campaign_id: str = "") -> dict:
    """Run the battery. Returns {verdict, reason, checks, dsr}."""
    s = spec_mod.validate(spec)
    checks: list[dict] = []

    if results.get("error"):
        return {"verdict": R.FAILED, "reason": f"harness: {results['error']}",
                "checks": [_check("harness", False, results["error"])], "dsr": None}

    effect, tstat = _primary(results)
    is_event = results.get("engine") == "event_study"

    # 1. sign -- binding, and checked before anything else
    predicted = s["predicted_sign"]
    sign_ok = np.isfinite(effect) and np.sign(effect) == predicted
    checks.append(_check(
        "predicted_sign", sign_ok,
        f"predicted {predicted:+d}, observed {np.sign(effect):+.0f} (effect {effect:+.5f})"))

    # 2. significance
    checks.append(_check(
        "significance", np.isfinite(tstat) and abs(tstat) >= MIN_ABS_T,
        f"|t| = {abs(tstat):.2f} vs floor {MIN_ABS_T}"))

    # 3. bootstrap interval excludes zero
    ci = results.get("mean_bhar_ci") or results.get("mean_net_ci") or [float("nan")] * 2
    ci_ok = all(np.isfinite(ci)) and (ci[0] > 0 or ci[1] < 0)
    checks.append(_check("bootstrap_ci", ci_ok,
                         f"blocked 95% CI [{ci[0]:.5f}, {ci[1]:.5f}]"))

    # 4. concentration
    if is_event:
        share = results.get("top10_share", 1.0)
        checks.append(_check("concentration", share <= MAX_TOP10_SHARE,
                             f"top 10 events carry {share:.1%} of gross effect"))
        checks.append(_check("breadth", results.get("n_distinct_names", 0) >= 50,
                             f"{results.get('n_distinct_names')} distinct names"))
    else:
        rets = np.asarray(results.get("returns", []), dtype=float)
        if rets.size and abs(rets.sum()) > 1e-12:
            share = abs(rets[np.argmax(np.abs(rets))]) / abs(rets.sum())
        else:
            share = 1.0
        checks.append(_check("concentration", share <= MAX_BEST_PERIOD_SHARE,
                             f"single best period is {share:.1%} of total"))
        # Informational, not fatal. Low coverage on a fundamentals signal is
        # usually the signal's natural domain rather than missing data --
        # inventory does not exist for banks or most software companies, so an
        # inventory signal legitimately covers about half the index. The real
        # survivorship guard is high_coverage_period below, which retests on
        # years where price coverage exceeds 85%.
        checks.append(_check("coverage", results.get("coverage", 0) >= MIN_COVERAGE,
                             f"mean cross-section coverage {results.get('coverage', 0):.1%} "
                             f"of index members ({results.get('mean_names', 0):.0f} names)",
                             fatal=False))

    # 5. subperiod agreement -- one regime should not carry the whole result
    rets = np.asarray(results.get("returns", []), dtype=float)
    if rets.size >= 12:
        h = rets.size // 2
        a, b = float(rets[:h].mean()), float(rets[h:].mean())
        checks.append(_check("subperiod_sign", np.sign(a) == np.sign(b) == predicted,
                             f"first half {a:+.5f}, second half {b:+.5f}"))
    else:
        checks.append(_check("subperiod_sign", False, "too few observations to split"))

    # 6. costs at 2x, and 7. placebo -- these rerun the harness
    child_specs: list[tuple[str, dict, dict]] = []
    if not is_event:
        double = dict(s)
        double["cost_bps"] = s["cost_bps"] * 2
        r2 = harness.run_cross_sectional(double, wide)
        e2, _ = _primary(r2)
        checks.append(_check("cost_x2", np.isfinite(e2) and np.sign(e2) == predicted,
                             f"effect at {double['cost_bps']}bps: {e2:+.5f}"))
        child_specs.append(("skeptic:cost_x2", double, r2))

        placebo = harness.run_cross_sectional(s, wide, shuffle_seed=1234)
        pt = placebo.get("t_gross", float("nan"))
        checks.append(_check("placebo", not np.isfinite(pt) or abs(pt) <= PLACEBO_MAX_ABS_T,
                             f"shuffled-signal gross |t| = {abs(pt):.2f} "
                             f"(gross, so the cost drag of random turnover is not "
                             f"mistaken for predictive content)"))
        child_specs.append(("skeptic:placebo", s, placebo))

        high = dict(s)
        high["start"] = "2016-01-01"
        r3 = harness.run_cross_sectional(high, wide)
        e3, _ = _primary(r3)
        checks.append(_check(
            "high_coverage_period", np.isfinite(e3) and np.sign(e3) == predicted,
            f"2016+ (where price coverage exceeds 85%): {e3:+.5f}"))
        child_specs.append(("skeptic:high_coverage", high, r3))
    else:
        placebo = harness.run_event_study(s, shuffle_seed=1234)
        _, pt = _primary(placebo)
        checks.append(_check("placebo", not np.isfinite(pt) or abs(pt) <= PLACEBO_MAX_ABS_T,
                             f"shuffled-condition |t| = {abs(pt):.2f}"))
        child_specs.append(("skeptic:placebo", s, placebo))

    # 8. deflated Sharpe against everything else tried in this family. Computed
    # on the sign-adjusted (tradable) series, not the raw spread.
    signed = np.asarray(results.get("returns_signed", results.get("returns", [])),
                        dtype=float)
    n_trials = reg.family_trial_count(family_id)
    sr_var = reg.family_sharpe_variance(family_id)
    d = stats.deflated_sharpe(signed, n_trials=max(n_trials, 1), sr_variance=sr_var)
    checks.append(_check(
        "deflated_sharpe", np.isfinite(d["dsr"]) and d["dsr"] >= MIN_DSR,
        f"DSR {d['dsr']:.4f} (Sharpe {d['sharpe']:.3f} over {n_trials} family trials, "
        f"hurdle {d['expected_max_sharpe']:.3f}) vs floor {MIN_DSR}"))

    # record the reruns so the audit trail is complete, flagged so they do not
    # inflate the family's trial count (see registry.family_trial_count)
    if exp_id and campaign_id:
        for label, cspec, cres in child_specs:
            cid = reg.register(
                campaign_id=campaign_id, family_id=family_id,
                hypothesis=f"{label} rerun of {exp_id}",
                predicted_sign=predicted,
                falsification="parent finding must survive this variant",
                spec=cspec, data_version=data_version,
                parent_id=exp_id, trial_kind="skeptic")
            eff, _t = _primary(cres)
            reg.record_result(cid, cres, sharpe=cres.get("sharpe"), dsr=None)

    failed = [c for c in checks if c["fatal"] and not c["passed"]]
    verdict = R.FAILED if failed else R.SURVIVED_DISCOVERY
    reason = ("; ".join(f"{c['check']}: {c['detail']}" for c in failed)
              if failed else "survived every check on discovery data")
    return {"verdict": verdict, "reason": reason, "checks": checks,
            "dsr": d["dsr"], "dsr_detail": d, "n_family_trials": n_trials}


def summarize(crit: dict) -> str:
    lines = [f"VERDICT: {crit['verdict']}"]
    for c in crit["checks"]:
        lines.append(f"  [{'PASS' if c['passed'] else 'FAIL'}] {c['check']}: {c['detail']}")
    if crit["verdict"] == R.FAILED:
        lines.append(f"  reason: {crit['reason']}")
    return "\n".join(lines)
