"""The experiment specification language.

This module exists to keep the language model out of the numbers. Claude
proposes a hypothesis as declarative JSON; this validator either accepts it or
rejects it; harness.py -- hand-written, hand-audited, never generated -- is the
only thing that computes a return. A generated backtest script could hide a
one-line lookahead bug that no amount of robustness testing would catch,
because a leaked signal passes subperiod replication and factor neutralisation
just as happily as a real one.

A second benefit: because every experiment runs identical machinery, counting
trials is meaningful. Two specs differing by one number are two comparable
trials, so the deflated Sharpe denominator means what it claims.

Unknown keys are rejected rather than ignored. A typo'd "neutralise" that
silently disabled neutralisation would be indistinguishable from a discovery.
"""
from __future__ import annotations

from typing import Any

# Rebalance interval -> trading days. The horizon must equal the interval so
# holding periods never overlap: overlapping returns are autocorrelated, and
# every Sharpe, t-stat and DSR downstream assumes independent periods. A
# hypothesis needing an overlapping design is a deliberate harness extension,
# not a spec the validator should quietly permit.
INTERVALS = {"monthly": 21, "quarterly": 63, "semiannual": 126}

FUNDAMENTAL_FEATURES = {
    "Revenues", "CostOfRevenue", "InventoryNet", "NetIncomeLoss",
    "OperatingIncomeLoss", "Assets", "StockholdersEquity", "Cash",
    "LongTermDebt", "AccountsReceivable", "OperatingCashFlow", "SharesOutstanding",
}
MARKET_FEATURES = {"mom_12_1", "beta_252", "vol_63", "adv_21", "entry_close", "mktcap"}
FEATURES = FUNDAMENTAL_FEATURES | MARKET_FEATURES

UNARY_OPS = {
    "level",       # q<lag> as-is
    "yoy_growth",  # (q0 - q4) / |q4|      year-on-year, same fiscal quarter
    "qoq_growth",  # (q0 - q1) / |q1|
    "accel",       # yoy_growth(t) - yoy_growth(t-1): change in the growth rate
    "log",         # sign-safe log of a positive quantity
    "neg", "abs",
    "zscore_xs",   # cross-sectional standardisation within a rebalance
    "rank_xs",     # cross-sectional rank mapped to [0, 1]
}
BINARY_OPS = {"add", "sub", "mul", "div"}

NEUTRALIZERS = {"sector", "size", "beta"}

TRIGGERS = {
    "8k_2.02": "8-K item 2.02 -- results of operations (the earnings release)",
    "8k_1.01": "8-K item 1.01 -- entry into a material agreement",
    "8k_2.01": "8-K item 2.01 -- completion of an acquisition",
    "8k_2.05": "8-K item 2.05 -- costs associated with exit or disposal (restructuring)",
    "8k_2.06": "8-K item 2.06 -- material impairment",
    "8k_5.02": "8-K item 5.02 -- officer or director change",
    "8k_4.01": "8-K item 4.01 -- change of accountant",
    "10q_filed": "10-Q filing accepted",
    "10k_filed": "10-K filing accepted",
    "insider_cluster_buy": "3+ distinct insiders bought on the open market within "
                           "30 days (dated by the filing that completes the cluster)",
    "insider_buy_officer": "an officer or director bought on the open market",
}
BENCHMARKS = {"spy", "sector"}

_CS_KEYS = {"type", "signal", "universe", "rebalance", "horizon_days", "lag_days",
            "neutralize", "cost_bps", "min_names", "winsorize", "predicted_sign",
            "start", "end"}
_EV_KEYS = {"type", "event", "window", "benchmark", "min_events", "predicted_sign",
            "universe", "lag_days", "start", "end", "condition"}

# Conditioning splits events on something observable at the event. reaction_1d
# is the abnormal return over the first full session after the filing became
# public -- known by the close of day +1, so any drift window must start at
# day +2 or later or the condition and the outcome share a bar.
CONDITIONS = {"reaction_1d"}


class SpecError(ValueError):
    """Raised for any spec that is not exactly well-formed."""


