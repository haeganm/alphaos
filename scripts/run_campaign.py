"""Run a research campaign: pre-register, test, critique, record.

A campaign is one theme, one family, one trial count. Every hypothesis in the
file is registered -- hypothesis text, mechanism, predicted sign, falsification
condition -- BEFORE any of them is tested, so the record cannot be rearranged
after the fact to make a result look predicted.

Specs are all validated up front. If one is malformed the campaign stops before
anything runs, because a half-registered campaign has a dishonest trial count.

Usage:
    python scripts/run_campaign.py campaigns/<name>/campaign.json
"""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from alphaos import harness, panel, registry as R, skeptic, spec as spec_mod  # noqa: E402


def main(path: str) -> int:
    cfg = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    campaign_id = cfg["campaign_id"]
    family_id = cfg["family_id"]
    hypotheses = cfg["hypotheses"]

    # Validate everything first: no partial campaigns.
    for i, h in enumerate(hypotheses):
        try:
            spec_mod.validate(h["spec"])
        except spec_mod.SpecError as exc:
            print(f"spec {i} ({h.get('hypothesis', '')[:60]}) invalid: {exc}")
            return 1
        for field in ("hypothesis", "mechanism", "falsification"):
            if not h.get(field, "").strip():
                print(f"spec {i} is missing {field}; pre-registration requires it")
                return 1

    data_version = panel.current_version()
    reg = R.Registry()
    out_dir = pathlib.Path(path).parent
    print(f"campaign {campaign_id} | family {family_id} | data_version {data_version}")
    print(f"{len(hypotheses)} hypotheses; family had "
          f"{reg.family_trial_count(family_id)} prior trials\n")

    # Pre-register every hypothesis before running any of them.
    registered = []
    for h in hypotheses:
        exp_id = reg.register(
            campaign_id=campaign_id, family_id=family_id,
            hypothesis=f"{h['hypothesis']}\n\nMechanism: {h['mechanism']}",
            predicted_sign=h["spec"]["predicted_sign"],
            falsification=h["falsification"],
            spec=h["spec"], data_version=data_version)
        registered.append((exp_id, h))
        print(f"  registered {exp_id}: {h['hypothesis'][:70]}")

    wide = None
    if any(h["spec"]["type"] == "cross_sectional" for _, h in registered):
        print("\nloading panel...")
        wide = harness.load_wide()

    results = []
    for exp_id, h in registered:
        spec = spec_mod.validate(h["spec"])
        print(f"\n{'=' * 72}\n{exp_id}: {h['hypothesis']}")
        print(f"  {spec_mod.describe(spec)}")
        res = harness.run(spec, wide=wide)
        if res.get("error"):
            print(f"  harness error: {res['error']}")
        reg.record_result(exp_id, res, sharpe=res.get("sharpe"))
        crit = skeptic.critique(spec, res, reg=reg, family_id=family_id,
                                exp_id=exp_id, wide=wide, data_version=data_version,
                                campaign_id=campaign_id)
        reg.record_verdict(exp_id, crit["verdict"], crit["reason"])
        print(skeptic.summarize(crit))
        results.append({"exp_id": exp_id, "hypothesis": h["hypothesis"],
                        "spec": spec, "results": res, "critique": crit})

    (out_dir / "results.json").write_text(
        json.dumps(results, indent=2, default=str), encoding="utf-8")

    survived = [r for r in results if r["critique"]["verdict"] == R.SURVIVED_DISCOVERY]
    print(f"\n{'=' * 72}\ncampaign complete: {len(survived)}/{len(results)} survived discovery")
    print(f"family {family_id} now has {reg.family_trial_count(family_id)} distinct trials")
    if not survived:
        print("Nothing survived. That is the expected outcome and it is a result:\n"
              "write the mechanism-level lesson into postmortem.md so the next\n"
              "campaign does not rediscover this dead end.")
    else:
        for r in survived:
            print(f"  {r['exp_id']} survived -- eligible for ONE embargo query "
                  f"(alphaos.holdout.query)")
    reg.close()
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
