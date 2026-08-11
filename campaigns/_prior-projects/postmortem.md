# Prior work: mechanisms already ruled out (2025-2026)

Seeded from four completed projects that predate AlphaOS. They were run with
less rigour than this lab enforces — no pre-registration, no trial ledger — so
treat them as strong priors rather than registered results. Their value is
mechanism-level: each one closes a direction, and rediscovering any of them
would be a waste of a campaign.

The through-line is worth stating plainly: **four independent efforts, four
honest no-edge verdicts.** That is the base rate this lab is calibrated
against. A campaign that finds nothing is behaving normally.

---

## TechnicalEngine — intraday technical signals on S&P 500 + NDX

**Verdict: NO EDGE.** Scanned 506 tickers, ~99 buy alerts a day, live for
months.

**Mechanism ruled out:** price-and-volume patterns on intraday bars for large
caps. Exits, sub-slices and threshold tuning were each tested separately and
each failed. The conclusion was not "the parameters were wrong" but "there is
nothing in this data at this frequency for these names".

**Do not retry:** any variant of intraday TA on liquid US large caps.

**Two implementation gotchas that carry over:** Yahoo labels bars by interval
*start*, not end. And `min_periods < window` in a rolling calculation silently
breaks parity between live and backtest paths — the backtest sees a value the
live system does not.

---

## BTCTA — Bitcoin technical analysis with calibrated probabilities

**Verdict: NO EDGE**, by design honestly measured. LightGBM over engineered TA
features, purged walk-forward, isolated calibration slices, blocked bootstrap
confidence intervals.

**Mechanism ruled out:** supervised learning over technical features for BTC
direction at hourly horizons.

**What survived and is now used here:** the methodology. AlphaOS lifted the
moving-block bootstrap (overlapping-horizon labels are autocorrelated; iid
intervals overstated power ~24x) and the no-edge-by-default verdict rule,
including returning NO EDGE when the interval is undefined. That default is
the single most useful thing that project produced.

---

## MYOWNTA — confluence-scored regime-aware swing system (Pine Script)

**Verdict: does not generalise.** A composite score across many technical
inputs looked strong in-sample and failed on holdout beds.

**Mechanism ruled out:** additive confluence scoring. Combining many weak
technical signals into one score did not produce a robust signal; it produced
a well-fitted one. This is directly relevant here — it is the reason the
operational-deterioration post-mortem refuses to build a composite out of five
individually failed variants.

**Also established:** equity and ETF *shorts* lost money across three
independent symbol sets. Short-side hypotheses in this lab should carry an
explicit reason why they escape that finding.

---

## Density Desk — crypto, weather, and index strategies on Kalshi

**Verdict: measured and retired**, three strategies, shut down 2025-07 with a
written shutdown note rather than left running.

**Mechanism ruled out:** the specific lead-lag relationships tested. The
decisive test was preserved as a ~20-minute rerunnable script, which is the
right pattern: a dead strategy should leave behind the cheapest experiment
that would revive it if the world changed.

**Reopen condition:** structural market change only.

---

## What these imply for AlphaOS

1. **Technical/price-pattern mechanisms on liquid instruments are exhausted**
   across three independent attempts. Fundamental, filing-based, and
   event-based mechanisms are the unexplored ground — which is why this lab is
   built on point-in-time SEC data rather than price history.
2. **Composite scores hide overfitting.** Test components independently and
   pre-register the combination as its own trial if you build one.
3. **Short-side equity strategies have failed here before.**
4. **A retired strategy should leave a rerunnable decisive test.** In AlphaOS
   that role is played by the registry plus `campaign.json`: any dead campaign
   can be re-run verbatim against a new `data_version`.
