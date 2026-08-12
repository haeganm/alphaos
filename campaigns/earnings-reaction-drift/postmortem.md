# Campaign post-mortem: post-earnings-announcement drift

> **Second re-run 2026-08-12 after an external review. Outputs reproduce
> exactly.**
>
> That review fixed a nondeterminism in the cross-sectional engine (unspecified
> row order decided tied ranks) and a point-in-time defect in duration-fact
> restatements. Neither touches this campaign: event studies read filings and
> prices, not the fundamentals panel. All four primary outputs reproduce to the
> digit, which is itself worth recording -- it means the event engine's results
> are stable across processes and across a data rebuild.
>
> Factor-alpha enforcement was also added to the skeptic. It does not apply
> here: there is no factor regression for an event study, so the check is
> recorded as not-applicable rather than silently passing. If an event-driven
> strategy ever reaches the vault, its factor exposure will need a separate
> answer.
>
> The only movement is the deflated Sharpe, which shifted with the family
> variance (trial 3: 0.777 -> 0.856, still short of the 0.95 floor). Verdicts
> unchanged: 0 of 4.

> **REVISED 2026-08-11 after the harness hardening pass. The original reading of
> this campaign was wrong and is corrected below.**
>
> Three harness bugs affected these numbers. The decisive one: t-statistics and
> bootstrap intervals were computed on event arrays ordered by CIK (and, after
> conditioning, by reaction rank within quarter) rather than by date. The
> Newey-West correction assumes temporal adjacency, so it was correcting for
> the wrong dependence structure entirely. Records are now sorted by date
> before any inference.
>
> | Trial | Before | After |
> |---|---|---|
> | 1 top 30%, +2..+61 | +0.20%, t 1.05 | +0.37%, t 2.04 |
> | 2 bottom 30%, +2..+61 | +0.67%, t 3.16 | +0.15%, t 0.78 |
> | 3 top 30%, +2..+21 | +0.38%, t 3.94, **placebo t 3.64** | +0.29%, t 2.79, **placebo t 0.54** |
> | 4 top 30% vs sector | +0.06%, t 0.33 | +0.21%, t 1.38 |
>
> All four verdicts remain FAILED, but for materially different reasons, and
> two claims in the original write-up do not survive:
>
> **The placebo did not catch trial 3.** The original text called this campaign
> "the best advert for the placebo" because a shuffled condition also reached
> t = 3.64. That 3.64 was itself the ordering artifact. With the fix the
> placebo sits at t = 0.54 and trial 3 passes it cleanly. Trial 3 now passes
> sign, significance, the bootstrap interval, concentration, breadth, subperiod
> agreement **and** the placebo, and fails only the deflated Sharpe
> (0.777 against a 0.95 floor). It is a candidate that cannot clear the
> multiple-testing hurdle for a family of four, which is a much narrower claim
> than "the drift belongs to earnings events generally".
>
> **Trial 2's reversal was mostly an artifact.** The apparently significant
> +0.67% (t = 3.16) reversal after negative reactions falls to +0.15%
> (t = 0.78) once inference runs on date-ordered data. The "worth investigating
> instead" note about short-horizon reversal is withdrawn: there is no longer a
> result there to investigate.
>
> The structural baseline drift is still real and still the main caution for
> event studies here. What has changed is that the unconditioned-study placebo
> is now a matched random-date placebo (same names, same window length,
> non-filing dates) rather than a bit-identical rerun of the experiment, which
> is what it silently was before.
>
> Everything below the line is the original write-up, preserved. Where it
> conflicts with this note, this note is correct.

---


- **Campaign:** `earnings-reaction-drift-2026-08`
- **Family:** `pead` (4 distinct trials)
- **Data:** 21,902 earnings 8-Ks (item 2.02), 583 names, 2010-01 to 2022-06
- **Outcome: 0 of 4 survived discovery.**
- **Disclosure:** preliminary versions of trials 1 and 2 were run during harness
  development before pre-registration. These were not naive tests.

## The mechanism tested

Post-earnings-announcement drift is one of the most replicated anomalies in the
literature: prices keep moving in the direction of an earnings surprise for
weeks afterwards, because information diffuses slowly. Measured here from the
market's own day-+1 reaction rather than an accounting surprise, with the drift
window starting at day +2 so the conditioning bar is never part of the outcome.

## What happened

| # | Variant | Effect | t | Died at |
|---|---------|-------:|--:|---------|
| 1 | top 30% reaction, days +2..+61 | +0.20% | 1.05 | significance, DSR |
| 2 | bottom 30% reaction, days +2..+61 | **+0.67%** | 3.16 | **wrong sign**, subperiod |
| 3 | top 30% reaction, days +2..+21 | +0.38% | 3.94 | **placebo**, DSR |
| 4 | top 30%, vs sector ETF | +0.06% | 0.33 | significance, placebo, DSR |

## Mechanism-level lessons

**PEAD is not present in S&P 500 large caps over 2010-2022.** This is the
headline and it agrees with the literature on the anomaly's decay: drift
survives mainly in small, illiquid, thinly covered names. In the most analysed
500 companies on earth, information diffusion is not slow enough to trade. Do
not test PEAD again in this universe.

**Trial 3 is the instructive failure and the best advert for the placebo.** It
had everything a discovery is supposed to have: correct sign, t = 3.94, a
bootstrap interval excluding zero, agreement across both halves of the sample,
554 distinct names, and the top ten events contributing 1.4% of the effect. It
died because the *shuffled-condition placebo also reached t = 3.64*. Selecting
a random 30% of earnings events produced nearly the same measured drift as
selecting the top 30% by reaction — so the drift belongs to earnings events in
general, not to the reaction that supposedly identified them. Without that
check this would have been recorded as a finding, and it would have been wrong.

**There is a baseline positive drift in these events (+0.33%, t = 1.99
unconditional) that has nothing to do with surprise.** Its most likely source
is the survivorship residual: an event only enters the sample if the name still
has 61 sessions of price history afterwards, which quietly conditions on not
being delisted. Any future event study in this repo must be read against that
baseline, and the placebo is what makes it visible. This is a property of the
data, not of the hypotheses, and it is the single most useful thing this
campaign produced.

**Sector benchmarking removed almost all of it** (+0.06%, t = 0.33). Whatever
drift survives the market benchmark is largely sector co-movement during
earnings season.

**Negative reactions reverse rather than drift.** Trial 2 predicted continued
underperformance and got +0.67% with t = 3.16 — significant in the opposite
direction, in both halves of the sample. Under the pre-registration rule this
is a falsified hypothesis, not a discovery, and it is recorded as FAILED. It
is also the most interesting residue of the campaign: short-horizon reversal
after negative earnings reactions is a real literature. Anyone pursuing it must
register it as a new hypothesis, in a new family, and beat the same baseline
drift and placebo that killed trial 3 — the +0.67% is measured against a
sample that already drifts +0.33% for structural reasons.

## What not to do next

- Do not retest PEAD in the S&P 500 with a different window, fraction, or
  surprise definition. Four trials of one mechanism are already recorded.
- Do not treat trial 2's reversal as a finding on the strength of these
  numbers. It inherits the same baseline contamination.

## Worth investigating instead

Any event study in this repo should now be benchmarked against a matched
random-event placebo by default, not just against SPY. The placebo caught what
the benchmark did not.
