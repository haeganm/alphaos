# Campaign post-mortem: insider buying

> **REVISED 2026-08-12 after an external review. Two data corrections and one
> inference correction; conclusions unchanged and strengthened.**
>
> **Amendment duplicates.** 227 open-market transaction identities appeared
> under more than one accession, 169 on different filing dates, with gaps
> running to 1,185 days — Form 4/A amendments re-reporting a trade that was
> already public. Left alone they create events dated long after the market
> learned the information. Transactions now resolve to their earliest
> disclosure. Cluster events fell from 1,034 to 1,030, and usable cluster
> events after price matching from ~470 to 442.
>
> **Cluster construction.** Two same-day bugs the review caught: filings that
> shared a date could take an acceptance timestamp from before the third
> filing was public, and insiders from a just-completed cluster could be
> counted again exactly 30 days later. Both fixed and pinned by tests.
>
> **Calendar-time inference** (see the earnings-reaction-drift post-mortem):
>
> | # | Variant | Effect | t naive | **t calendar** |
> |---|---|---:|---:|---:|
> | 1 | cluster, +1..+61 | −0.28% | −0.10 | −0.37 |
> | 2 | any officer buy, +1..+61 | +0.43% | +1.36 | **+0.91** |
> | 3 | cluster, +1..+21 | +0.11% | +0.78 | +0.28 |
> | 4 | cluster vs sector | −0.31% | −0.59 | −0.51 |
>
> The conclusions below stand. Variant 2 was already dead on its placebo; it is
> now also insignificant on its own statistic, so the campaign no longer rests
> on the placebo alone to reject it.



- **Campaign:** `insider-activity-2026-08` — **Family:** `insider_buying` (4 trials)
- **Data:** 1.32M Form 4 transactions, 753 issuers, 2009-2026, built for this
  campaign. 31k open-market purchases against 433k sales.
- **Outcome: 0 of 4 survived discovery.**

| # | Variant | Events | BHAR | t | Placebo t | DSR |
|---|---------|-------:|-----:|--:|----------:|----:|
| 1 | cluster buy, +1..+61 vs SPY | 1,034 | −0.076% | 0.10 | 1.64 | 0.214 |
| 2 | any officer/director buy, +1..+61 | 14,747 | +0.358% | 1.34 | **3.13** | 0.323 |
| 3 | cluster buy, +1..+21 | 1,034 | +0.286% | 0.79 | 0.70 | 0.552 |
| 4 | cluster buy vs sector ETF | 1,034 | −0.381% | 0.59 | 1.30 | 0.099 |

## Mechanism-level lessons

**The placebo killed the only variant with a plausible effect.** Variant 2 —
single officer or director purchases, 14,747 events across 488 names — showed
+0.358% drift with the right sign and subperiod agreement. Its matched
random-date placebo reached t = 3.13, nearly three times the effect's own
t-statistic. Selecting stocks *at random dates* produced comparable drift, so
what variant 2 measured is the structural baseline this repo documented in the
PEAD campaign, not information from insiders. Without that placebo this would
have been recorded as the campaign's finding.

**Cluster buying is too rare in this universe to test.** Three or more insiders
buying within thirty days happened 1,034 times in seventeen years across 333
issuers, and after price matching only ~158 distinct names survive. The
literature's strongest insider result is built on the whole market, where
cluster buys are common in small caps with concentrated insider ownership. S&P
500 executives are compensated in equity and are structurally net sellers —
the 14:1 sell-to-buy ratio in this data is the mechanism's own obstacle. **The
constraint is the universe, not the signal**, and this is now the second
campaign (after `governance_events`) to fail for that specific reason.

**Clustering did add something, in the one place it could be seen.** Variant 3
(clusters, one-month window) has a clean placebo at t = 0.70 while the
single-purchase variant's placebo blew out at 3.13. Requiring independent
agreement genuinely filters, and it survives the check the broader signal
fails — it simply has too few events to reach significance. If insider data is
revisited, the clustering rule is the part worth keeping.

**Horizon matters more than benchmark.** The one-month cluster window (+0.286%)
beat the three-month (−0.076%), consistent with information diffusion resolving
quickly if it resolves at all. The sector benchmark inverted the sign, which at
these event counts is not evidence of anything beyond noise.

## Data note

This campaign's real deliverable may be the dataset rather than the verdict.
`data/insiders.parquet` carries every transaction code, ownership type and
reporting-owner role, dated by filing acceptance rather than transaction date,
with 100% acceptance-stamp coverage joined from the submissions API. It cost
~64 zip downloads instead of the 500k-700k per-filing requests the naive
approach would have needed.

## What not to do next

- Do not test insider *sales*. 433k of them exist and they are liquidity and
  compensation events; prior work in this repo has already established that
  short-side equity strategies fail here.
- Do not loosen the cluster definition (2 insiders, 60 days) to manufacture
  more events. That is searching for a threshold that produces significance,
  which is what the trial counter exists to punish, and the constants are
  deliberately not spec-tunable for this reason.
- Do not re-test in this universe at all. Both remaining ideas — dollar-weighted
  purchase size, purchases against a backdrop of selling — need a cross-section
  where insiders actually buy.