def _require(cond: bool, msg: str):
    if not cond:
        raise SpecError(msg)


def _check_expr(node: Any, path: str = "signal") -> None:
    _require(isinstance(node, dict), f"{path}: expression must be an object")
    if "const" in node:
        _require(set(node) == {"const"}, f"{path}: const node takes no other keys")
        _require(isinstance(node["const"], (int, float)), f"{path}: const must be numeric")
        return
    if "feature" in node:
        _require(set(node) <= {"feature", "lag"}, f"{path}: leaf takes only feature, lag")
        _require(node["feature"] in FEATURES,
                 f"{path}: unknown feature {node['feature']!r}; known: {sorted(FEATURES)}")
        lag = node.get("lag", 0)
        _require(isinstance(lag, int) and 0 <= lag <= 7, f"{path}: lag must be 0..7")
        if node["feature"] in MARKET_FEATURES:
            _require(lag == 0, f"{path}: market features have no quarterly lags")
        return
    _require("op" in node, f"{path}: expression needs one of const, feature, op")
    op = node["op"]
    if op in UNARY_OPS:
        _require(set(node) == {"op", "x"}, f"{path}: unary {op} takes exactly x")
        _check_expr(node["x"], f"{path}.x")
    elif op in BINARY_OPS:
        _require(set(node) == {"op", "a", "b"}, f"{path}: binary {op} takes exactly a, b")
        _check_expr(node["a"], f"{path}.a")
        _check_expr(node["b"], f"{path}.b")
    else:
        raise SpecError(f"{path}: unknown op {op!r}; "
                        f"known: {sorted(UNARY_OPS | BINARY_OPS)}")


def _check_common(spec: dict):
    _require(spec.get("predicted_sign") in (1, -1),
             "predicted_sign must be +1 or -1 and is binding on the verdict")
    _require(spec.get("universe", "sp500_pit") == "sp500_pit",
             "universe must be sp500_pit (the only point-in-time universe built)")
    lag = spec.get("lag_days", 0)
    _require(isinstance(lag, int) and 0 <= lag <= 21, "lag_days must be 0..21")


