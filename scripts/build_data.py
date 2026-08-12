"""Build every dataset from scratch and stamp a data_version.

Order matters: the universe defines which CIKs and tickers everything else
fetches. Roughly 30-60 minutes cold, most of it the 1.3 GB SEC bulk download
and the per-CIK filing history.

    python scripts/build_data.py [--skip-prices] [--skip-events]
"""
from __future__ import annotations

import pathlib
import sys

import polars as pl

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from alphaos import panel, paths  # noqa: E402
from alphaos.ingest import (edgar, events, factors, insiders, prices,  # noqa: E402
                            universe)


def main(argv: list[str]) -> int:
    skip_prices = "--skip-prices" in argv
    skip_events = "--skip-events" in argv

    print("== 1/6 universe (point-in-time S&P 500 membership) ==")
    uni = universe.build()

    print("\n== 2/6 prices ==")
    if not skip_prices:
        prices.build(uni["ticker"].unique().to_list())
    px = pl.read_parquet(paths.PRICES)

    print("\n== 3/6 resolving renamed tickers ==")
    spans = {}
    for key, g in px.group_by("ticker"):
        spans[key[0] if isinstance(key, tuple) else key] = (g["date"].min(), g["date"].max())
    uni, accepted = universe.resolve_price_tickers(uni, spans)
    uni.write_parquet(paths.UNIVERSE)
    print(f"  {len(accepted)} renames resolved via CIK continuity")

    print("\n== 4/6 fundamentals (SEC XBRL, point-in-time) ==")
    ciks = uni.filter(pl.col("cik").is_not_null())["cik"].unique().to_list()
    edgar.build(ciks)

    print("\n== 5/7 filing events + Fama-French factors ==")
    if not skip_events:
        events.build(ciks)
    factors.build()

    print("\n== 6/7 insider transactions (Form 4) ==")
    if not skip_events:
        # Acceptance timestamps first: the quarterly data sets carry a filing
        # date but no clock, and an event study needs the clock.
        events.build(ciks, out_path=paths.FORM4_META, forms=events.FORM4)
        insiders.build(ciks)

    print("\n== 7/7 panel + embargo split ==")
    meta = panel.build()
    print(f"\ndata_version = {meta['data_version']}")
    print("Now run: python -c \"from alphaos import controls; "
          "print(controls.report(controls.run_all()))\"")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
