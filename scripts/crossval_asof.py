"""Cross-validate the event engine against AsOf's independently graded outcomes.

AsOf (C:\\AsOf, TypeScript) sealed and graded 6,081 8-K signals against SPY with
its own entry rule and its own copy of the price history. Reproducing its
numbers from this codebase is the closest thing to an external oracle the lab
has: two independent implementations agreeing on the same 5/20/60-day excess
returns is real evidence the entry timing and return alignment here are right.

Exact equality is not expected. Both sides use Yahoo adjusted closes fetched at
different times, and adjustment factors move with every dividend, so the test
is high correlation and a near-zero mean difference, not identity.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import polars as pl

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parents[1]))

from alphaos import harness  # noqa: E402
from alphaos.ingest import events as ev_mod  # noqa: E402


def main(limit: int = 4000) -> int:
    sig = ev_mod.asof_signals()
    if sig.is_empty():
        print("AsOf database not found; nothing to cross-validate")
        return 0

    px = pl.read_parquet("data/prices.parquet").sort(["ticker", "date"])
    series = {}
    for key, d in px.partition_by("ticker", as_dict=True).items():
        t = key[0] if isinstance(key, tuple) else key
        series[t] = (d["date"].to_list(), np.asarray(d["close"].to_list(), dtype=float))
    if "SPY" not in series:
        print("no SPY prices")
        return 1
    spy_dates, spy_close = series["SPY"]
    spy_idx = {d: i for i, d in enumerate(spy_dates)}

    # AsOf's price history begins 2021-08, but it graded backfilled signals
    # anyway: a 2013 8-K got an entry bar in August 2021 because that was the
    # first bar it had. Those outcomes are meaningless as an oracle (and are a
    # real defect in AsOf worth fixing there). Only signals that fired after its
    # price history starts can validate anything.
    valid_from = "2021-09-01"
    sig = sig.filter(pl.col("known_at") >= valid_from)
    print(f"comparing {sig.height} graded outcomes with known_at >= {valid_from} "
          f"(backfilled pre-2021 outcomes excluded: AsOf graded them against its "
          f"earliest available bar)")
    rows = sig.rows(named=True)[:limit]
    pairs: dict[int, list[tuple[float, float]]] = {5: [], 20: [], 60: []}
    entry_match = entry_total = 0

    for r in rows:
        t = r["ticker"]
        if t not in series:
            continue
        h = int(r["horizon_days"])
        known = harness._parse_accepted(r["known_at"])
        if known is None:
            continue
        dts, closes = series[t]
        i = harness.entry_index(dts, known)
        if i is None:
            continue

        # AsOf counts horizons on the benchmark's calendar and shares entry/exit
        # dates across both legs -- reproduce that, do not use the stock's bars.
        bi = spy_idx.get(dts[i])
        if bi is None or bi + h >= len(spy_dates):
            continue
        exit_date = spy_dates[bi + h]
        try:
            j = dts.index(exit_date)
        except ValueError:
            continue

        ours = (closes[j] / closes[i] - 1.0) - (spy_close[bi + h] / spy_close[bi] - 1.0)
        pairs[h].append((float(ours), float(r["excess_return"])))

        entry_total += 1
        if r["entry_at"]:
            asof_entry = datetime.fromisoformat(r["entry_at"].replace("Z", "+00:00"))
            if asof_entry.astimezone(timezone.utc).date() == dts[i]:
                entry_match += 1

    print(f"entry-date agreement: {entry_match}/{entry_total} "
          f"({100 * entry_match / max(entry_total, 1):.1f}%)")
    ok = True
    for h, ps in pairs.items():
        if len(ps) < 50:
            print(f"h={h:>2}: only {len(ps)} comparable events, skipped")
            continue
        a = np.asarray([p[0] for p in ps])
        b = np.asarray([p[1] for p in ps])
        corr = float(np.corrcoef(a, b)[0, 1])
        diff = a - b
        print(f"h={h:>2}: n={len(ps):>5} corr={corr:.4f} "
              f"mean_diff={diff.mean():+.5f} median_abs_diff={np.median(np.abs(diff)):.5f}")
        if corr < 0.95 or abs(diff.mean()) > 0.005:
            ok = False
    print("VERDICT:", "engines agree" if ok else "DISAGREEMENT -- investigate before trusting")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
