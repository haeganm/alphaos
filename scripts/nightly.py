"""Nightly job: refresh data, forward-grade the vault, rebuild the sheet.

Forward grading is the part that matters. An embargoed slice can be peeked at;
data that did not exist when a spec was frozen cannot. Every night, each
surviving spec is re-run on rebalances that postdate its registration and the
result is appended to the registry. A vault entry whose forward performance
decays gets demoted -- alphas rot, and a vault that only ever grows is a trophy
case rather than a research instrument.

Install (windowless, and with a working directory -- schtasks cannot set one,
so use the PowerShell cmdlets; never wrap this in a .cmd, it flashes a console):

    $a = New-ScheduledTaskAction -Execute "C:\\AlphaOS\\.venv\\Scripts\\pythonw.exe" `
         -Argument "C:\\AlphaOS\\scripts\\nightly.py" -WorkingDirectory "C:\\AlphaOS"
    $t = New-ScheduledTaskTrigger -Daily -At 4:30am
    Register-ScheduledTask -TaskName AlphaOS-nightly -Action $a -Trigger $t -Force

Paths are absolute regardless (alphaos.paths), so the working directory is
belt-and-braces rather than load-bearing.
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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from backup_registry import export as export_registry  # noqa: E402
from alphaos import conviction, harness, panel, paths, registry as R  # noqa: E402
from alphaos import spec as spec_mod, stats  # noqa: E402
from alphaos.ingest import events, prices, universe  # noqa: E402

DEMOTE_AFTER_DAYS = 180
MIN_FORWARD_PERIODS = 3


def log(msg: str):
    line = f"{datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')} {msg}"
    print(line, flush=True)
    paths.NIGHTLY_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(paths.NIGHTLY_LOG, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")


def refresh_data() -> str:
    uni = pl.read_parquet(paths.UNIVERSE)
    current = universe.members_on(uni, date.today())
    log(f"refreshing prices for {len(current)} current members")
    ok, missing = prices.fetch_all(current + prices.BENCHMARKS + prices.SECTOR_ETFS)
    log(f"  prices: {ok} ok, {len(missing)} without data")
    prices.load_panel().write_parquet(paths.PRICES)

    ciks = uni.filter(pl.col("cik").is_not_null())["cik"].unique().to_list()
    log(f"topping up filing events for {len(ciks)} CIKs (recent block only)")
    # recent_only + merge: the paged archive is 33 years of history that cannot
    # change, and re-fetching it nightly cost ~1,900 throttled requests to gain
    # a handful of rows. events.build refuses to shrink the archive.
    events.build(ciks, recent_only=True, merge=True)

    meta = panel.build()
    log(f"panel rebuilt, data_version={meta['data_version']}")
    return meta["data_version"]


def forward_grade(reg: R.Registry, data_version: str):
    """Re-run surviving specs on rebalances that postdate their registration."""
    live = reg.by_verdict(R.VAULT) + reg.by_verdict(R.SURVIVED_DISCOVERY)
    if not live:
        log("no surviving specs to forward-grade")
        return
    wides: dict[int, object] = {}
    for row in live:
        try:
            spec = spec_mod.validate(json.loads(row["spec_json"]))
            frozen = row["registered_at"][:10]
            fwd_spec = dict(spec, start=frozen)

            # all_data, not the discovery panel. Grading a frozen spec on data
            # that postdates it is evaluation, not search; reading the discovery
            # panel here made every forward result structurally empty and left
            # auto-demotion permanently unreachable.
            if spec["type"] == "cross_sectional":
                lag = spec["lag_days"]
                if lag not in wides:
                    wides[lag] = harness.load_wide(all_data=True, lag_days=lag)
                res = harness.run_cross_sectional(fwd_spec, wides[lag])
            else:
                res = harness.run_event_study(fwd_spec, all_data=True)

            arr = np.asarray(res.get("returns_signed", []), dtype=float)
            age = (date.today() - date.fromisoformat(frozen)).days
            if arr.size < MIN_FORWARD_PERIODS:
                summary = (f"{arr.size} periods since {frozen} ({age}d old); "
                           f"too early to judge")
                if age > 400:
                    # Genuinely too early is fine at 30 days, not at 400. That
                    # combination means the grader is broken, not patient.
                    log(f"  {row['id']}: WARNING frozen {age}d ago but only "
                        f"{arr.size} forward periods -- check the panel range")
                decayed = False
            else:
                t = stats.newey_west_tstat(arr)
                summary = (f"{arr.size} periods since {frozen}: mean "
                           f"{arr.mean():+.5f}, t {t:+.2f}")
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
            log(f"  {row['id']}: forward grading FAILED\n{traceback.format_exc()}")


def main() -> int:
    log("=== nightly start ===")
    paths.ensure_dirs()
    ok = True
    try:
        data_version = refresh_data()
    except Exception:                                # noqa: BLE001
        ok = False
        log(f"data refresh FAILED, continuing with existing data\n"
            f"{traceback.format_exc()}")
        data_version = panel.current_version(default="unknown")

    reg = R.Registry()
    try:
        forward_grade(reg, data_version)
        conviction.build(reg=reg)
        log(f"conviction sheet rebuilt; registry: {json.dumps(reg.summary())}")
        log(f"registry exported to {export_registry(reg)}")
    except Exception:                                # noqa: BLE001
        ok = False
        log(f"post-refresh stage FAILED\n{traceback.format_exc()}")
    finally:
        reg.close()
    log(f"=== nightly done ({'ok' if ok else 'WITH FAILURES'}) ===")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
