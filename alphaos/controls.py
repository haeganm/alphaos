"""Controls: unit tests for the laboratory itself.

Two failure modes matter and they pull in opposite directions.

FALSE POSITIVES -- the pipeline blesses noise. Tested by pushing deliberate
garbage (random numbers, a signal derived from the letters of the ticker)
through the complete pipeline including the skeptic. Every one must come out
FAILED.

FALSE NEGATIVES -- the pipeline is broken in a way that makes everything look
dead, and "no edge" stops being informative because it is the only thing the
machine can say. Tested by planting a signal that genuinely predicts forward
returns and requiring the harness to find it, loudly.

Both together are the claim worth making: this lab can tell signal from noise.

The positive controls check wiring against external ground truth rather than
asserting profits. That is deliberate. The obvious choices -- "momentum must
make money", "PEAD must appear" -- are both false in this universe and period:
12-1 momentum in S&P 500 large caps had a negative mean over 2010-2022 (the
2016, 2020 and 2021 crashes), and post-earnings drift has largely been
arbitraged out of liquid large caps. Requiring them to pay would force the
pipeline to be wrong. What must hold is that our momentum spread IS momentum
(beta ~1 on the Fama-French factor), and our size spread IS size.

Rerun after any change to harness, panel, or data_version.
"""
from __future__ import annotations

import tempfile

import numpy as np
import polars as pl

from . import harness, registry as R, skeptic

SEED = 20260811

MOM_SPEC = {"type": "cross_sectional", "signal": {"feature": "mom_12_1"},
            "rebalance": "monthly", "horizon_days": 21, "neutralize": [],
            "cost_bps": 10, "predicted_sign": 1}


def _doctor(wide: pl.DataFrame, values: np.ndarray) -> pl.DataFrame:
    """Replace mom_12_1 with a supplied column so a legitimate spec runs against
    controlled data. The spec language stays clean -- only the data is rigged."""
    return wide.with_columns(pl.Series("mom_12_1", values).alias("mom_12_1"))


# ---------------------------------------------------------------------------
# negative controls: garbage must die
# ---------------------------------------------------------------------------

def _garbage_columns(wide: pl.DataFrame) -> dict[str, np.ndarray]:
    rng = np.random.default_rng(SEED)
    n = wide.height
    tickers = wide["ticker"].to_list()
    return {
        "random_noise": rng.normal(size=n),
        # astrology tier: a letter of the ticker symbol, mapped to a number
        "ticker_letters": np.asarray(
            [float(ord(t[2]) if len(t) > 2 else ord(t[-1])) for t in tickers]),
        # a constant plus noise: no cross-sectional information at all
        "almost_constant": np.ones(n) + rng.normal(scale=1e-6, size=n),
    }


def run_negative_controls(wide: pl.DataFrame, reg: R.Registry,
                          data_version: str) -> list[dict]:
    out = []
    for name, col in _garbage_columns(wide).items():
        w = _doctor(wide, col)
        res = harness.run_cross_sectional(MOM_SPEC, w)

        # Adversarial: hand the garbage the sign it actually produced. Killing a
        # noise signal on the sign check alone proves nothing -- a researcher who
        # guessed the direction would have sailed past it. Some of these reach
        # |t| > 4 (a static ticker-letter sort is a fixed 12-year bet on ~39
        # names), so the remaining checks have to do the real work.
        observed = res.get("mean_net", 0.0)
        sign = 1 if observed >= 0 else -1
        adversarial = dict(MOM_SPEC, predicted_sign=sign)

        exp_id = reg.register(
            campaign_id="controls", family_id=f"control_neg_{name}",
            hypothesis=f"negative control: {name} should predict nothing, "
                       f"and is granted its observed sign to make the test hard",
            predicted_sign=sign,
            falsification="any surviving verdict is a pipeline failure",
            spec=adversarial, data_version=data_version)
        reg.record_result(exp_id, res, sharpe=res.get("sharpe"))
        crit = skeptic.critique(adversarial, res, reg=reg, family_id=f"control_neg_{name}",
                                exp_id=exp_id, wide=w, data_version=data_version,
                                campaign_id="controls")
        reg.record_verdict(exp_id, crit["verdict"], crit["reason"])
        passed = crit["verdict"] == R.FAILED
        killers = [c["check"] for c in crit["checks"] if c["fatal"] and not c["passed"]]
        out.append({"control": f"negative:{name}", "passed": passed,
                    "detail": f"verdict {crit['verdict']} with sign granted "
                              f"({sign:+d}); effect {res.get('mean_net', float('nan')):+.5f}, "
                              f"t {res.get('t_net', float('nan')):+.2f}; "
                              f"killed by {killers or 'NOTHING'}"})
    return out


# ---------------------------------------------------------------------------
# power control: a real signal must be found
# ---------------------------------------------------------------------------

