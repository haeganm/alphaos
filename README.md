# AlphaOS

[![CI](https://github.com/haeganm/alphaos/actions/workflows/ci.yml/badge.svg)](https://github.com/haeganm/alphaos/actions/workflows/ci.yml) [![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

A personal quantitative research laboratory. Claude proposes hypotheses as
declarative specs; deterministic Python tests them on point-in-time data; a
skeptic tries to kill the survivors; every trial is recorded in an append-only
ledger that knows how many things have been tried.

The product is not a trading strategy. It is the accumulating record of what
was tested, what died, at which check, and why - so that findings are scored
against the honest size of the search that produced them.

Runs on free data. Total cost: $0.

## What is actually built

| Piece | File | What it does |
|---|---|---|
| Ledger | `alphaos/registry.py` | Append-only DuckDB. Pre-registered fields cannot be rewritten; results cannot be overwritten; every mutation is logged |
| Statistics | `alphaos/stats.py` | Deflated Sharpe, blocked bootstrap, Newey-West, rank IC |
| PIT panel | `alphaos/panel.py` | Filing-dated fundamentals; YTD chains differenced to quarters; embargo split |
| Spec language | `alphaos/spec.py` | Validated JSON. Unknown keys are rejected, not ignored |
| Harness | `alphaos/harness.py` | The only code that computes a return. Cross-sectional + event study |
| Skeptic | `alphaos/skeptic.py` | Sign, significance, bootstrap, concentration, subperiod, costs, placebo, DSR |
| Controls | `alphaos/controls.py` | Proves the lab can tell signal from noise, in both directions |
| Embargo gate | `alphaos/holdout.py` | Returns PASS/FAIL and no statistics; counts every touch |

Read `CLAUDE.md` before proposing any research. It contains the rules that make
these numbers mean something.

## Data (all free)

- **Fundamentals**: SEC XBRL company facts, 758k facts across 757 companies,
  each stamped with its filing date. Restatements are kept as separate rows.
- **Universe**: S&P 500 membership reconstructed from Wikipedia revision
  history, quarterly since 2010: **889 distinct tickers** versus 503 today.
  That difference is the survivorship bias removed.
- **Prices**: Yahoo daily adjusted, 5.6M bars, 693 tickers. 33 ticker renames
  resolved through CIK continuity (UTX→RTX, ANTM→ELV…), accepted only when the
  target's history spans the old name's membership so bankruptcies are not
  spliced.
- **Events**: 297k filings (219k 8-Ks) with EDGAR acceptance timestamps.
- **Insiders**: 1.32M Form 4 transactions, 753 issuers, 2009→present, from
  SEC's quarterly bulk data sets (~64 zips rather than the 500k-700k
  per-filing requests the naive route needs). Every transaction code,
  ownership type and reporting-owner role, with acceptance timestamps joined
  from the submissions API at 100% coverage - so an event is dated by when the
  filing became public, never by when the trade happened.
- **Factors**: Fama-French 3 + momentum, daily.

## Verified, not asserted

- **Point-in-time correctness.** Apple's December-2014 quarter ($74.6B) is
  absent from the 2015-01-01 snapshot and present on 2015-02-01, because the
  10-Q was filed on January 28. Six lookahead tests in `tests/test_panel_pit.py`.
- **Event timing vs an independent implementation.** Re-graded 4,000 outcomes
  against AsOf (separate TypeScript codebase, its own price history):
  **100% entry-date agreement, correlation 0.9988–0.9995**, median difference
  0.00000. Run `python scripts/crossval_asof.py`.
- **The lab can say no.** Random noise, ticker-letter sorts, and near-constant
  signals all reach FAILED - even when granted the sign they happened to
  produce, so they cannot die on the sign check alone.
- **The lab can say yes.** A planted predictive signal reaches
  SURVIVED_DISCOVERY through the full battery. A skeptic that kills everything
  is broken and indistinguishable from a rigorous one.
- **Known factors replicate.** The 12-1 spread loads 1.05 on Fama-French MOM
  (R² 0.63); long-small/short-large loads 0.45 on SMB.

## Findings so far

**Eight campaigns, 34 pre-registered hypotheses, zero survivors.** That is the
expected base rate for one person mining public data, and the reason the ledger
exists. The asset being built is the map of where the edge is not.

| Family | N | Best result | Died on |
|---|--:|---|---|
| `accruals` | 4 | **−0.434%/mo, t = 2.49, FF4 alpha t = −3.19** | **DSR alone (0.887 vs 0.95)** |
| `operational_deterioration` | 5 | −0.339%/mo, t = 1.67, alpha t = −3.44 | significance, DSR |
| `pead` | 4 | +0.66%/60d, t = 1.84 | significance, DSR |
| `net_issuance` | 4 | −0.419%/mo, t = 1.40 | significance, alpha, DSR |
| `profitability` | 4 | +0.376%/mo, t = 1.07, HML −0.44 | significance, alpha, DSR |
| `governance_events` | 5 | +0.35%/61d, t = 1.05 | wrong sign, significance |
| `asset_growth` | 4 | −0.291%/mo, t = 0.92 | significance, DSR |
| `insider_buying` | 4 | +0.43%/61d, t = 0.91 | significance, placebo |

Event-study t-statistics are calendar-time (see below); earlier revisions of
this table quoted event-level figures that were substantially inflated.

Four patterns worth more than any single verdict:

**Event counts are not sample sizes.** Item 2.02 produces 48,276 events on
4,014 distinct dates, and a 61-day window makes every event overlap every other
one filed within three months - roughly **fifty independent blocks** across the
whole discovery span. Inference now runs on a calendar-time portfolio (Fama
1998) rather than on the event array. This cut the best earnings-drift result
from t = 2.81 to t = 0.88 and removed a t = 2.06 governance "finding"
entirely. Both had previously been described here as near-misses; neither was.

**The universe is probably the binding constraint - and that could not be
tested.** Rare-disclosure events (243 impairments, 74 auditor changes) and
insider cluster buys (1,030 in 17 years) fail for want of events, not want of
effect. Asset growth collapsed sixfold under size/sector neutralisation because
the S&P 500 has no small-cap tail. An attempt to widen to the S&P 400 MidCap
was built and then **rejected at its own pre-committed coverage gate**: Yahoo
has no history for most mid-caps that delisted, so point-in-time coverage runs
64-76% from 2012 to 2019, and EDGAR blanks a company's ticker once it stops
filing so there is no free way to recover it. The diagnosis stays plausible and
stays unverified. See `campaigns/_universe-expansion/postmortem.md` - it also
puts a price on the fix (~$40/month for delisting-covered prices) and notes the
one route that stays open at $0: mid-cap coverage is 99.6% *today*, so a study
that accumulates forward rather than backfilling would be clean.

**The placebo earns its keep repeatedly.** It killed an earnings-drift result
and an insider result by showing that random-date events drift just as much.
Any event study here must be read against it, never against the benchmark alone.

**Accruals is the standing candidate, and now the only one.** It passed every
check except the multiple-testing hurdle, with a factor-model R² of 0.067:
almost none of it is factor exposure. It is cross-sectional, so the
calendar-time correction does not touch it: its 150 monthly periods were
already non-overlapping and sequential by construction. Had it been registered
alone it would have survived; testing three sibling variants correctly raised
its bar. It needs out-of-sample time, not more variants. See
`campaigns/accruals-v2/postmortem.md`, including why the floor will not be
relaxed to let it through.

`campaigns/_prior-projects/` records the mechanisms retired by TechnicalEngine,
BTCTA, MYOWNTA and Density Desk. All four found no edge; that is the prior this
lab was calibrated against, and eight campaigns later it still holds.

Post-mortems carry revision notes rather than edits. The earnings campaign has
been revised three times - event ordering, then the placebo reading, then
calendar-time inference - and its verdict was 0 of 4 on every pass. The
verdicts have never changed; the reasons have been wrong twice, and both
corrections are recorded in place.

## Honest limitations

- **Survivorship residual.** Price coverage of historical members is ~76% in
  2010 rising to ~99% today; delisted names often have no Yahoo history. The
  skeptic's `high_coverage_period` check retests every finding on 2016+.
  Treat pre-2014 results with extra suspicion. Upgrade path: paid data
  (~$40/mo) if a survivor ever justifies it.
- **Event studies carry a structural baseline drift** (+0.33%, t = 1.99 for all
  earnings 8-Ks) because an event only enters the sample if the name has price
  history afterwards. Always read event results against the placebo.
- **Quarterly fundamentals** cannot support faster than monthly rebalancing.
- **Holding periods may not overlap**: `spec.py` enforces horizon == interval
  so every Sharpe and t-stat assumes independent periods honestly.
- **`mktcap` is sparser than prices**; size neutralisation falls back to the
  cross-sectional mean where `SharesOutstanding` is missing.
- **The embargo is enforced by discipline plus accounting**, not permissions.
  Three touches closes a family permanently. The only unleakable holdout is
  forward time, which is what `scripts/nightly.py` grades.

## Usage

```bash
python -m venv .venv && .venv/Scripts/pip install -r requirements.txt
python scripts/build_data.py            # cold build, ~30-60 min
python -m pytest tests/ -q              # 78 tests, runs from any directory
python -c "from alphaos import controls; print(controls.report(controls.run_all()))"
python scripts/run_campaign.py campaigns/<name>/campaign.json
python scripts/crossval_asof.py         # external oracle check
python scripts/backup_registry.py       # mirror the ledger to committed CSV
```

Nightly. `schtasks /create` cannot set a working directory, and without one the
task starts in `System32`; paths resolve absolutely via `alphaos/paths.py`
regardless, but set it anyway. Never wrap this in a `.cmd` - it flashes a
console window.

```powershell
$a = New-ScheduledTaskAction -Execute "C:\AlphaOS\.venv\Scripts\pythonw.exe" `
     -Argument "C:\AlphaOS\scripts\nightly.py" -WorkingDirectory "C:\AlphaOS"
$t = New-ScheduledTaskTrigger -Daily -At 4:30am
Register-ScheduledTask -TaskName AlphaOS-nightly -Action $a -Trigger $t -Force
```

`data/` is gitignored and every parquet in it is re-derivable from the network.
The experiment ledger is not, so `registry_export/` mirrors it to committed CSV
on every nightly run - losing it would silently reset the embargo touch counts.
