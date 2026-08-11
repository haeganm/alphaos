# AlphaOS

A personal quantitative research laboratory. Claude proposes hypotheses as
declarative specs; deterministic Python tests them on point-in-time data; a
skeptic tries to kill the survivors; every trial is recorded in an append-only
ledger that knows how many things have been tried.

The product is not a trading strategy. It is the accumulating record of what
was tested, what died, at which check, and why — so that findings are scored
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

- **Fundamentals** — SEC XBRL company facts, 758k facts across 757 companies,
  each stamped with its filing date. Restatements are kept as separate rows.
- **Universe** — S&P 500 membership reconstructed from Wikipedia revision
  history, quarterly since 2010: **889 distinct tickers** versus 503 today.
  That difference is the survivorship bias removed.
- **Prices** — Yahoo daily adjusted, 5.6M bars, 693 tickers. 33 ticker renames
  resolved through CIK continuity (UTX→RTX, ANTM→ELV…), accepted only when the
  target's history spans the old name's membership so bankruptcies are not
  spliced.
- **Events** — 297k filings (219k 8-Ks) with EDGAR acceptance timestamps.
- **Factors** — Fama-French 3 + momentum, daily.

## Verified, not asserted

- **Point-in-time correctness.** Apple's December-2014 quarter ($74.6B) is
  absent from the 2015-01-01 snapshot and present on 2015-02-01, because the
  10-Q was filed on January 28. Six lookahead tests in `tests/test_panel_pit.py`.
- **Event timing vs an independent implementation.** Re-graded 4,000 outcomes
  against AsOf (separate TypeScript codebase, its own price history):
  **100% entry-date agreement, correlation 0.9988–0.9995**, median difference
  0.00000. Run `python scripts/crossval_asof.py`.
- **The lab can say no.** Random noise, ticker-letter sorts, and near-constant
  signals all reach FAILED — even when granted the sign they happened to
  produce, so they cannot die on the sign check alone.
- **The lab can say yes.** A planted predictive signal reaches
  SURVIVED_DISCOVERY through the full battery. A skeptic that kills everything
  is broken and indistinguishable from a rigorous one.
- **Known factors replicate.** The 12-1 spread loads 1.05 on Fama-French MOM
  (R² 0.63); long-small/short-large loads 0.45 on SMB.

## Findings so far

Two campaigns, nine pre-registered hypotheses, **zero survivors** — the
expected base rate, and the reason the ledger exists.

- `campaigns/operational-deterioration/` — inventory, receivables and accrual
  deterioration. Effects near 5bp/month against 40x that in monthly vol; the
  ratio and difference forms of the same idea produced opposite signs.
- `campaigns/earnings-reaction-drift/` — PEAD in large caps. One variant
  reached t = 3.94 with correct sign and broad breadth, and died because the
  **shuffled-condition placebo reached t = 3.64**: the drift belonged to
  earnings events generally, not to the selection.
- `campaigns/_prior-projects/` — mechanisms retired by TechnicalEngine, BTCTA,
  MYOWNTA and Density Desk. All four found no edge; that is the prior.

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
- **Holding periods may not overlap** — `spec.py` enforces horizon == interval
  so every Sharpe and t-stat assumes independent periods honestly.
- **`mktcap` is sparser than prices**; size neutralisation falls back to the
  cross-sectional mean where `SharesOutstanding` is missing.
- **The embargo is enforced by discipline plus accounting**, not permissions.
  Three touches closes a family permanently. The only unleakable holdout is
  forward time, which is what `scripts/nightly.py` grades.

## Usage

```bash
python scripts/build_data.py            # cold build, ~30-60 min
python -m pytest tests/ -q              # 38 tests
python -c "from alphaos import controls; print(controls.report(controls.run_all()))"
python scripts/run_campaign.py campaigns/<name>/campaign.json
python scripts/crossval_asof.py         # external oracle check
```

Nightly (windowless — never wrap in a `.cmd`, it flashes a console):

```
schtasks /create /tn AlphaOS-nightly /sc daily /st 04:30 ^
  /tr "C:\AlphaOS\.venv\Scripts\pythonw.exe C:\AlphaOS\scripts\nightly.py"
```
