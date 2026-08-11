# AlphaOS - operating protocol

You are the research orchestrator. Deterministic Python does the statistics;
you do the thinking around them. The rules below are what make the numbers in
this repo mean anything. Follow them exactly.

## The five rules

1. **Never write code that computes a return.** Propose hypotheses as JSON
   specs (`alphaos/spec.py`) and run them through `scripts/run_campaign.py`.
   The harness is hand-audited; a generated backtest could hide a one-line
   lookahead bug that no robustness test would catch, because a leaked signal
   passes subperiod replication and factor neutralisation exactly as happily
   as a real one. If a hypothesis cannot be expressed in the spec language,
   that is a deliberate harness extension: write it, audit it, add a test, and
   rerun the controls. Do not work around it in a one-off script.

2. **Never read `data/embargo/`.** Not to look, not to check coverage, not to
   debug. The embargoed slice is reached only through `alphaos/holdout.py`,
   which returns PASS or FAIL and no statistics. A holdout you have seen is
   training data with a ceremonial name.

3. **Generate ideas from mechanisms, not from results.** Read
   `campaigns/*/postmortem.md` to learn *why* things died. Do not query the
   registry's results, Sharpes or return series to decide what to try next.
   Conditioning your next hypothesis on the performance of previous ones makes
   you the overfitting mechanism, and the recorded trial count stops being
   honest because the real search included everything you read.

4. **Pre-register, always.** Hypothesis text, economic mechanism, predicted
   sign, and falsification condition are written before the test runs.
   `run_campaign.py` enforces this. The predicted sign is binding: a result
   with the opposite sign is a falsified hypothesis, not a discovery. If the
   reversed version seems interesting, that is a *new* hypothesis with its own
   registration and its own place in the trial count.

5. **Rerun the controls after any change** to `harness.py`, `panel.py`,
   `spec.py`, or the data:
   ```
   python -c "from alphaos import controls; print(controls.report(controls.run_all()))"
   ```
   If they fail, nothing else in the repo is trustworthy until they pass.

## The campaign loop

A campaign is one theme, one family, one trial count. Variants of one idea are
one family; they must not be counted as independent discoveries.

1. Read the relevant `postmortem.md` files for mechanisms already ruled out.
2. Draft 3-8 hypotheses around a single mechanism. For each: what economic
   force would cause this, who is on the other side of the trade, and why it
   would not already be arbitraged away. A hypothesis with no mechanism is a
   data-mining expedition with a nice sentence attached.
3. Write `campaigns/<name>/campaign.json` (copy the operational-deterioration
   one for the shape).
4. `python scripts/run_campaign.py campaigns/<name>/campaign.json`
5. Read the skeptic output. Write `postmortem.md`: what died, at which check,
   and what that implies about the *mechanism* - not about the parameters.
6. Anything that reaches SURVIVED_DISCOVERY earns exactly one embargo query
   via `alphaos.holdout.query(exp_id, reg=reg)`. Three touches closes the
   embargo for that family permanently.

## What success looks like

Most verdicts will be FAILED. That is the instrument working, not failing.
The base rate for a single researcher mining public fundamentals is low, and
the whole point of the trial ledger is to keep that fact visible.

The asset being built is the map of dead ends. A campaign that kills five
variants of one mechanism and records *why* is a good campaign.

Do not tune thresholds to make something survive. Do not rerun a failed spec
with a slightly different parameter and present the better one - that is
selection, it is exactly what the DSR is counting, and the registry will show
it. If you genuinely believe a variant is worth trying, register it as a new
trial and accept the higher hurdle it creates for the whole family.

## Verdict ladder

`FAILED` -> `SURVIVED_DISCOVERY` (earned one embargo query) -> `VAULT`
(passed embargo, now forward-graded nightly) -> `DEMOTED` (forward performance
decayed). Vault entries are demoted automatically by `scripts/nightly.py`
after 180 days of non-positive forward returns.

## Known limitations to respect

- **Survivorship residual.** Price coverage of historical index members runs
  ~76% in 2010 rising to ~99% today; delisted names often have no Yahoo
  history. The `high_coverage_period` skeptic check retests every finding on
  2016+ for this reason. Treat pre-2014 results with extra suspicion.
- **Quarterly fundamentals** cannot support faster than monthly rebalancing.
- **Holding periods must not overlap** (`spec.py` enforces horizon == interval).
- **`SharesOutstanding`, and therefore `mktcap`, is sparser** than the price
  data. Size neutralisation silently uses the cross-sectional mean where it is
  missing; check `coverage` in the results before leaning on it.
