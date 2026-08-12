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
MIN_NEUTRALIZER_COVERAGE = 0.5  # a requested neutraliser must have real data
# Alpha must survive Fama-French 3 + momentum at the same bar as the raw
# effect. A spread whose returns are explained by market, size, value or
# momentum exposure is not a discovery -- it is a factor bet that several
# providers already sell for about five basis points. The harness has always
# computed this and the docstring always claimed it mattered; until now nothing
# enforced it, so a pure momentum repackaging could have reached the vault.
MIN_ABS_ALPHA_T = 2.0


def _primary(results: dict) -> tuple[float, float]:
    """(effect, t-stat) in whichever units this engine reports.

    For event studies both come from the calendar-time portfolio, never from
    the per-event average. They must be the same series or the sign check and
    the significance check are judging different quantities -- and they can
    genuinely disagree: officer departures average +0.27% per event while the
    calendar-time series runs negative, because days carrying many events are
    not the days carrying the positive ones. The calendar-time portfolio is
    also the thing you could actually hold (every name with a live event, each
    day), so it is the honest basis for a verdict.
    """
    if results.get("engine") == "event_study":
        return (results.get("calendar_effect", float("nan")),
                results.get("t_bhar", float("nan")))
    return results.get("mean_net", float("nan")), results.get("t_net", float("nan"))


def _check(name: str, passed: bool, detail: str, fatal: bool = True) -> dict:
    return {"check": name, "passed": bool(passed), "fatal": fatal, "detail": detail}


def concentration_share(returns) -> float:
    """Fraction of total absolute movement contributed by the single largest
    period. Always in [0, 1].

    The denominator is the sum of absolute returns, not the absolute sum.
    Partially cancelling periods make the latter tiny, which printed shares
    like "197% of total" -- and this is a fatal check, so it was killing
    hypotheses on arithmetic rather than on evidence.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    denom = float(np.abs(r).sum())
    if r.size == 0 or denom <= 1e-12:
        return 1.0
    return float(np.abs(r).max()) / denom


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

    # 3. bootstrap interval on the tradable (sign-adjusted) series excludes zero
    # from below. A CI sitting entirely below zero means the effect ran the
    # wrong way, which the sign check has already caught.
    ci = results.get("mean_bhar_ci") or results.get("mean_net_ci") or [float("nan")] * 2
    ci_ok = all(np.isfinite(ci)) and ci[0] > 0
    checks.append(_check("bootstrap_ci", ci_ok,
                         f"blocked 95% CI on the tradable series "
                         f"[{ci[0]:.5f}, {ci[1]:.5f}]"))

    # 4. concentration
    if is_event:
        share = results.get("top10_share", 1.0)
        checks.append(_check("concentration", share <= MAX_TOP10_SHARE,
                             f"top 10 events carry {share:.1%} of gross effect"))
        checks.append(_check("breadth", results.get("n_distinct_names", 0) >= 50,
                             f"{results.get('n_distinct_names')} distinct names"))
    else:
        share = concentration_share(results.get("returns", []))
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

    # 5a. factor alpha (cross-sectional only; there is no factor regression for
    # an event study, so the check does not apply rather than silently passing)
    if not is_event:
        if results.get("factor_alpha_error"):
            checks.append(_check(
                "factor_alpha", False,
                f"factor regression did not run ({results['factor_alpha_error']}), so "
                f"existing-factor exposure could not be ruled out"))
        else:
            a = results.get("ff4_alpha", float("nan"))
            at = results.get("ff4_alpha_t", float("nan"))
            betas = results.get("ff4_betas", {})
            ok = (np.isfinite(a) and np.isfinite(at)
                  and np.sign(a) == predicted and abs(at) >= MIN_ABS_ALPHA_T)
            checks.append(_check(
                "factor_alpha", ok,
                f"FF3+MOM alpha {a:+.5f} (t {at:+.2f}, needs |t| >= "
                f"{MIN_ABS_ALPHA_T} with the predicted sign); loadings "
                f"{ {k: round(v, 2) for k, v in betas.items()} }"))

    # 5b. a requested neutraliser that had no data is a mislabelled result
    cov = results.get("neutralize_coverage") or {}
    if cov:
        worst = min(cov, key=cov.get)
        checks.append(_check(
            "neutralizer_data", cov[worst] >= MIN_NEUTRALIZER_COVERAGE,
            f"{worst} neutralisation had real data for {cov[worst]:.0%} of names "
            f"(rest mean-imputed); spec claims neutralisation on {sorted(cov)}"))

    # 6. costs at 2x, and 7. placebo -- these rerun the harness
    child_specs: list[tuple[str, dict, dict]] = []
    if not is_event:
        if wide is None:
            raise ValueError("critique of a cross_sectional spec requires `wide`")
        double = dict(s)
        double["cost_bps"] = s["cost_bps"] * 2
        r2 = harness.run_cross_sectional(double, wide)
        e2, _ = _primary(r2)
        checks.append(_check("cost_x2", np.isfinite(e2) and np.sign(e2) == predicted,
                             f"effect at {double['cost_bps']}bps: {e2:+.5f}"))
        child_specs.append(("skeptic:cost_x2", double, r2))

        placebo = harness.run_cross_sectional(s, wide, shuffle_seed=1234)
        pt = placebo.get("t_gross", float("nan"))
        checks.append(_check("placebo", np.isfinite(pt) and abs(pt) <= PLACEBO_MAX_ABS_T,
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
        checks.append(_check("placebo", np.isfinite(pt) and abs(pt) <= PLACEBO_MAX_ABS_T,
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
