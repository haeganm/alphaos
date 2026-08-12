# Campaign post-mortem: gross profitability

- **Campaign:** `quality-profitability-2026-08` — **Family:** `profitability` (4 trials)
- **Outcome: 0 of 4 survived discovery.**

| # | Variant | Effect /mo | t | FF4 alpha t | Coverage | DSR |
|---|---------|-----------:|--:|------------:|---------:|----:|
| 1 | gross profit / assets | +0.376% | 1.07 | +1.35 | 53% | 0.648 |
| 2 | same, sector+size neutral | +0.321% | 1.12 | +1.24 | 53% | 0.696 |
| 3 | operating income / assets | +0.029% | 0.08 | +0.36 | 67% | 0.244 |
| 4 | change in gross profitability | −0.036% | 0.15 | −0.14 | 51% | 0.170 |

## Mechanism-level lessons

**The signal is directionally right and too weak to trade.** Both level forms
carry the predicted positive sign, survive doubled costs, the placebo and the
high-coverage retest, and hold their sign after sector and size
neutralisation — variant 2 also agrees across both halves of the sample. What
they do not have is significance: t ≈ 1.1 on a 150-month series. An effect of
0.32%/month sounds material until you notice the monthly standard deviation is
roughly thirty times larger.

**The signal is behaving like profitability, which is the useful part.** Both
level variants load −0.44 and −0.34 on HML. That is the documented signature:
profitable firms look expensive on book-to-market, so gross profitability is
structurally anti-value. Seeing the loading come out with the right sign and
magnitude is evidence the construction is faithful, independent of whether it
pays. That matters more than the verdict here, because it means a null result
from this panel is informative rather than a symptom of broken plumbing.

**Gross beats operating, as Novy-Marx argued.** Variant 3 is essentially zero
(t = 0.08). The claim that gross profit is the cleaner measure — further from
accounting discretion and from expensed investment — is the one part of this
campaign the data agrees with, even though neither version clears the bar.

**Profitability is a level, not news.** Variant 4 (change in profitability)
inverts to a near-zero negative. The effect, such as it is, lives in the stable
characteristic rather than in its change, which points to a risk premium
rather than mispricing — and a risk premium is exactly the thing that should
not survive a factor-alpha check, which it does not.

**Coverage caveat.** Gross profitability needs `CostOfRevenue`, which financial
firms do not report; these cross-sections ran ~253 names against ~480 members.
That is the signal's natural domain rather than missing data, but any result
here would only ever have applied to half the index.

## What not to do next

Do not re-run with different deflators (sales, book equity) or different
profitability definitions. Four variants of one mechanism are already recorded,
and the point estimates are not close enough to the bar for a fifth definition
to be the difference between noise and edge. If profitability is revisited it
should be as an *interaction* with a distinct mechanism, registered as a new
family, not as a fifth way to divide gross profit by something.
