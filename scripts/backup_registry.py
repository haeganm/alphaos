"""Export the experiment ledger to committed CSV.

Every parquet under data/ is one `build_data.py` away from being rebuilt. The
registry is not. It holds the pre-registration timestamps, the distinct-spec
counts that feed the DSR, and the embargo touch counters -- and data/ is
gitignored, so a single `rm -rf data` erases the only irreplaceable thing here.

Losing it is worse than losing data. A registry restored from nothing reports
zero holdout touches, so a family that has already spent its three embargo
queries silently gets three more. The append-only guarantees in registry.py are
enforced against the code; nothing enforces them against deletion.

CSV rather than a database copy on purpose: it diffs, so the trial history is
reviewable in git rather than an opaque blob.

    python scripts/backup_registry.py
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from alphaos import paths, registry as R  # noqa: E402

TABLES = ("experiments", "registry_events")


def export(reg: R.Registry | None = None) -> str:
    own = reg is None
    reg = reg or R.Registry()
    try:
        out = paths.REGISTRY_EXPORT
        out.mkdir(parents=True, exist_ok=True)
        for table in TABLES:
            dest = out / f"{table}.csv"
            reg.con.execute(
                f"copy (select * from {table} order by 1) to '{dest.as_posix()}' "
                f"(header, delimiter ',')")
        n = reg.con.execute("select count(*) from experiments").fetchone()[0]
        (out / "README.md").write_text(
            "# Registry export\n\n"
            "Plain-text mirror of `data/alphaos.duckdb`, refreshed nightly and "
            "committed so the trial ledger survives loss of `data/`.\n\n"
            f"- experiments: {n}\n"
            "- Restore is manual and deliberate: the ledger is append-only, so "
            "re-importing should be a considered act, not a script anyone can "
            "run by accident.\n",
            encoding="utf-8")
        return str(out)
    finally:
        if own:
            reg.close()


if __name__ == "__main__":
    print(f"exported to {export()}")
