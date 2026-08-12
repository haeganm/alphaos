# Campaign post-mortem: accruals (Sloan), second registration

- **Campaign:** `accruals-v2-2026-08` — **Family:** `accruals` (4 trials)
- **Outcome: 0 of 4 survived discovery — but variant 1 failed on the
  multiple-testing hurdle alone, and is the strongest result this lab has
  produced across 33 pre-registered hypotheses.**
- **Disclosure:** not a naive test. A growth-rate form ran in
  `operational-deterioration` (variant 5) and failed there; the prior exposure
  is recorded in `campaign.json` and that variant was re-run inside this family
  so both constructions face the same trial count.

| # | Variant | Effect /mo | t | FF4 alpha t | DSR | Failed on |
|---|---------|-----------:|--:|------------:|----:|-----------|
| 1 | (earnings − cash flow) / assets | −0.434% | **2.49** | **−3.19** | 0.887 | **DSR only** |
| 2 | same, sector+size neutral | −0.274% | 1.76 | −2.21 | 0.681 | significance, DSR |
| 3 | scaled by revenue | +0.049% | 0.23 | — | 0.060 | sign, significance, DSR |
| 4 | growth-rate form (from v1) | −0.339% | 1.67 | −3.44 | 0.747 | significance, DSR |

> **Note 2026-08-12.** A later audit found that the DSR variance pool was
> counting every re-run of a spec as another draw, which for campaigns run more
> than once mixed results computed under different code versions. It now takes
> the latest run per distinct spec. **This family is unaffected** — it has been
> run exactly once, four rows for four specs — so the 0.887 below is unchanged.
> The event-study families, which were re-run after the calendar-time
> correction, did shift; all remain far below the floor and all fail on other
> checks as well, so no verdict moves.

## The result that matters

Variant 1 — canonical Sloan accruals, scaled by assets — passed **every fatal
check except the deflated Sharpe**: predicted sign, significance (t = −2.49),
bootstrap interval excluding zero, concentration (best month 3% of total),
subperiod agreement, doubled costs, the shuffled-signal placebo, the
high-coverage 2016+ retest, and factor alpha (t = −3.19). It ran on 150
months, ~400 names per cross-section, 85% coverage, hit rate 55%, maximum
drawdown −12%, monthly Sharpe 0.20.

Its FF3+MOM regression R² is 0.067. Almost none of the return is explained by
market, size, value or momentum exposure — this is not a factor bet in
disguise, which is the single most common way a fundamentals result turns out
to be nothing.

**And it still failed, correctly.** DSR 0.887 against a 0.95 floor. Given four
distinct trials in this family, an observed Sharpe of 0.20 is not far enough
above what the best of four random tries would produce.

## The uncomfortable part, stated plainly

Had I registered only the canonical form, the family would have had N = 1, the
deflation term would have been zero, and this hypothesis would have reached
SURVIVED_DISCOVERY and earned an embargo query. **Testing three additional
variants of the same idea cost this hypothesis its verdict.**

That is not a flaw in the accounting; it is the accounting working. I would
have reported whichever of the four looked best, so the bar has to reflect
that I looked four times. But it does mean the verdict here is a statement
about *my search*, not only about the world — and the honest reading is:
"promising effect, insufficient evidence to distinguish it from the best of
four tries", not "no effect".

The temptation is to declare N = 1 retroactively, or to relax the floor. Both
are forbidden, and the ledger would show it.

## Mechanism-level lessons

**The deflator matters, and revenue scaling fails.** Variant 3 inverts to a
near-zero positive. That is the same warning sign the inventory campaign
produced when its ratio and difference forms disagreed — with one important
difference: there the two forms had comparable magnitudes and opposite signs
(genuine contradiction), here the revenue-scaled form is simply noise
(t = 0.23) rather than a competing claim. Assets are the deflator the
literature uses and the only one that works.

**Both level and growth constructions agree.** Variants 1 and 4 come from the
same economics via different arithmetic and produce −0.434% (t = 2.49) and
−0.339% (t = 1.67), with factor alphas of −3.19 and −3.44. Consistency across
constructions is the property the inventory mechanism lacked.

**Neutralisation weakens it by about a third** (variant 2: −0.274%, t = 1.76,
alpha t = −2.21). The effect is partly a sector/size tilt but survives their
removal with the right sign and a still-significant alpha, so it is not purely
a tilt.

**The factor alpha is consistently stronger than the raw effect.** Across
variants 1, 2 and 4 the alpha t-stat exceeds the raw t-stat. Factor exposures
are working *against* this signal and partially masking it — a rare and
encouraging pattern, and the reason enforcing the factor-alpha check made this
family look better rather than worse.

## What to do next — and what not to

**Do not** run a fifth variant. Every addition raises the hurdle for the whole
family, and the point estimates will not move enough to outrun that.

**Do not** carve the canonical form into its own family to reset N. The search
happened; the ledger records it.

What this mechanism needs is **out-of-sample time, not more in-sample
variants**. The correct next step is to let the existing registration age and
be forward-graded, then revisit it in a fresh family once genuinely new data
has accumulated — where a smaller, honest N and independent evidence can carry
it over the bar if the effect is real. The embargo slice remains untouched for
this family (0 of 3 queries spent), and it stays untouched: the protocol grants
a query only to SURVIVED_DISCOVERY, and this is a FAILED verdict.
