"""Nightly job: refresh data, forward-grade the vault, rebuild the sheet.

Forward grading is the part that matters. An embargoed slice can be peeked at;
data that did not exist when a spec was frozen cannot. Every night, each
surviving spec is re-run on rebalances that postdate its registration, and the
result is appended to the registry. A vault entry whose forward performance
decays toward zero gets demoted -- alphas rot, and a vault that only ever grows
is a trophy case rather than a research instrument.

Install (windowless, no console flash -- never wrap this in a .cmd):
    schtasks /create /tn AlphaOS-nightly /sc daily /st 04:30 ^
      /tr "C:\\AlphaOS\\.venv\\Scripts\\pythonw.exe C:\\AlphaOS\\scripts\\nightly.py"
"""
from __future__ import annotations

import json
import pathlib
import sys
import traceback
from datetime import date, datetime, timezone

import numpy as np
import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from alphaos import conviction, harness, panel, registry as R, spec as spec_mod, stats  # noqa: E402
from alphaos.ingest import events, prices, universe  # noqa: E402

LOG = pathlib.Path("data/nightly.log")
DEMOTE_AFTER_DAYS = 180


def log(msg: str):
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def refresh_data() -> str:
    uni = pl.read_parquet("data/universe.parquet")
    current = universe.members_on(uni, date.today())
    log(f"refreshing prices for {len(current)} current members")
    prices.fetch_all(current + prices.BENCHMARKS + prices.SECTOR_ETFS, ttl_hours=18)
    px = prices.load_panel()
    px.write_parquet("data/prices.parquet")

    ciks = uni.filter(pl.col("cik").is_not_null())["cik"].unique().to_list()
    log(f"refreshing filing events for {len(ciks)} CIKs")
    events.build(ciks)

    meta = panel.build()
    log(f"panel rebuilt, data_version={meta['data_version']}")
    return meta["data_version"]


def forward_grade(reg: R.Registry, data_version: str):
    """Re-run surviving specs on rebalances that postdate their registration."""
    live = reg.by_verdict(R.VAULT) + reg.by_verdict(R.SURVIVED_DISCOVERY)
    if not live:
        log("no surviving specs to forward-grade")
        return
    wide = None
    for row in live:
        try:
            spec = spec_mod.validate(json.loads(row["spec_json"]))
            frozen = row["registered_at"][:10]
            fwd_spec = dict(spec, start=frozen)
            if spec["type"] == "cross_sectional":
                if wide is None:
                    wide = harness.load_wide()
                res = harness.run_cross_sectional(fwd_spec, wide)
                series = res.get("returns_signed", [])
            else:
                res = harness.run_event_study(fwd_spec)
                series = res.get("returns_signed", [])

            arr = np.asarray(series, dtype=float)
            if arr.size < 3:
                summary = f"only {arr.size} periods since {frozen}; too early to judge"
                decayed = False
            else:
                t = stats.newey_west_tstat(arr)
                summary = (f"{arr.size} periods since {frozen}: mean "
                           f"{arr.mean():+.5f}, t {t:+.2f}")
                age = (date.today() - date.fromisoformat(frozen)).days
                decayed = age >= DEMOTE_AFTER_DAYS and arr.mean() <= 0

            reg.record_forward(row["id"], {
                "as_of": date.today().isoformat(), "frozen_at": frozen,
                "n_periods": int(arr.size),
                "mean": float(arr.mean()) if arr.size else None,
                "summary": summary, "data_version": data_version})
            log(f"  {row['id']}: {summary}")

            if decayed and row["verdict"] == R.VAULT:
                reg.record_verdict(row["id"], R.DEMOTED,
                                   f"forward performance decayed: {summary}")
                log(f"  {row['id']}: DEMOTED")
        except Exception:                            # noqa: BLE001
            log(f"  {row['id']}: forward grading failed\n{traceback.format_exc()}")


def main() -> int:
    log("=== nightly start ===")
    try:
        data_version = refresh_data()
    except Exception:                                # noqa: BLE001
        log(f"data refresh failed, continuing with existing data\n{traceback.format_exc()}")
        data_version = panel.current_version()

    reg = R.Registry()
    try:
        forward_grade(reg, data_version)
        conviction.build(reg=reg)
        log(f"conviction sheet rebuilt; registry: {json.dumps(reg.summary())}")
    finally:
        reg.close()
    log("=== nightly done ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
