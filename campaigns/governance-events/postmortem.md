# Campaign post-mortem: governance and bad-news 8-K disclosures

> **REVISED 2026-08-12. The positive-drift finding does not survive honest
> inference.**
>
> Inference now runs on a calendar-time portfolio rather than on the array of
> events (see the earnings-reaction-drift post-mortem for why: same-day
> clustering plus 61-day window overlap leaves ~50 independent blocks in the
> whole discovery span, not tens of thousands).
>
> | # | Event | Effect | t naive | **t calendar** |
> |---|---|---:|---:|---:|
> | 1 | 5.02 departures | +0.35% | **+2.06** | **−1.05** |
> | 2 | 2.05 restructuring | +1.17% | +1.60 | −1.24 |
> | 3 | 2.06 impairment | −1.04% | −0.67 | +0.85 |
> | 4 | 4.01 auditor change | +2.19% | +0.26 | −1.23 |
> | 5 | 5.02 vs sector | +0.21% | +1.50 | −0.78 |
>
> (The t column is signed against the hypothesis: these predicted a negative
> drift, so a positive effect yields a negative t. Trial 1's effect is still
> positive and still falsifies its predicted sign — it simply is not
> significant in either direction.)
>
> **What changes.** The original write-up said departures showed "a real,
> broad, sign-reversed result" at t = 2.06 with a clean placebo, and treated
> "departures are slow-diffusing good news" as a live idea deserving its own
> registration. At t = 1.05 there is no result to explain. The falsification of
> the original hypothesis stands — the effect is positive where I predicted
> negative — but the positive drift is not distinguishable from zero, and the
> follow-up hypothesis is no longer motivated by anything in this data.
>
> **What does not change.** The structural lesson is unaffected and if anything
> reinforced: rare disclosures (243 impairments, 74 auditor changes) cannot be
> tested in a 500-name universe, and the 8-K item-code family looks exhausted.



- **Campaign:** `governance-events-2026-08` — **Family:** `governance_events` (5 trials)
- **Outcome: 0 of 5 survived discovery.** Four of five were falsified on sign.

| # | Event | Events | BHAR +1..+61 | t | Placebo t | DSR |
|---|-------|-------:|-------------:|--:|----------:|----:|
| 1 | 5.02 officer/director change | 13,273 | **+0.274%** | 2.06 | 0.03 | 0.000 |
| 2 | 2.05 restructuring charge | 416 | **+1.373%** | 1.60 | 1.07 | 0.000 |
| 3 | 2.06 material impairment | 243 | −0.771% | 0.66 | 2.04 | 0.448 |
| 4 | 4.01 auditor change | 74 | +0.485% | 0.26 | 1.26 | 0.204 |
| 5 | 5.02 vs sector benchmark | 12,790 | +0.180% | 1.52 | 0.81 | 0.000 |

## Mechanism-level lessons

**The central hypothesis was falsified, not merely unsupported.** I predicted
that officer departures would be followed by underperformance — disruption,
loss of firm-specific human capital, a signal of problems the market cannot yet
see. The observed drift is *positive* (+0.274%, t = 2.06) with a clean placebo
(t = 0.03) and 557 distinct names. That is a real, broad, sign-reversed result
on the largest event sample in the campaign, and it survives the sector
benchmark (+0.180%). Pre-registration is what makes this a falsification rather
than a rebranded discovery about "management refresh creating value".

**The obvious next hypothesis is not licensed by this one.** "Departures are
good news that diffuses slowly" is a plausible reading — removing a
underperforming executive is a positive act, and the market may underreact
because the 8-K is terse and gives no guidance on the replacement. But that is
a *new* hypothesis. It must be registered in its own family, with its own
predicted sign, and clear the bar on evidence collected after the prediction.
Reading it off this table would be exactly the after-the-fact story-fitting
the ledger exists to prevent.

**Restructuring charges also ran the wrong way, and are untrustworthy anyway.**
Variant 2 shows +1.373% but with only 416 events, the top ten of which carry
14.6% of the gross effect, and halves that disagree violently (−0.24% then
+2.98%). Whatever that number is, it is not a stable property of restructuring
announcements.

**The rare-event variants failed on power, as expected, and that is the
structural lesson.** Impairments (243 events) and auditor changes (74) were the
most interesting hypotheses in the campaign — genuinely rare, genuinely hard to
parse, exactly where slow information diffusion should survive. They are also
the ones this universe cannot test: 500 large caps simply do not generate
enough of these disclosures. Variant 3's placebo (t = 2.04) is nearly as large
as its effect, and variant 4 fails concentration and breadth outright. **The
constraint is the universe, not the mechanism.** Testing rare-disclosure
hypotheses properly needs a broader cross-section, which is out of reach at $0.

**The 8-K family as a whole now looks exhausted.** Between this campaign and
`pead`, nine event-study hypotheses across six 8-K item types have been tested
against structured filing metadata. Nothing has survived. The consistent
pattern is that the *timing* is right — the placebos are clean, the entry rule
is externally validated — and the *content* is simply already priced.

## What not to do next

- Do not test further 8-K item types with structured metadata alone. Items
  1.01, 2.01, 7.01 and 8.01 remain untested, but there is no mechanism-level
  reason to expect them to behave differently from the six already tried, and
  each would raise the hurdle for the whole family.
- Do not pursue the departure reversal without fresh pre-registration.
- Any future event work on this data should read the *text* of the filing
  rather than its item code. Item codes are a taxonomy the market has fully
  internalised; the unstructured content is where anything left would be.
