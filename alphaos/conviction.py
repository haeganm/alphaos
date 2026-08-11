"""The conviction sheet: what the surviving research actually implies today.

Only VAULT entries appear here -- specs that survived the full skeptic battery
on discovery data and then passed the embargo. Each line carries the effect
size and DSR registered at the time, so a name is never presented without the
evidence behind it and the honest magnitude of the edge.

Two things this file must never do, both of which it used to.

It must not publish stale picks. It read the discovery panel, whose last
rebalance is mid-2022, and stamped the result with today's date. An empty vault
hid that, so it would have surfaced first on the day something finally passed
the embargo -- the worst possible day to be silently four years out of date.

It must not invent its own ranking. It reimplemented the sort and skipped
winsorisation, neutralisation and the minimum-names floor, so for a
sector/size-neutralised spec the published names could be nearly disjoint from
the ones the backtest actually traded. It now asks the harness for the same
book the harness would trade.

At launch this file is empty. That is the correct output of a lab that has not
yet found anything.
"""
from __future__ import annotations

import json
import os
from datetime import date, timedelta

import numpy as np
import polars as pl

from . import harness, paths, registry as R, spec as spec_mod

OUT_PATH = paths.CONVICTION_SHEET
MAX_STALE_DAYS = 45


def current_book(spec: dict, wide: pl.DataFrame, n: int = 10) -> dict:
    """The long and short legs the harness itself would hold at the latest
    rebalance, built through the same winsorise -> neutralise -> decile path."""
    s = spec_mod.validate(spec)
    df = wide.with_columns(harness.eval_expr(s["signal"]).alias("signal"))
    latest = df["asof"].max()
    g = df.filter((pl.col("asof") == latest) & pl.col("signal").is_finite())
    if g.height < s["min_names"]:
        return {"asof": latest, "long": [], "short": [],
                "note": f"only {g.height} names with a signal; spec requires "
                        f"{s['min_names']}"}

    sig = harness._winsorize(np.asarray(g["signal"].to_list(), dtype=float),
                             s["winsorize"])
    if s["neutralize"]:
        sig, _ = harness._neutralize(
            sig,
            np.asarray(g["gics_sector"].fill_null("UNKNOWN").to_list()),
            np.asarray(g["mktcap"].to_list(), dtype=float),
            np.asarray(g["beta_252"].to_list(), dtype=float),
            s["neutralize"])

    order = np.argsort(sig)
    k = max(1, g.height // 10)
    tickers = g["ticker"].to_list()
    hi = [(tickers[i], float(sig[i])) for i in order[-k:][::-1]]
    lo = [(tickers[i], float(sig[i])) for i in order[:k]]
    # predicted_sign flips which decile is the long leg
    long_leg, short_leg = (hi, lo) if s["predicted_sign"] > 0 else (lo, hi)
    return {"asof": latest, "long": long_leg[:n], "short": short_leg[:n],
            "decile_size": k, "note": None}


def build(out_path=OUT_PATH, reg: R.Registry | None = None) -> str:
    own = reg is None
    reg = reg or R.Registry()
    try:
        vault = reg.by_verdict(R.VAULT)
        lines = [f"# Conviction sheet - {date.today().isoformat()}", ""]

        if not vault:
            lines += [
                "**No strategies in the vault.** Nothing has survived both the "
                "skeptic battery and the embargo, so this lab has no positions to "
                "suggest.",
                "",
                "This is the expected state early on, and an empty sheet is the "
                "honest output of a lab that has not found anything yet. Registry "
                f"to date: `{json.dumps(reg.summary())}`.",
                "",
                "Findings that failed are not wasted -- they are the map of where "
                "the edge is not. See `campaigns/*/postmortem.md`.",
            ]
        else:
            lines.append(f"{len(vault)} strategy(ies) in the vault.\n")
            wides: dict[int, pl.DataFrame] = {}
            for row in vault:
                spec = spec_mod.validate(json.loads(row["spec_json"]))
                res = json.loads(row["results_json"] or "{}")
                fwd = json.loads(row["forward_json"] or "{}")
                effect = res.get("mean_net", res.get("mean_bhar"))
                lines += [
                    f"## {row['id']} - {row['family_id']}",
                    "",
                    f"- {spec_mod.describe(spec)}",
                    f"- registered {row['registered_at']} on data_version "
                    f"`{row['data_version']}`",
                    f"- discovery effect "
                    f"{effect:.5f} per period" if effect is not None
                    else "- discovery effect unavailable",
                    f"- DSR {row['dsr']}",
                    f"- forward since freeze: {fwd.get('summary', 'not yet graded')}",
                    "",
                ]
                if spec["type"] != "cross_sectional":
                    lines.append("Event-driven: names appear when the trigger "
                                 "fires; see the nightly log.\n")
                    continue

                lag = spec["lag_days"]
                if lag not in wides:
                    # load_all, not the discovery panel: today's book has to be
                    # built from the most recent rebalance that exists.
                    wides[lag] = harness.load_wide(all_data=True, lag_days=lag)
                book = current_book(spec, wides[lag])

                stale = (date.today() - book["asof"]).days
                if stale > MAX_STALE_DAYS:
                    lines.append(
                        f"> **Not published.** Latest rebalance is {book['asof']} "
                        f"({stale} days old, limit {MAX_STALE_DAYS}). Rebuild the "
                        f"panel before trading this.\n")
                    continue
                if book["note"]:
                    lines.append(f"> {book['note']}\n")
                    continue

                lines.append(f"Book as of {book['asof']} "
                             f"(decile = {book['decile_size']} names, showing top 10):\n")
                lines.append("| side | ticker | signal |")
                lines.append("| --- | --- | --- |")
                for t, v in book["long"]:
                    lines.append(f"| long | {t} | {v:.4f} |")
                for t, v in book["short"]:
                    lines.append(f"| short | {t} | {v:.4f} |")
                lines.append("")
                lines.append("Expected edge is small and exists only in aggregate "
                             "across the whole book. No single name here is a "
                             "high-conviction bet.\n")

        text = "\n".join(lines)
        os.makedirs(os.path.dirname(str(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(text)
        return text
    finally:
        if own:
            reg.close()
