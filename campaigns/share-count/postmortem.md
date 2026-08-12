# Campaign post-mortem: net share issuance

- **Campaign:** `share-count-2026-08` — **Family:** `net_issuance` (4 trials)
- **Outcome: 0 of 4 survived discovery.**

| # | Variant | Effect /mo | t | FF4 alpha t | DSR |
|---|---------|-----------:|--:|------------:|----:|
| 1 | 1-year share-count growth | −0.036% | 0.15 | −0.75 | 0.212 |
| 2 | same, sector+size neutral | −0.029% | 0.10 | −0.60 | 0.205 |
| 3 | 2-year share-count change | −0.292% | 1.36 | −1.75 | 0.683 |
| 4 | issuance minus asset growth | −0.419% | 1.40 | −1.32 | 0.829 |

## Mechanism-level lessons

**The longer window is ten times the shorter one.** One-year issuance is
indistinguishable from zero (−0.036%); two-year issuance is −0.292% and the
issuance-minus-asset-growth form is −0.419%. Neither clears significance, but
the ordering is consistent and interpretable: a single year of share-count
change is dominated by option exercises, acquisition currency and buyback
timing, which are mechanical. Sustained multi-year issuance policy is the part
that reflects management's standing view of their own valuation. **If this
mechanism is ever revisited, the multi-year window is the only form worth
carrying forward** — and the one-year forms should be treated as already
falsified rather than re-tested.

**Issuance is not simply asset growth wearing a different hat.** Variant 4
subtracts asset growth to isolate the pure financing decision, and it produced
the *strongest* result in the family rather than the weakest. Whatever is in
share count is not the same thing the `asset_growth` family measured — the two
mechanisms are genuinely separable, and the signalling story survives the test
that would have collapsed it into an investment story.

**Nothing here is tradable, and the honest reason is power, not sign.** All
four carry the predicted negative sign; none reaches |t| = 2 on 150 months, and
none has a factor alpha clearing the bar either. A −0.4%/month effect with a
t of 1.4 is what a real-but-small effect and pure noise both look like at this
sample size, and this lab cannot distinguish them.

**Coverage note:** `SharesOutstanding` is the sparsest tag in the panel, and
these cross-sections ran at 75-78% coverage — lower than the accruals or asset
families. Some of the weakness in the one-year forms may be measurement noise
in the tag itself rather than in the economics.

## What not to do next

Do not test buyback-specific variants (dollar repurchases, buyback yield) as a
separate family — they are the same mechanism with the sign flipped, and the
tooling would correctly count them as more trials against the same idea. Do not
re-run the one-year window.
