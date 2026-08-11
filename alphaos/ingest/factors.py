"""Fama-French daily factors + momentum, from Ken French's data library.

Used to answer the question that kills most "discoveries": is this signal
actually new, or is it market/size/value/momentum exposure wearing a costume?
A spread that loses its t-stat once regressed on these four is a factor bet,
not an alpha.
"""
from __future__ import annotations

import io
import os
import zipfile

import polars as pl

BASE = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
FF3_URL = f"{BASE}/F-F_Research_Data_Factors_daily_CSV.zip"
MOM_URL = f"{BASE}/F-F_Momentum_Factor_daily_CSV.zip"


def _read_zip_csv(url: str) -> list[str]:
    from . import http
    r = http.get(url)
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        name = zf.namelist()[0]
        return zf.read(name).decode("latin-1").splitlines()


def _parse(lines: list[str], names: list[str]) -> pl.DataFrame:
    """French's CSVs carry a prose header and a trailing annual section; the
    daily rows are exactly the ones starting with an 8-digit date."""
    rows = []
    for line in lines:
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < len(names) + 1 or len(parts[0]) != 8 or not parts[0].isdigit():
            continue
        try:
            vals = [float(p) / 100.0 for p in parts[1:len(names) + 1]]
        except ValueError:
            continue
        rows.append([parts[0]] + vals)
    df = pl.DataFrame(rows, schema=["date_str"] + names, orient="row")
    return df.with_columns(
        pl.col("date_str").str.strptime(pl.Date, "%Y%m%d").alias("date")).drop("date_str")


def build(out_path: str = "data/factors.parquet") -> pl.DataFrame:
    ff3 = _parse(_read_zip_csv(FF3_URL), ["mkt_rf", "smb", "hml", "rf"])
    mom = _parse(_read_zip_csv(MOM_URL), ["mom"])
    df = ff3.join(mom, on="date", how="left").sort("date")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    df.write_parquet(out_path)
    print(f"factors: {df.height:,} days, {df['date'].min()} to {df['date'].max()}")
    return df


def load(path: str = "data/factors.parquet") -> pl.DataFrame:
    return pl.read_parquet(path)
