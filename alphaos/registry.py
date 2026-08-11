"""The experiment ledger. Append-only by construction, not by good intentions.

Every hypothesis is written down BEFORE it is tested: prose mechanism,
predicted sign, and the condition that would falsify it, hashed and stamped.
Nothing in this module can rewrite a pre-registered field, and no result can
overwrite an earlier result -- a rerun is a new trial, which is the whole
point. The N in "Sharpe 1.5 after N experiments" comes from here.

Trial counting: N is COUNT(DISTINCT spec_hash) within a family. Re-running an
identical spec does not widen the search, so it does not inflate N; every
distinct variant does, including the ones abandoned before a verdict.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import uuid
from datetime import datetime, timezone

import duckdb

from . import paths

DEFAULT_DB = str(paths.REGISTRY_DB)

# status lifecycle
REGISTERED = "REGISTERED"
RUN = "RUN"
ABANDONED = "ABANDONED"

# verdicts
FAILED = "FAILED"
SURVIVED_DISCOVERY = "SURVIVED_DISCOVERY"
VAULT = "VAULT"
DEMOTED = "DEMOTED"
VERDICTS = {FAILED, SURVIVED_DISCOVERY, VAULT, DEMOTED}

# Fields fixed at registration. Nothing in this module updates them; the test
# suite asserts it stays that way.
FROZEN_FIELDS = ("campaign_id", "family_id", "hypothesis", "predicted_sign",
                 "falsification", "spec_json", "spec_hash", "data_version",
                 "registered_at", "parent_id", "trial_kind")

_SCHEMA = """
create table if not exists experiments (
    id              varchar primary key,
    campaign_id     varchar not null,
    family_id       varchar not null,
    hypothesis      varchar not null,
    predicted_sign  integer not null,
    falsification   varchar not null,
    spec_json       varchar not null,
    spec_hash       varchar not null,
    data_version    varchar not null,
    registered_at   varchar not null,
    parent_id       varchar,
    trial_kind      varchar not null,
    status          varchar not null,
    ran_at          varchar,
    results_json    varchar,
    sharpe          double,
    dsr             double,
    verdict         varchar,
    verdict_reason  varchar,
    decided_at      varchar,
    holdout_touches integer not null default 0,
    forward_json    varchar
);
create table if not exists registry_events (
    seq        bigint,
    at_utc     varchar not null,
    exp_id     varchar not null,
    event      varchar not null,
    payload    varchar
);
create sequence if not exists registry_event_seq start 1;
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(obj) -> str:
    """Stable serialization -- key order must not change a spec's identity."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(canonical_json(spec).encode("utf-8")).hexdigest()


class Registry:
    def __init__(self, path: str = DEFAULT_DB):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self.path = path
        self.con = duckdb.connect(path)
        self.con.execute(_SCHEMA)

    def close(self):
        self.con.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- audit ------------------------------------------------------------
    def _event(self, exp_id: str, event: str, payload=None):
        self.con.execute(
            "insert into registry_events values (nextval('registry_event_seq'), ?, ?, ?, ?)",
            [utcnow(), exp_id, event, canonical_json(payload) if payload is not None else None])

    # -- write path -------------------------------------------------------
    def register(self, *, campaign_id: str, family_id: str, hypothesis: str,
                 predicted_sign: int, falsification: str, spec: dict,
                 data_version: str, parent_id: str | None = None,
                 trial_kind: str = "primary") -> str:
        """Pre-register a hypothesis. Must be called before the harness runs.

        predicted_sign is +1 or -1 and is binding: a result with the opposite
        sign is a failed hypothesis, however large its t-stat.
        """
        if predicted_sign not in (1, -1):
            raise ValueError("predicted_sign must be +1 or -1")
        for name, val in (("hypothesis", hypothesis), ("falsification", falsification)):
            if not val or not val.strip():
                raise ValueError(f"{name} must be non-empty -- pre-registration is the point")
        sh = spec_hash(spec)
        at = utcnow()
        # Row key only; spec_hash is the reproducible identity. The uuid keeps
        # two genuinely separate trials of the same spec in the same second apart.
        exp_id = "exp_" + hashlib.sha256(
            f"{campaign_id}|{sh}|{at}|{parent_id}|{uuid.uuid4().hex}".encode()).hexdigest()[:12]
        self.con.execute(
            """insert into experiments (id, campaign_id, family_id, hypothesis,
                   predicted_sign, falsification, spec_json, spec_hash, data_version,
                   registered_at, parent_id, trial_kind, status, holdout_touches)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            [exp_id, campaign_id, family_id, hypothesis, predicted_sign, falsification,
             canonical_json(spec), sh, data_version, at, parent_id, trial_kind, REGISTERED])
        self._event(exp_id, "register", {"family": family_id, "spec_hash": sh,
                                         "data_version": data_version})
        return exp_id

    def record_result(self, exp_id: str, results: dict, *, sharpe: float | None = None,
                      dsr: float | None = None):
        """Attach harness output. Refuses to overwrite -- rerun means re-register."""
        row = self.get(exp_id)
        if row is None:
            raise KeyError(exp_id)
        if row["results_json"] is not None:
            raise ValueError(
                f"{exp_id} already has a result; a rerun is a new trial, register it")
        self.con.execute(
            "update experiments set status=?, ran_at=?, results_json=?, sharpe=?, dsr=? "
            "where id=?",
            [RUN, utcnow(), canonical_json(results), sharpe, dsr, exp_id])
        self._event(exp_id, "result", {"sharpe": sharpe, "dsr": dsr})

    def record_verdict(self, exp_id: str, verdict: str, reason: str,
                       dsr: float | None = None):
        """Verdicts may change over time (VAULT -> DEMOTED); every change is logged.

        dsr is set here rather than at record_result because it is only known
        once the whole family has run -- deflation depends on how many trials
        the winner was selected from.
        """
        if verdict not in VERDICTS:
            raise ValueError(f"unknown verdict {verdict!r}")
        if self.get(exp_id) is None:
            raise KeyError(exp_id)
        self.con.execute(
            "update experiments set verdict=?, verdict_reason=?, decided_at=? where id=?",
            [verdict, reason, utcnow(), exp_id])
        if dsr is not None and math.isfinite(dsr):
            self.con.execute("update experiments set dsr=? where id=?", [dsr, exp_id])
        self._event(exp_id, "verdict", {"verdict": verdict, "reason": reason,
                                        "dsr": dsr})

    def abandon(self, exp_id: str, reason: str) -> bool:
        """An abandoned trial still counts toward N. That is why it is recorded.

        Only logs when a row actually changed -- otherwise the audit trail
        recorded abandonments that never happened.
        """
        row = self.get(exp_id)
        if row is None or row["results_json"] is not None:
            return False
        self.con.execute("update experiments set status=? where id=?", [ABANDONED, exp_id])
        self._event(exp_id, "abandon", {"reason": reason})
        return True

    def record_holdout_touch(self, exp_id: str) -> int:
        self.con.execute(
            "update experiments set holdout_touches = holdout_touches + 1 where id=?", [exp_id])
        n = self.con.execute("select holdout_touches from experiments where id=?",
                             [exp_id]).fetchone()[0]
        self._event(exp_id, "holdout_touch", {"touches": n})
        return int(n)

    def record_forward(self, exp_id: str, forward: dict):
        """Forward-grading result, refreshed nightly. The only stat that cannot leak."""
        self.con.execute("update experiments set forward_json=? where id=?",
                         [canonical_json(forward), exp_id])
        self._event(exp_id, "forward", {"as_of": forward.get("as_of")})

    # -- read path --------------------------------------------------------
    def get(self, exp_id: str) -> dict | None:
        cur = self.con.execute("select * from experiments where id=?", [exp_id])
        row = cur.fetchone()
        if row is None:
            return None
        return dict(zip([d[0] for d in cur.description], row))

    def family_trial_count(self, family_id: str) -> int:
        """N for the DSR: distinct search attempts in this family.

        Robustness reruns (trial_kind='skeptic') are excluded. The quantity the
        DSR needs is how many candidates the reported winner was selected from,
        and a skeptic rerun is not a candidate -- it can only kill its parent,
        never be promoted in its place. Counting them would penalise thorough
        testing, which is backwards. Genuine variants stay in: if a failed
        skeptic pass prompts a tweaked spec, that tweak is registered as its
        own trial and does widen the search.
        """
        return int(self.con.execute(
            "select count(distinct spec_hash) from experiments "
            "where family_id=? and trial_kind <> 'skeptic'",
            [family_id]).fetchone()[0])

    def family_sharpe_variance(self, family_id: str) -> float:
        """Var of trial Sharpes in the family -- the spread DSR deflates against.

        Excludes skeptic reruns, matching family_trial_count exactly. The two
        halves of the DSR previously disagreed about what a trial is: reruns
        were kept out of N but left in the variance, and since the skeptic
        writes a Sharpe for every cost/placebo/subperiod rerun, deliberately
        constructed noise dominated the pool. Measured on the live ledger that
        inflated the variance by 52% (0.0117 vs 0.0077), raising the hurdle for
        every genuine trial in the family.
        """
        row = self.con.execute(
            "select var_samp(sharpe) from experiments "
            "where family_id=? and trial_kind <> 'skeptic' "
            "and sharpe is not null and isfinite(sharpe)",
            [family_id]).fetchone()
        return float(row[0]) if row and row[0] is not None else float("nan")

    def family_holdout_touches(self, family_id: str) -> int:
        row = self.con.execute(
            "select coalesce(sum(holdout_touches),0) from experiments where family_id=?",
            [family_id]).fetchone()
        return int(row[0])

    def by_verdict(self, verdict: str) -> list[dict]:
        cur = self.con.execute("select * from experiments where verdict=? order by decided_at",
                               [verdict])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]

    def summary(self) -> dict:
        rows = self.con.execute(
            "select coalesce(verdict,status), count(*) from experiments group by 1").fetchall()
        return {k: int(v) for k, v in rows}
