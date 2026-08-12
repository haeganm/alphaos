"""Price coverage of the point-in-time universe, by year and by index.

This is the gate on whether a universe can be tested honestly. Membership comes
from Wikipedia revision history, which records who was in an index and under
what symbol; prices come from Yahoo, which frequently has nothing at all for a
company that delisted. The difference between those two is the survivorship
hole, and it is invisible unless measured -- a name with no price data simply
never enters a cross-section, so the backtest reports a clean result on whoever
happens to have survived.

Thresholds are pre-committed in the Stage 4 plan, before the numbers were
known:

    sp400    >= 80% in every year from 2012
    combined >= 85% in every year from 2014

Failing them is a legitimate result. A mid-cap panel with a large survivorship
hole manufactures findings, and "this cannot be tested honestly at $0" is worth
more than a caveated campaign.

    python scripts/coverage_report.py
"""
from __future__ import annotations

import pathlib
import sys
from datetime import date

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from alphaos import paths  # noqa: E402
from alphaos.ingest import universe as U  # noqa: E402

SP400_FLOOR, SP400_FROM = 0.80, 2012
COMBINED_FLOOR, COMBINED_FROM = 0.85, 2014


def coverage_by_year(uni: pl.DataFrame, priced: set[str]) -> dict:
    indices = (uni["index"].unique().to_list() if "index" in uni.columns else [None])
    out: dict = {}
    for year in range(2010, date.today().year + 1):
        d = date(year, 6, 30)
        row = {}
        for idx in indices + ["combined"]:
            members = U.members_on(uni, d, None if idx == "combined" else idx)
            if not members:
                continue
            sub = uni.filter(pl.col("ticker").is_in(members))
            pt = {(r["price_ticker"] if "price_ticker" in sub.columns and r["price_ticker"]
                   else r["ticker"]) for r in sub.select(
                      [c for c in ("ticker", "price_ticker") if c in sub.columns]
                  ).unique().rows(named=True)}
            n_ok = sum(1 for t in pt if t in priced)
            row[idx] = (n_ok, len(members), n_ok / max(len(members), 1))
        out[year] = row
    return out


def main() -> int:
    uni = pl.read_parquet(paths.UNIVERSE)
    px = pl.read_parquet(paths.PRICES)
    priced = set(px["ticker"].unique().to_list())

    table = coverage_by_year(uni, priced)
    cols = sorted({k for row in table.values() for k in row})
    print("price coverage of point-in-time membership")
    print("year  " + "  ".join(f"{c:>18}" for c in cols))
    for year, row in table.items():
        cells = []
        for c in cols:
            if c in row:
                ok, n, frac = row[c]
                cells.append(f"{ok:4d}/{n:4d} ({frac:5.1%})")
            else:
                cells.append(" " * 18)
        print(f"{year}  " + "  ".join(cells))

    failures = []
    for year, row in table.items():
        if "sp400" in row and year >= SP400_FROM and row["sp400"][2] < SP400_FLOOR:
            failures.append(f"sp400 {year} {row['sp400'][2]:.1%} < {SP400_FLOOR:.0%}")
        if "combined" in row and year >= COMBINED_FROM and row["combined"][2] < COMBINED_FLOOR:
            failures.append(
                f"combined {year} {row['combined'][2]:.1%} < {COMBINED_FLOOR:.0%}")

    print()
    if failures:
        print("GATE FAILED -- do not run campaigns on this universe:")
        for f in failures:
            print(f"  {f}")
        print("\nThe pre-committed rule says stop and write this up, not proceed "
              "with a caveat.")
        return 1
    print("GATE PASSED -- coverage clears the pre-committed thresholds.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
