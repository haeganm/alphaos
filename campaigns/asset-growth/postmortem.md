# Campaign post-mortem: asset growth

- **Campaign:** `asset-growth-2026-08` — **Family:** `asset_growth` (4 trials)
- **Outcome: 0 of 4 survived discovery.**

| # | Variant | Effect /mo | t | FF4 alpha t | DSR |
|---|---------|-----------:|--:|------------:|----:|
| 1 | yoy asset growth | −0.291% | 0.92 | −1.97 | 0.753 |
| 2 | same, sector+size neutral | −0.048% | 0.17 | −0.50 | 0.397 |
| 3 | asset growth − revenue growth | −0.070% | 0.31 | −0.38 | 0.433 |
| 4 | quarterly rebalance, 63d hold | −0.338% | 0.64 | −0.98 | 0.615 |

## Mechanism-level lessons

**All four carry the predicted negative sign and none is significant.** That
consistency is worth something: unlike the inventory campaign, where the ratio
and difference forms produced opposite signs, every construction here agrees on
direction. The mechanism is not being manufactured by a scaling choice. It is
simply small.

**Neutralisation kills it, which is the finding.** The raw sort gives −0.291%
with an FF4 alpha t of −1.97 — the closest thing to a result in this family.
Removing sector and size exposure collapses it to −0.048%, a sixfold reduction.
Asset growth in this universe is largely *a size and sector bet in disguise*:
small firms and capital-hungry industries grow their balance sheets fastest, and
sorting on growth sorts on those characteristics. Cooper, Gulen and Schill's
result held across size buckets in a universe spanning the whole market; the
S&P 500 has no small-cap tail for it to work in.

**Conditioning on revenue did not sharpen it.** Variant 3 tested whether the
over-investment story (assets growing without sales) is the operative
mechanism rather than plain extrapolation. It came out weaker, not stronger,
which is mild evidence against over-investment being the channel here — though
at these effect sizes the comparison is between two numbers that are both
indistinguishable from zero.

**Matching the holding period to the data frequency did not help.** Variant 4
rebalanced quarterly to stop re-ranking on stale balance-sheet data twice out
of every three months. The effect roughly matched variant 1 rather than
improving on it, so monthly staleness was not what was suppressing the signal.

## What not to do next

Do not test further balance-sheet growth definitions (net operating assets,
capex intensity, investment-to-capital). This family already establishes that
in a large-cap universe the growth-based mechanisms are absorbed by size and
sector. Any future investment-based hypothesis needs a reason why it would
escape that absorption, stated before the test.
