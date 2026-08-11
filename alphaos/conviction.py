"""The conviction sheet: what the surviving research actually implies today.

Only VAULT entries appear here -- specs that survived the full skeptic battery
on discovery data and then passed the embargo. Each line carries the effect
size and DSR that were registered at the time, so a name is never presented
without the evidence behind it and the honest magnitude of the edge.

At launch this file is empty. That is the correct output of a lab that has not
yet found anything, and an empty sheet is worth more than a populated one
built from findings that did not survive.
"""
from __future__ import annotations

import json
from datetime import date

import polars as pl

from . import harness, registry as R, spec as spec_mod

OUT_PATH = "data/conviction_sheet.md"


def _cross_sectional_picks(spec: dict, wide: pl.DataFrame, n: int = 10) -> list[dict]:
    """Current extreme decile under a surviving spec, at the latest rebalance."""
    df = wide.with_columns(harness.eval_expr(spec["signal"]).alias("signal"))
    latest = df["asof"].max()
    g = df.filter((pl.col("asof") == latest) & pl.col("signal").is_finite())
    if g.is_empty():
        return []
    desc = spec["predicted_sign"] > 0
    g = g.sort("signal", descending=desc)
    return [{"ticker": t, "signal": float(s), "side": "long" if desc else "short"}
            for t, s in zip(g["ticker"][:n], g["signal"][:n])]


def build(out_path: str = OUT_PATH, reg: R.Registry | None = None) -> str:
    own = reg is None
    reg = reg or R.Registry()
    try:
        vault = reg.by_verdict(R.VAULT)
        lines = [f"# Conviction sheet - {date.today().isoformat()}", ""]

        if not vault:
            summary = reg.summary()
            lines += [
                "**No strategies in the vault.** Nothing has survived both the skeptic "
                "battery and the embargo, so this lab has no positions to suggest.",
                "",
                "This is the expected state early on, and an empty sheet is the honest "
                "output of a lab that has not found anything yet. Registry to date: "
                f"`{json.dumps(summary)}`.",
                "",
                "Findings that failed are not wasted -- they are the map of where the "
                "edge is not. See `campaigns/*/postmortem.md`.",
            ]
        else:
            wide = harness.load_wide()
            lines.append(f"{len(vault)} strategy(ies) in the vault.\n")
            for row in vault:
                spec = spec_mod.validate(json.loads(row["spec_json"]))
                res = json.loads(row["results_json"] or "{}")
                fwd = json.loads(row["forward_json"] or "{}")
                lines += [
                    f"## {row['id']} - {row['family_id']}",
                    "",
                    f"- {spec_mod.describe(spec)}",
                    f"- registered {row['registered_at']} on data_version "
                    f"`{row['data_version']}`",
                    f"- discovery effect {res.get('mean_net', res.get('mean_bhar')):.5f} "
                    f"per period, DSR {row['dsr']}",
                    f"- forward since freeze: {fwd.get('summary', 'no forward data yet')}",
                    "",
                ]
                if spec["type"] == "cross_sectional":
                    picks = _cross_sectional_picks(spec, wide)
                    if picks:
                        lines.append("| ticker | side | signal |")
                        lines.append("| --- | --- | --- |")
                        for p in picks:
                            lines.append(f"| {p['ticker']} | {p['side']} | "
                                         f"{p['signal']:.4f} |")
                        lines.append("")
                lines.append("Expected edge is small and only exists in aggregate "
                             "across the whole book. No single name here is a "
                             "high-conviction bet.\n")

        text = "\n".join(lines)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return text
    finally:
        if own:
            reg.close()