def run_survivability_control(wide: pl.DataFrame, reg: R.Registry,
                              data_version: str, *, noise: float = 3.0) -> list[dict]:
    """Can the lab ever say yes?

    The most important control here. A skeptic strict enough to kill everything
    is not rigorous, it is broken -- and it would be indistinguishable from a
    rigorous one, because both only ever print FAILED. A genuinely predictive
    signal must be able to reach SURVIVED_DISCOVERY through the full battery.
    """
    rng = np.random.default_rng(SEED + 2)
    fwd = np.asarray(wide["fwd_21"].to_list(), dtype=float)
    sd = float(np.nanstd(fwd[np.isfinite(fwd)]))
    planted = np.where(np.isfinite(fwd), fwd, 0.0) + rng.normal(scale=noise * sd,
                                                                size=wide.height)
    w = _doctor(wide, planted)
    res = harness.run_cross_sectional(MOM_SPEC, w)
    exp_id = reg.register(
        campaign_id="controls", family_id="control_survivability",
        hypothesis="planted predictive signal must be able to survive the battery",
        predicted_sign=1,
        falsification="if this cannot survive, the skeptic is unfalsifiable",
        spec=MOM_SPEC, data_version=data_version)
    reg.record_result(exp_id, res, sharpe=res.get("sharpe"))
    crit = skeptic.critique(MOM_SPEC, res, reg=reg, family_id="control_survivability",
                            exp_id=exp_id, wide=w, data_version=data_version,
                            campaign_id="controls")
    reg.record_verdict(exp_id, crit["verdict"], crit["reason"])
    failed = [c["check"] for c in crit["checks"] if c["fatal"] and not c["passed"]]
    return [{
        "control": "survivability:lab_can_say_yes",
        "passed": crit["verdict"] == R.SURVIVED_DISCOVERY,
        "detail": f"planted signal verdict {crit['verdict']} "
                  f"(DSR {crit['dsr']:.4f}); blocked by {failed or 'nothing'}",
    }]


def run_power_control(wide: pl.DataFrame, *, noise: float = 3.0) -> list[dict]:
    """Plant a signal correlated with the forward return and require detection.

    Without this, "everything failed" is ambiguous between an honest lab and a
    broken one. The noise multiple keeps the planted edge realistic rather than
    perfect, so this tests sensitivity, not arithmetic.
    """
    rng = np.random.default_rng(SEED + 1)
    fwd = np.asarray(wide["fwd_21"].to_list(), dtype=float)
    planted = np.where(np.isfinite(fwd), fwd, 0.0) + rng.normal(
        scale=noise * np.nanstd(fwd[np.isfinite(fwd)]), size=wide.height)
    res = harness.run_cross_sectional(MOM_SPEC, _doctor(wide, planted))
    t = res.get("t_net", float("nan"))
    eff = res.get("mean_net", float("nan"))
    return [{
        "control": "power:planted_signal",
        "passed": bool(np.isfinite(t) and t > 4.0 and eff > 0),
        "detail": f"planted signal recovered with effect {eff:+.5f}, t {t:+.2f} "
                  f"(needs t > 4); IC {res.get('mean_ic', float('nan')):+.4f}",
    }]


# ---------------------------------------------------------------------------
# replication controls: known factors must be reproduced
# ---------------------------------------------------------------------------

def run_replication_controls(wide: pl.DataFrame) -> list[dict]:
    out = []

    mom = harness.run_cross_sectional(MOM_SPEC, wide)
    b = (mom.get("ff4_betas") or {}).get("mom", float("nan"))
    r2 = mom.get("ff4_r2", float("nan"))
    out.append({
        "control": "replication:momentum_is_momentum",
        "passed": bool(np.isfinite(b) and b > 0.5 and r2 > 0.3),
        "detail": f"our 12-1 decile spread loads {b:.2f} on Fama-French MOM "
                  f"(R^2 {r2:.2f}); needs beta > 0.5, R^2 > 0.3. Mean return is "
                  f"{mom.get('mean_net', float('nan')):+.5f}/month, which is NOT "
                  f"required to be positive -- large-cap momentum lost money this period",
    })

    size_spec = {"type": "cross_sectional",
                 "signal": {"op": "neg", "x": {"op": "log", "x": {"feature": "mktcap"}}},
                 "rebalance": "monthly", "horizon_days": 21, "neutralize": [],
                 "cost_bps": 10, "predicted_sign": 1}
    size = harness.run_cross_sectional(size_spec, wide)
    sb = (size.get("ff4_betas") or {}).get("smb", float("nan"))
    out.append({
        "control": "replication:size_is_size",
        "passed": bool(np.isfinite(sb) and sb > 0.3),
        "detail": f"long-small/short-large loads {sb:.2f} on SMB (needs > 0.3), "
                  f"confirming mktcap and the factor regression are wired correctly",
    })
    return out


# ---------------------------------------------------------------------------

def run_all(wide: pl.DataFrame | None = None, data_version: str = "controls") -> dict:
    if wide is None:
        wide = harness.load_wide()
    # Polars does not promise the row order of joins/unique. Stable ordering
    # makes seeded planted/garbage columns refer to the same companies on every
    # process invocation.
    wide = wide.sort(["asof", "ticker"])
    results: list[dict] = []
    with tempfile.TemporaryDirectory() as td:
        reg = R.Registry(f"{td}/controls.duckdb")
        try:
            results += run_replication_controls(wide)
            results += run_power_control(wide)
            results += run_survivability_control(wide, reg, data_version)
            results += run_negative_controls(wide, reg, data_version)
        finally:
            reg.close()
    passed = all(r["passed"] for r in results)
    return {"passed": passed, "results": results}


def report(res: dict) -> str:
    lines = []
    for r in res["results"]:
        lines.append(f"[{'PASS' if r['passed'] else 'FAIL'}] {r['control']}\n"
                     f"       {r['detail']}")
    lines.append("")
    lines.append("CONTROLS: " + ("ALL PASS -- pipeline can tell signal from noise"
                                 if res["passed"] else
                                 "FAILURE -- do not trust any result until fixed"))
    return "\n".join(lines)
