# Campaign post-mortem: operational deterioration in filings

> **Second re-run 2026-08-12 after an external review. Conclusions unchanged;
> one number is now more interesting than it was.**
>
> Two further fixes, both affecting this campaign:
>
> **Duration-fact restatements were being discarded.** `quarterly_series`
> collapsed each (company, tag, period) to its newest filing *before*
> differencing YTD chains, so an earlier vintage vanished. Concretely: Q1
> revenue filed in May, restated in September, left an August snapshot with no
> Q1 at all -- and therefore no derived Q2 either. The instant path never had
> this defect, which is why the original restatement test passed. Rebuilding
> recovers **60,810 periods that now carry multiple vintages** and raises the
> quarterly series from 511,835 to 563,842 observations.
>
> **Factor alpha is now enforced.** The harness always computed FF3+MOM alpha
> and the docstring always claimed a factor bet should die here, but no check
> read it. It is now fatal at |t| >= 2.0 with the predicted sign.
>
> | # | Variant | Effect /mo | t | FF4 alpha t |
> |---|---------|-----------:|--:|------------:|
> | 1 | inventory − revenue growth | −0.016% | 0.05 | −0.26 |
> | 2 | inventory growth alone | −0.166% | 0.59 | −1.20 |
> | 3 | receivables − revenue growth | +0.091% | 0.36 | +0.02 |
> | 4 | inventory / revenue ratio | +0.094% | 0.34 | +0.18 |
> | 5 | earnings − cash flow growth | −0.339% | 1.67 | **−3.44** |
>
> All five remain FAILED. Variants 1-4 are unambiguously dead: near-zero
> effects, no alpha, and 1 and 3 now also fail subperiod agreement.
>
> **Variant 5 is the one to note.** Its factor alpha (t = −3.44) is
> substantially stronger than its raw spread (t = −1.67), meaning market, size,
> value and momentum exposure were working *against* the signal and partially
> masking it. It passes sign, concentration, subperiod, doubled costs, the
> placebo, the high-coverage retest and now factor alpha, and fails on raw
> significance and the deflated Sharpe (0.794 against a 0.95 floor). That is
> still a failure and must not be reported as anything else -- but it is a
> failure of the multiple-testing bar with a real residual alpha underneath,
> which is a different situation from the other four. If this mechanism is ever
> revisited it must be pre-registered afresh, in a new family, and clear the
> bar on its own evidence rather than inheriting this note's optimism.

> **First re-run 2026-08-11 after the harness hardening pass. Conclusions unchanged.**
>
> All five verdicts remain FAILED and the mechanism-level lessons below still
> hold. Two things did change and are worth recording:
>
> **Every trial now faces the same hurdle.** Previously the deflated Sharpe was
> computed as each hypothesis finished, so the first one in the file was scored
> against a variance estimated from a single row -- undefined, which collapsed
> the deflation to zero and gave it *no multiple-testing penalty at all* --
> while later ones faced hurdles of 0.032, 0.063, 0.084 and 0.108 depending
> purely on their position. Campaigns now run every trial before judging any,
> so all five are scored against one completed family variance (hurdle 0.094).
>
> **The concentration check was arithmetically broken.** It divided by the
> absolute sum of period returns instead of the sum of absolute returns, so
> partially cancelling series produced "single best period is 197% of total".
> That is not a share, and it is a fatal check: it was killing trials 1 and 4
> on arithmetic rather than evidence. Both now pass concentration and fail on
> their actual weakness -- magnitude.
>
> Trial 5 (accruals) remains the only one with a pulse: correct sign, survives
> costs, placebo, subperiod and the high-coverage retest, failing on
> significance (t = 1.61) and DSR (0.778). That is unchanged from before.



- **Campaign:** `operational-deterioration-2026-08`
- **Family:** `operational_deterioration` (5 distinct trials)
- **Data version:** 2026-08-11 build, discovery window 2010-01 to 2022-06
- **Outcome: 0 of 5 survived discovery.**

## The mechanism tested

If working capital grows faster than sales, something is accumulating that has
not been sold or collected. The behavioural claim was that the market anchors
on headline revenue and earnings and underweights the balance-sheet detail, so
deterioration is visible in the filing before it is visible in the price.

Five expressions of that one idea: inventory growth against revenue growth,
inventory growth alone, receivables against revenue, the inventory-to-revenue
ratio, and earnings growth against operating cash flow growth.

## What happened

| # | Variant | Effect /mo | t | Died at |
|---|---------|-----------:|--:|---------|
| 1 | inventory growth − revenue growth | −0.049% | 0.17 | significance, concentration, DSR |
| 2 | inventory growth alone | −0.145% | 0.51 | significance, DSR |
| 3 | receivables growth − revenue growth | **+0.197%** | 0.72 | **wrong sign**, subperiod, DSR |
| 4 | inventory / revenue ratio | **+0.102%** | 0.35 | **wrong sign**, concentration, DSR |
| 5 | earnings growth − cash flow growth | −0.353% | 1.61 | significance, DSR |

## Mechanism-level lessons

**The inventory channel is dead in this universe, and not because of costs.**
Variants 1 and 2 carried the predicted sign and survived doubled costs, the
placebo, and the high-coverage-period retest. They died on magnitude: the
effect is about 5 basis points a month against a monthly standard deviation
roughly forty times larger. There is nothing here to tune. A sharper inventory
definition would be searching for a signal whose own point estimate says it is
not there.

**The ratio form contradicts the difference form.** Variants 1 and 4 express
the same economics and came out with opposite signs, both insignificant. That
is the signature of noise being shaped by the scaling choice rather than of an
economic effect, and it is the strongest single reason to close this mechanism
rather than refine it. Any future variant that only works in one algebraic
form should be treated as failed on arrival.

**Receivables ran backwards.** Predicted negative, observed positive, and the
two halves of the sample disagreed. Pre-registration is what makes this a
falsification instead of a rebranded discovery about "revenue quality".

**The accruals variant is the only one worth remembering.** Variant 5 was the
sole hypothesis to pass sign, subperiod agreement, doubled costs, the placebo,
concentration *and* the high-coverage retest, failing only significance
(t = 1.61, needs 2.0) and DSR. Its effect roughly doubled on 2016+ data
(−0.672%/mo). That is not a discovery — it is one variant landing short of the
bar, which is exactly what noise looks like a decent fraction of the time. It
is recorded so a future campaign knows this is the branch with a pulse, and
knows it must clear the bar on its own evidence rather than inherit optimism.

**Structural note: inventory signals only cover about half the index.** Banks,
insurers and most software companies hold no inventory, so these
cross-sections ran ~237 names against ~480 members. That is the signal's
natural domain rather than a data defect, but it means an inventory result
would only ever have applied to half the universe.

## What not to do next

- Do not re-run these with different lags, winsorisation, or neutralisation
  sets. Five variants of one mechanism already sit in this family; more would
  raise the DSR hurdle for everything here while chasing point estimates that
  are indistinguishable from zero.
- Do not fold receivables or the ratio form into a composite score and retest.
  A composite of five things that individually failed is a sixth trial, not
  new evidence.

## Worth investigating instead

The mechanism assumed the market is slow to read the balance sheet. In S&P 500
names, covered by dozens of analysts, that assumption is weak on its face. Any
follow-up should target where slow information processing is actually
plausible: smaller and less covered names (out of reach with this universe), or
disclosures that are genuinely hard to parse rather than merely tabular.
