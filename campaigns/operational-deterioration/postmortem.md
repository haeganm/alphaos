# Campaign post-mortem: operational deterioration in filings

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