def validate(spec: dict) -> dict:
    """Return a normalised copy, or raise SpecError. No partial acceptance."""
    _require(isinstance(spec, dict), "spec must be an object")
    kind = spec.get("type")
    _require(kind in ("cross_sectional", "event_study"),
             "type must be cross_sectional or event_study")

    if kind == "cross_sectional":
        unknown = set(spec) - _CS_KEYS
        _require(not unknown, f"unknown keys: {sorted(unknown)}")
        _check_common(spec)
        _require("signal" in spec, "cross_sectional needs a signal")
        _check_expr(spec["signal"])

        rb = spec.get("rebalance", "monthly")
        _require(rb in INTERVALS, f"rebalance must be one of {sorted(INTERVALS)}")
        horizon = spec.get("horizon_days", INTERVALS[rb])
        _require(horizon == INTERVALS[rb],
                 f"horizon_days must equal the {rb} interval ({INTERVALS[rb]}) so holding "
                 f"periods do not overlap; pick a different rebalance for a longer horizon")

        cost = spec.get("cost_bps", 10)
        _require(isinstance(cost, (int, float)) and cost >= 0, "cost_bps must be >= 0")
        neut = spec.get("neutralize", [])
        _require(isinstance(neut, list) and set(neut) <= NEUTRALIZERS,
                 f"neutralize must be a subset of {sorted(NEUTRALIZERS)}")
        wins = spec.get("winsorize", 0.01)
        _require(isinstance(wins, (int, float)) and 0 <= wins < 0.2,
                 "winsorize must be in [0, 0.2)")
        mn = spec.get("min_names", 50)
        _require(isinstance(mn, int) and mn >= 20,
                 "min_names must be >= 20; thinner cross-sections are noise")

        out = dict(spec)
        out.update({"rebalance": rb, "horizon_days": horizon, "cost_bps": cost,
                    "neutralize": sorted(neut), "winsorize": wins, "min_names": mn,
                    "lag_days": spec.get("lag_days", 0), "universe": "sp500_pit"})
        return out

    unknown = set(spec) - _EV_KEYS
    _require(not unknown, f"unknown keys: {sorted(unknown)}")
    _check_common(spec)
    ev = spec.get("event")
    _require(isinstance(ev, dict) and set(ev) <= {"trigger"}, "event takes only trigger")
    _require(ev.get("trigger") in TRIGGERS,
             f"unknown trigger; known: {sorted(TRIGGERS)}")
    win = spec.get("window", [1, 60])
    _require(isinstance(win, list) and len(win) == 2 and all(isinstance(v, int) for v in win),
             "window must be [start_day, end_day] in trading days from entry")
    _require(1 <= win[0] < win[1] <= 252,
             "window must satisfy 1 <= start < end <= 252; a window starting at 0 "
             "would include the return into the entry close (the untradable "
             "announcement reaction) and would collapse the BHAR benchmark leg "
             "to zero, silently reporting a raw buy-and-hold return")
    bm = spec.get("benchmark", "spy")
    _require(bm in BENCHMARKS, f"benchmark must be one of {sorted(BENCHMARKS)}")
    me = spec.get("min_events", 100)
    _require(isinstance(me, int) and me >= 30, "min_events must be >= 30")

    cond = spec.get("condition")
    if cond is not None:
        _require(isinstance(cond, dict) and set(cond) <= {"on", "select", "fraction"},
                 "condition takes only on, select, fraction")
        _require(cond.get("on") in CONDITIONS, f"condition.on must be in {sorted(CONDITIONS)}")
        _require(cond.get("select") in ("top", "bottom"),
                 "condition.select must be top or bottom")
        frac = cond.get("fraction", 0.3)
        _require(isinstance(frac, (int, float)) and 0.05 <= frac <= 0.5,
                 "condition.fraction must be in [0.05, 0.5]")
        _require(win[0] >= 2,
                 "a conditioned study must start at day +2 or later: reaction_1d is "
                 "only known at the close of day +1, and sharing that bar with the "
                 "outcome window would measure the condition as if it were drift")
        cond = {"on": cond["on"], "select": cond["select"], "fraction": frac}

    out = dict(spec)
    out.update({"window": win, "benchmark": bm, "min_events": me, "condition": cond,
                "lag_days": spec.get("lag_days", 0), "universe": "sp500_pit"})
    return out


def describe(spec: dict) -> str:
    """Plain-English rendering, for post-mortems and the conviction sheet."""
    if spec["type"] == "event_study":
        w = spec.get("window", [1, 60])
        c = spec.get("condition")
        cs = (f", restricted to the {c['select']} {c['fraction']:.0%} by {c['on']}"
              if c else "")
        return (f"event study: {TRIGGERS[spec['event']['trigger']]}, "
                f"days +{w[0]}..+{w[1]} vs {spec.get('benchmark', 'spy')}{cs}, "
                f"predicted {'positive' if spec['predicted_sign'] > 0 else 'negative'} drift")
    return (f"cross-sectional: {_expr_str(spec['signal'])}, "
            f"{spec.get('rebalance', 'monthly')} rebalance, "
            f"{spec.get('horizon_days')}d hold, "
            f"neutralised on {spec.get('neutralize') or ['nothing']}, "
            f"{spec.get('cost_bps', 10)}bps costs, "
            f"predicted {'high-minus-low positive' if spec['predicted_sign'] > 0 else 'high-minus-low negative'}")


def _expr_str(node: dict) -> str:
    if "const" in node:
        return str(node["const"])
    if "feature" in node:
        lag = node.get("lag", 0)
        return node["feature"] + (f"[t-{lag}q]" if lag else "")
    op = node["op"]
    if op in UNARY_OPS:
        return f"{op}({_expr_str(node['x'])})"
    sym = {"add": "+", "sub": "-", "mul": "*", "div": "/"}[op]
    return f"({_expr_str(node['a'])} {sym} {_expr_str(node['b'])})"
