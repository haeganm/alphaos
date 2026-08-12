# Post-mortem: can this lab test a wider universe at $0?

**Answer: no, and the number is 64%.** The attempt was stopped at its
pre-committed gate before any hypothesis was run.

- **Date:** 2026-08-12
- **What was under test:** not a market mechanism, but this lab's own repeated
  diagnosis of itself.
- **Outcome:** the expansion was built, measured, and rejected. The active
  universe remains S&P 500 only.

## Why this was attempted

Four separate campaign post-mortems had reached the same conclusion in the
lab's own words: *the universe is the binding constraint, not the mechanisms.*
Insider cluster buys produced 1,030 events in 17 years; material impairments
243; auditor changes 74; asset growth collapsed sixfold under size/sector
neutralisation because the S&P 500 has no small-cap tail.

That is a comfortable explanation for eight campaigns of nulls, and comfortable
explanations are exactly the ones to distrust. It was an assertion, never a
test. The plan was to widen the cross-section and see whether the constrained
mechanisms moved — with the honest expectation stated up front that *nothing
moving* would be the more valuable result, because it would redirect the
project away from cross-sectional fundamentals entirely.

## What was built

The S&P 400 MidCap universe, using the same Wikipedia revision-history
reconstruction that recovered 889 historical S&P 500 tickers including the 386
that left. It worked exactly as it did for large caps: **1,081 distinct MidCap
tickers across 67 quarterly snapshots, 746 departures recorded**, roughly 400
members in every year from 2011. Combined with the S&P 500 that is 1,707
historical tickers and ~900 members per year.

Membership was never the problem.

## The gate, and the number

The decision rule was written into the plan **before the coverage was known**:

> Proceed only if S&P 400 price coverage is ≥ 80% in every year from 2012 and
> the combined universe holds ≥ 85% from 2014. If coverage lands below that,
> stop and write it up as the phase result. Do not proceed and caveat; the
> caveat would not be strong enough.

Measured (`scripts/coverage_report.py`):

| Year | S&P 400 | Combined | S&P 500 |
|---|---:|---:|---:|
| 2012 | **64.8%** | 72.2% | 78.6% |
| 2014 | **64.2%** | **73.5%** | 80.8% |
| 2016 | **67.2%** | **76.8%** | 84.5% |
| 2018 | **75.8%** | **83.0%** | 89.1% |
| 2020 | **79.8%** | 85.5% | 90.7% |
| 2022 | 86.0% | 90.4% | 94.1% |
| 2024 | 92.5% | 94.9% | 96.8% |
| 2026 | 99.8% | 99.6% | 99.4% |

The gate failed in every year from 2012 to 2020. A mid-cap panel for the
2010-2022 discovery window would be missing a third of its names, and missing
them non-randomly: the absent names are precisely the ones that delisted.

## Why it cannot be fixed at $0

Yahoo simply has no history for most mid-caps that stopped trading. The obvious
workaround — recover the ticker from SEC and fetch under that symbol — does not
exist, because **EDGAR blanks a company's `tickers` field once it stops
filing.** Carefusion and Monsanto, both acquired, both return `[]`. There is no
free path from a dead CIK back to the symbol it traded under, which also rules
out the broader "all US registrants" universe that the 20,200 CIKs already
sitting in `companyfacts.zip` would otherwise make trivial.

Wikipedia's revision history is the only free record of who was in an index and
under what symbol. It solves membership completely and prices not at all.

## What this changes

**The diagnosis is still untested.** This phase set out to falsify "the
universe is the binding constraint" and could not run the experiment. That
distinction matters: the lab has not shown the diagnosis is right, and must not
start citing it as though it had. It remains the most plausible explanation for
eight campaigns of nulls, and it remains unverified.

**The constraint is sharper than it looked.** It is not "the S&P 500 is only
500 names." It is *this lab cannot observe smaller companies that stopped
trading, at any price it currently pays.* That is a more specific and more
actionable statement, and it puts a number on the upgrade: a price source with
delisting coverage (Sharadar, Norgate — roughly $40/month) is the single
purchase that would unlock every mechanism class currently out of reach.
Before this phase that would have been a guess; it is now a costed decision.

**There is one route that stays open at $0, and it runs forwards.** Coverage
reaches 92% by 2024 and 99.6% in 2026 — the hole is entirely historical,
because currently-listed mid-caps are perfectly well covered. A mid-cap study
that *accumulates* from today rather than backfilling from 2010 would be clean.
It would need years before it could say anything, which is precisely what
`nightly.py` and the new WATCH status exist to accumulate. Worth revisiting in
2028, not before.

## What was kept

The rejection is of the data, not the code. All of this survives and is tested:

- `ingest/universe.py` builds any index from Wikipedia revision history
  (`INDEX_PAGES`, `build_index`), so re-enabling MidCap is one line.
- The panel carries an `index` tier per row.
- **`spec.universe` is now binding.** It was validated, normalised, and then
  ignored by both engines — harmless while one universe existed, and a silent
  redefinition of all 34 recorded hypotheses the moment a second one appeared.
  Specs default to `sp500_pit`, so the existing ledger means exactly what it
  meant. `sp900_pit` is deliberately *absent* from `UNIVERSES`: the validator
  refuses a universe that failed its own coverage gate.

## What not to do next

- Do not run campaigns on the MidCap universe with a caveat. The pre-committed
  rule exists so that a 30% survivorship hole cannot be argued away later, and
  the argument would be tempting precisely because the data is already built.
- Do not lower the gate. It was set before the number was seen.
- Do not attempt the S&P 600 (no usable page history before 2018) or an
  all-registrant universe (no ticker mapping for dead CIKs).
