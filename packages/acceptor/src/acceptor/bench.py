# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""AcceptorBench: a simulation harness for acceptance policies (PACE's two regimes).

Regimes (PACE, arXiv 2606.08106, Sec. 5)
-----------------------------------------
``planted``    one real improvement with true lift ``lift`` hidden among ``k_null`` noisy
               null candidates (and optionally ``n_harmful`` candidates with lift ``-harm``);
               every candidate is evaluated on ``n`` paired instances against the incumbent.
``stochastic`` no real gain: ``t_candidates`` null candidates in sequence.
``adversarial`` the proposer games the *protocol* rather than the statistics -- see below.

Both of the first two regimes assume an **honest i.i.d. proposer**: candidates are
independent Bernoulli draws, fed in generation order, one gate each, state never
persisted.  "Gates <= alpha" measured there is therefore a statement about acceptor-
sampled, fresh, in-order pairs with unmodified state, and nothing else.  The
``adversarial`` regime measures what happens when that is not true, and what the
hardening in ``instances``/``ledger``/``schedule`` buys:

``reorder``     feed the same n pairs wins-first and stop at the first crossing.
                Guarded by ``gate.bind_instances(order)``: an out-of-order instance is an
                ``OrderViolation``.
``rewind``      snapshot gate JSON before each batch, restore whenever wealth fell.
                Guarded by ``ledger.CandidateMonitor``: n_pairs/evidence_hash may not go
                backwards.
``select:K``    privately evaluate K null candidates on one fixed dev set, submit the
                luckiest.  Guarded by drawing fresh instances per candidate.
``repropose:K`` resubmit the same null candidate K times under fresh ids.  Guarded by the
                CTHS spend schedule plus content-hash dedup.
``targeted:m``  hand-pick m instances the incumbent is known to fail.  Guarded by
                ``min_informative`` (a decision may not rest on m discordant pairs).

``guarded`` columns are the false-commit rate with the defence on; ``naive`` without.

Outcome model: on instance i the incumbent passes with probability ``p_inc`` and the
candidate with ``p_inc + lift`` (clipped to [0, 1]), independently -- the same model as
research/tools/gate_power_sim.py, whose numbers this module reproduces (greedy 44-46 %
false commits at zero lift; e-process false commits <= alpha).  All policies see the
*same* simulated pairs (common random numbers), so differences are policy differences.

Policies
--------
``greedy``        commit iff the candidate's mean on the n instances exceeds the incumbent's
                  (what most production loops do; PACE measured 30-42 % false commits).
``mcnemar``       fixed-n exact one-sided McNemar test (binomial on discordant pairs).
``gate_fixed``    PairedBinaryGate, lambda = 0.5 (PACE default), sequential, budget n.
``gate_mixture``  PairedBinaryGate, mixture over (0.2, 0.4, 0.6, 0.8).
``gate_ons``      PairedBinaryGate, ONS adaptive fraction.
``gate_agrapa``   PairedBinaryGate, aGRAPA adaptive fraction.

Reported per policy: commits per run, false-commit rate (fraction of *null* candidates
committed), harmful-commit rate (fraction of harmful candidates committed),
missed-improvement rate (fraction of real improvements not committed), evaluations
per decision (sequential gates stop early), run-level FWER (runs with >= 1 false or
harmful commit) and commit precision.

CLI::

    python -m acceptor.bench --regime planted --n 40 --lift 0.2 --seeds 200
    python -m acceptor.bench --regime stochastic --n 40 --t 10 --seeds 200 --schedule
    python -m acceptor.bench --regime adversarial --n 40 --seeds 200
    python -m acceptor.bench --regime adversarial --attack reorder --seeds 500
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Sequence

from .eprocess import GateState, OrderViolation, PairedBinaryGate
from .instances import content_hash
from .ledger import CandidateMonitor, Certificate, LedgerViolation
from .schedule import SpendSchedule

__all__ = [
    "BenchConfig",
    "PolicyStats",
    "BenchResult",
    "AcceptorBench",
    "AttackStats",
    "AdversarialResult",
    "run_bench",
    "run_adversarial",
    "DEFAULT_POLICIES",
    "ATTACKS",
    "REGIMES",
    "main",
]

DEFAULT_POLICIES = ("greedy", "mcnemar", "gate_fixed", "gate_mixture", "gate_ons", "gate_agrapa")
REGIMES = ("planted", "stochastic", "adversarial")
ATTACKS = ("reorder", "rewind", "select", "repropose", "targeted")
Pair = tuple[int, int]


@dataclass
class BenchConfig:
    regime: str = "planted"
    n: int = 40
    lift: float = 0.2
    k_null: int = 9
    n_harmful: int = 0
    harm: float = 0.2
    t_candidates: int = 10
    p_inc: float = 0.5
    alpha: float = 0.05
    seeds: int = 200
    seed0: int = 0
    policies: Sequence[str] = DEFAULT_POLICIES
    schedule: bool = False  # per-candidate alpha from SpendSchedule(delta0=alpha)
    harm_alpha: float | None = None
    # ---- adversarial regime ----
    attacks: Sequence[str] = ATTACKS
    k_select: int = 20  # candidates privately screened on one dev set (select)
    k_repropose: int = 20  # re-submissions of the same candidate (repropose)
    targeted_m: int = 8  # hand-picked discordant instances (targeted)
    batch: int = 5  # evaluations per snapshot (rewind)
    rewind_evals: int = 200  # total evaluation budget the rewinding adversary may burn
    min_informative: int = 20  # the guarded gate's floor on discordant pairs
    gate_betting: Sequence[str] = ("fixed", "mixture")

    def __post_init__(self) -> None:
        if self.regime not in REGIMES:
            raise ValueError(f"regime must be one of {REGIMES}")
        if not (0.0 <= self.p_inc <= 1.0):
            raise ValueError("p_inc must lie in [0, 1]")
        for p in self.policies:
            if p not in DEFAULT_POLICIES:
                raise ValueError(f"unknown policy {p!r}; choose from {DEFAULT_POLICIES}")
        for a in self.attacks:
            if a not in ATTACKS:
                raise ValueError(f"unknown attack {a!r}; choose from {ATTACKS}")


@dataclass
class PolicyStats:
    name: str
    runs: int = 0
    decisions: int = 0
    commits: int = 0
    n_null: int = 0
    null_commits: int = 0
    n_harmful: int = 0
    harmful_commits: int = 0
    n_true: int = 0
    true_commits: int = 0
    evals_total: int = 0
    runs_with_false_commit: int = 0
    stop_pairs: list[int] = field(default_factory=list, repr=False)

    @property
    def commits_per_run(self) -> float:
        return self.commits / self.runs if self.runs else float("nan")

    @property
    def false_commit_rate(self) -> float:
        return self.null_commits / self.n_null if self.n_null else float("nan")

    @property
    def harmful_commit_rate(self) -> float:
        return self.harmful_commits / self.n_harmful if self.n_harmful else float("nan")

    @property
    def missed_improvement_rate(self) -> float:
        return 1.0 - self.true_commits / self.n_true if self.n_true else float("nan")

    @property
    def power(self) -> float:
        return self.true_commits / self.n_true if self.n_true else float("nan")

    @property
    def evals_per_decision(self) -> float:
        return self.evals_total / self.decisions if self.decisions else float("nan")

    @property
    def fwer(self) -> float:
        return self.runs_with_false_commit / self.runs if self.runs else float("nan")

    @property
    def precision(self) -> float:
        return self.true_commits / self.commits if self.commits else float("nan")

    @property
    def median_stop(self) -> float | None:
        if not self.stop_pairs:
            return None
        s = sorted(self.stop_pairs)
        return float(s[len(s) // 2])

    def as_dict(self) -> dict[str, Any]:
        """JSON-friendly summary; undefined rates (no candidates of that kind) are ``None``."""

        def _f(x: float | None) -> float | None:
            return None if x is None or (isinstance(x, float) and math.isnan(x)) else x

        d = {k: v for k, v in asdict(self).items() if k != "stop_pairs"}
        d.update(
            commits_per_run=_f(self.commits_per_run),
            false_commit_rate=_f(self.false_commit_rate),
            harmful_commit_rate=_f(self.harmful_commit_rate),
            missed_improvement_rate=_f(self.missed_improvement_rate),
            power=_f(self.power),
            evals_per_decision=_f(self.evals_per_decision),
            fwer=_f(self.fwer),
            precision=_f(self.precision),
            median_stop=self.median_stop,
        )
        return d


@dataclass
class BenchResult:
    config: BenchConfig
    policies: dict[str, PolicyStats]

    def as_dict(self) -> dict[str, Any]:
        return {"config": asdict(self.config), "policies": {k: v.as_dict() for k, v in self.policies.items()}}

    def table(self) -> str:
        return format_table(self)


# ---- policies -----------------------------------------------------------------------
def _binomial_tail_half(n: int, k: int) -> float:
    """P(Bin(n, 1/2) >= k)."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(math.comb(n, j) for j in range(k, n + 1)) / 2.0**n


def policy_greedy(pairs: Sequence[Pair], alpha: float, cfg: BenchConfig) -> tuple[bool, int]:
    inc = sum(p[0] for p in pairs)
    cand = sum(p[1] for p in pairs)
    return cand > inc, len(pairs)


def policy_mcnemar(pairs: Sequence[Pair], alpha: float, cfg: BenchConfig) -> tuple[bool, int]:
    wins = sum(1 for i, c in pairs if c > i)
    losses = sum(1 for i, c in pairs if i > c)
    p = _binomial_tail_half(wins + losses, wins) if wins + losses else 1.0
    return p <= alpha, len(pairs)


def _gate_policy(betting: str) -> Callable[[Sequence[Pair], float, BenchConfig], tuple[bool, int]]:
    def run(pairs: Sequence[Pair], alpha: float, cfg: BenchConfig) -> tuple[bool, int]:
        g = PairedBinaryGate(alpha=alpha, betting=betting, max_pairs=len(pairs), harm_alpha=cfg.harm_alpha)
        for k, pr in enumerate(pairs, 1):
            if g.update(pr).terminal:
                return g.state is GateState.ACCEPT, k
        return g.state is GateState.ACCEPT, len(pairs)

    return run


POLICIES: dict[str, Callable[[Sequence[Pair], float, BenchConfig], tuple[bool, int]]] = {
    "greedy": policy_greedy,
    "mcnemar": policy_mcnemar,
    "gate_fixed": _gate_policy("fixed"),
    "gate_mixture": _gate_policy("mixture"),
    "gate_ons": _gate_policy("ons"),
    "gate_agrapa": _gate_policy("agrapa"),
}


# ---- simulation ---------------------------------------------------------------------
def simulate_pairs(rng: random.Random, n: int, p_inc: float, lift: float) -> list[Pair]:
    p_cand = min(1.0, max(0.0, p_inc + lift))
    return [(int(rng.random() < p_inc), int(rng.random() < p_cand)) for _ in range(n)]


class AcceptorBench:
    """Deterministic given ``config.seed0``; each run uses ``random.Random(seed0 + run)``."""

    def __init__(self, config: BenchConfig | None = None, **kw: Any) -> None:
        self.config = config if config is not None else BenchConfig(**kw)

    def candidates_for_run(self, run: int) -> list[tuple[float, list[Pair]]]:
        cfg = self.config
        rng = random.Random(cfg.seed0 + run)
        lifts: list[float]
        if cfg.regime == "planted":
            lifts = [0.0] * cfg.k_null + [cfg.lift] + [-abs(cfg.harm)] * cfg.n_harmful
            rng.shuffle(lifts)
        else:
            lifts = [0.0] * cfg.t_candidates
        return [(lift, simulate_pairs(rng, cfg.n, cfg.p_inc, lift)) for lift in lifts]

    def run(self) -> BenchResult:
        cfg = self.config
        stats = {p: PolicyStats(p) for p in cfg.policies}
        for run in range(cfg.seeds):
            cands = self.candidates_for_run(run)
            sched = SpendSchedule(delta0=cfg.alpha) if cfg.schedule else None
            alphas = [sched.draw() if sched else cfg.alpha for _ in cands]
            for name in cfg.policies:
                st = stats[name]
                st.runs += 1
                fn = POLICIES[name]
                false_in_run = False
                for (lift, pairs), a in zip(cands, alphas):
                    commit, evals = fn(pairs, a, cfg)
                    st.decisions += 1
                    st.evals_total += evals
                    st.commits += int(commit)
                    if lift > 0:
                        st.n_true += 1
                        st.true_commits += int(commit)
                        if commit:
                            st.stop_pairs.append(evals)
                    elif lift < 0:
                        st.n_harmful += 1
                        st.harmful_commits += int(commit)
                        false_in_run |= commit
                    else:
                        st.n_null += 1
                        st.null_commits += int(commit)
                        false_in_run |= commit
                st.runs_with_false_commit += int(false_in_run)
        return BenchResult(cfg, stats)


def run_bench(config: BenchConfig | None = None, **kw: Any) -> BenchResult:
    cfg = config if config is not None else BenchConfig(**kw)
    if cfg.regime == "adversarial":
        raise ValueError("use run_adversarial() for the adversarial regime")
    return AcceptorBench(cfg).run()


# ---- adversarial regime --------------------------------------------------------------
@dataclass
class AttackStats:
    """False-commit rate of one attack with the defence off (naive) and on (guarded)."""

    attack: str
    betting: str
    seeds: int = 0
    naive_commits: int = 0
    guarded_commits: int = 0
    guarded_refusals: int = 0
    honest_commits: int = 0
    note: str = ""

    @property
    def naive_rate(self) -> float:
        return self.naive_commits / self.seeds if self.seeds else float("nan")

    @property
    def guarded_rate(self) -> float:
        return self.guarded_commits / self.seeds if self.seeds else float("nan")

    @property
    def honest_rate(self) -> float:
        return self.honest_commits / self.seeds if self.seeds else float("nan")

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d.update(naive_rate=self.naive_rate, guarded_rate=self.guarded_rate, honest_rate=self.honest_rate)
        return d


@dataclass
class AdversarialResult:
    config: BenchConfig
    attacks: list[AttackStats]

    def as_dict(self) -> dict[str, Any]:
        return {"config": asdict(self.config), "attacks": [a.as_dict() for a in self.attacks]}

    def table(self) -> str:
        return format_attack_table(self)


def _null_pairs(rng: random.Random, n: int, p_inc: float) -> list[Pair]:
    """A zero-lift candidate: both arms Bernoulli(p_inc), so q = 1/2 exactly."""
    return simulate_pairs(rng, n, p_inc, 0.0)


def _feed_until_terminal(gate: PairedBinaryGate, pairs: Sequence[Pair], ids: Sequence[str] | None = None) -> bool:
    for k, pr in enumerate(pairs):
        st = gate.update(pr, instance_id=None if ids is None else ids[k])
        if st.terminal:
            break
    return gate.state is GateState.ACCEPT


def _attack_reorder(cfg: BenchConfig, betting: str, seed: int, st: AttackStats) -> None:
    rng = random.Random(seed)
    pairs = _null_pairs(rng, cfg.n, cfg.p_inc)
    ids = [f"i{j}" for j in range(cfg.n)]
    st.honest_commits += _feed_until_terminal(PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n), pairs)
    # The proposer knows every outcome and re-orders: wins, then ties, then losses.
    order = sorted(range(cfg.n), key=lambda j: (-(pairs[j][1] - pairs[j][0]),))
    naive = PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n)
    st.naive_commits += _feed_until_terminal(naive, [pairs[j] for j in order])
    # Guarded: the acceptor committed the order before any outcome existed.
    guarded = PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n).bind_instances(ids)
    try:
        st.guarded_commits += _feed_until_terminal(guarded, [pairs[j] for j in order], [ids[j] for j in order])
    except OrderViolation:
        st.guarded_refusals += 1


def _attack_rewind(cfg: BenchConfig, betting: str, seed: int, st: AttackStats) -> None:
    """Snapshot before every batch, restore whenever the batch lost money.

    The gate itself is unbounded (rewinding resets ``n_pairs`` anyway); what is bounded
    is the adversary's *evaluation* budget, ``rewind_evals``.
    """
    rng = random.Random(seed)
    pairs = _null_pairs(rng, cfg.rewind_evals, cfg.p_inc)
    st.honest_commits += _feed_until_terminal(
        PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.rewind_evals), pairs
    )
    for guarded in (False, True):
        gate = PairedBinaryGate(alpha=cfg.alpha, betting=betting)
        monitor, rnd, refused, committed = CandidateMonitor(), 0, False, False
        snapshot, snap_wealth = gate.to_json(), gate.wealth
        for start in range(0, cfg.rewind_evals, cfg.batch):
            gate.update_many(pairs[start : start + cfg.batch])
            rnd += 1
            if guarded:
                # Every batch is certified; from_gate marks the certification point, so a
                # restored snapshot no longer continues the certified evidence.
                try:
                    monitor.accept(
                        Certificate.from_gate(
                            "c", rnd, gate, alpha_spent=cfg.alpha, cumulative_alpha=cfg.alpha, metrics={}
                        )
                    )
                except LedgerViolation:
                    refused = True
                    break
            if gate.state is GateState.ACCEPT:
                committed = True
                break
            if gate.wealth < snap_wealth:  # a losing batch never happened
                gate = PairedBinaryGate.from_json(snapshot)
            else:
                snapshot, snap_wealth = gate.to_json(), gate.wealth
        if guarded:
            st.guarded_commits += committed
            st.guarded_refusals += refused
        else:
            st.naive_commits += committed


def _attack_select(cfg: BenchConfig, betting: str, seed: int, st: AttackStats) -> None:
    rng = random.Random(seed)
    # One fixed dev set, K private candidates: the proposer submits the luckiest.
    dev = [[(int(rng.random() < cfg.p_inc), int(rng.random() < cfg.p_inc)) for _ in range(cfg.n)] for _ in range(cfg.k_select)]
    incumbent = [p[0] for p in dev[0]]
    dev = [[(incumbent[j], c[j][1]) for j in range(cfg.n)] for c in dev]  # same instances, same incumbent

    def peak(pairs: Sequence[Pair]) -> float:
        g = PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n)
        g.update_many(pairs)
        return g.max_wealth

    best = max(dev, key=peak)
    st.honest_commits += _feed_until_terminal(PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n), dev[0])
    st.naive_commits += _feed_until_terminal(PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n), best)
    # Guarded: the acceptor draws instances the proposer has not screened on.
    fresh = _null_pairs(rng, cfg.n, cfg.p_inc)
    st.guarded_commits += _feed_until_terminal(PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n), fresh)


def _attack_repropose(cfg: BenchConfig, betting: str, seed: int, st: AttackStats) -> None:
    rng = random.Random(seed)
    ch = content_hash(f"candidate-{seed}")
    sched = SpendSchedule(delta0=cfg.alpha)
    monitor = CandidateMonitor()
    naive = guarded = False
    refused = False
    for k in range(cfg.k_repropose):
        pairs = _null_pairs(rng, cfg.n, cfg.p_inc)
        if k == 0:
            st.honest_commits += _feed_until_terminal(
                PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n), pairs
            )
        naive |= _feed_until_terminal(PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n), pairs)
        if refused:
            continue
        # Guarded: identical content under a fresh id is refused by the ledger; a genuinely
        # new candidate would pay the next (much smaller) schedule level.
        a = sched.reserve()
        cert = Certificate(
            candidate_id=f"cand-{k}",
            round=1,
            algorithm=f"PairedBinaryGate/{betting}",
            decision="HOLD",
            alpha_spent=a,
            cumulative_alpha=sched.spent() + a,
            content_hash=ch,
            schedule_round=sched.rounds_spent + 1,
        )
        try:
            monitor.validate(cert)
        except LedgerViolation:
            sched.release()
            refused = True
            continue
        sched.commit(ch)
        monitor.record(cert.as_dict())
        guarded |= _feed_until_terminal(PairedBinaryGate(alpha=a, betting=betting, max_pairs=cfg.n), pairs)
    st.naive_commits += naive
    st.guarded_commits += guarded
    st.guarded_refusals += refused


def _attack_targeted(cfg: BenchConfig, betting: str, seed: int, st: AttackStats) -> None:
    m = cfg.targeted_m
    ties = max(0, cfg.n - m)
    # m instances the proposer knows the incumbent fails and the candidate passes,
    # padded with ties: zero population lift, a deterministic ACCEPT.
    pairs: list[Pair] = [(1, 1)] * (ties // 2) + [(0, 0)] * (ties - ties // 2) + [(0, 1)] * m
    st.honest_commits += _feed_until_terminal(
        PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n),
        _null_pairs(random.Random(seed), cfg.n, cfg.p_inc),
    )
    st.naive_commits += _feed_until_terminal(PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n), pairs)
    guarded = PairedBinaryGate(alpha=cfg.alpha, betting=betting, max_pairs=cfg.n, min_informative=cfg.min_informative)
    st.guarded_commits += _feed_until_terminal(guarded, pairs)


_ATTACK_FNS = {
    "reorder": _attack_reorder,
    "rewind": _attack_rewind,
    "select": _attack_select,
    "repropose": _attack_repropose,
    "targeted": _attack_targeted,
}
_ATTACK_NOTES = {
    "reorder": "guarded by bind_instances (committed order)",
    "rewind": "guarded by CandidateMonitor (n_pairs/evidence monotone)",
    "select": "guarded by fresh instances per candidate",
    "repropose": "guarded by content-hash dedup + CTHS schedule",
    "targeted": "guarded by min_informative",
}


def run_adversarial(config: BenchConfig | None = None, **kw: Any) -> AdversarialResult:
    """Measure each protocol attack with the defence off and on (null candidates only)."""
    cfg = config if config is not None else BenchConfig(regime="adversarial", **kw)
    out: list[AttackStats] = []
    for attack in cfg.attacks:
        for betting in cfg.gate_betting:
            st = AttackStats(attack=attack, betting=betting, note=_ATTACK_NOTES[attack])
            fn = _ATTACK_FNS[attack]
            for s in range(cfg.seeds):
                st.seeds += 1
                fn(cfg, betting, cfg.seed0 + 7919 * s, st)
            out.append(st)
    return AdversarialResult(cfg, out)


# ---- reporting ----------------------------------------------------------------------
def _fmt(x: float | None, pct: bool = True) -> str:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "  -  "
    return f"{100 * x:5.1f}%" if pct else f"{x:6.1f}"


def format_table(res: BenchResult) -> str:
    cfg = res.config
    head = (
        f"AcceptorBench regime={cfg.regime} n={cfg.n} p_inc={cfg.p_inc} alpha={cfg.alpha} seeds={cfg.seeds}"
        + (f" lift={cfg.lift} k_null={cfg.k_null} n_harmful={cfg.n_harmful} harm={cfg.harm}" if cfg.regime == "planted" else f" t={cfg.t_candidates}")
        + (" schedule=CTHS" if cfg.schedule else "")
        + (f" harm_alpha={cfg.harm_alpha}" if cfg.harm_alpha else "")
    )
    cols = ["policy", "commits/run", "false-commit", "harmful-commit", "missed-improv", "power", "evals/decision", "FWER(run)", "precision", "median stop"]
    rows = [cols]
    for name, s in res.policies.items():
        rows.append(
            [
                name,
                f"{s.commits_per_run:6.2f}",
                _fmt(s.false_commit_rate),
                _fmt(s.harmful_commit_rate),
                _fmt(s.missed_improvement_rate),
                _fmt(s.power),
                _fmt(s.evals_per_decision, pct=False),
                _fmt(s.fwer),
                _fmt(s.precision),
                "-" if s.median_stop is None else f"{s.median_stop:.0f}",
            ]
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(cols))]
    lines = [head, ""]
    for j, r in enumerate(rows):
        lines.append("  ".join(c.rjust(w) if j else c.ljust(w) for c, w in zip(r, widths)))
        if j == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def format_attack_table(res: AdversarialResult) -> str:
    cfg = res.config
    head = (
        f"AcceptorBench regime=adversarial n={cfg.n} p_inc={cfg.p_inc} alpha={cfg.alpha} seeds={cfg.seeds} "
        f"(null candidates only; select K={cfg.k_select}, repropose K={cfg.k_repropose}, "
        f"targeted m={cfg.targeted_m}, min_informative={cfg.min_informative})"
    )
    cols = ["attack", "betting", "honest", "naive", "guarded", "refused", "defence"]
    rows = [cols]
    for a in res.attacks:
        rows.append(
            [
                a.attack,
                a.betting,
                _fmt(a.honest_rate),
                _fmt(a.naive_rate),
                _fmt(a.guarded_rate),
                f"{a.guarded_refusals:5d}",
                a.note,
            ]
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(cols))]
    lines = [head, ""]
    for j, r in enumerate(rows):
        lines.append("  ".join(c.rjust(w) if j and i < 6 else c.ljust(w) for i, (c, w) in enumerate(zip(r, widths))))
        if j == 0:
            lines.append("  ".join("-" * w for w in widths))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m acceptor.bench", description=__doc__.split("\n\n")[0])
    ap.add_argument("--regime", choices=REGIMES, default="planted")
    ap.add_argument("--n", type=int, default=40, help="paired evaluations available per candidate")
    ap.add_argument("--lift", type=float, default=0.2, help="true lift of the planted improvement")
    ap.add_argument("--k", "--k-null", dest="k_null", type=int, default=9, help="null candidates per run (planted)")
    ap.add_argument("--n-harmful", type=int, default=0, help="harmful candidates per run (planted)")
    ap.add_argument("--harm", type=float, default=0.2, help="magnitude of harmful candidates' negative lift")
    ap.add_argument("--t", "--t-candidates", dest="t_candidates", type=int, default=10, help="null candidates per run (stochastic)")
    ap.add_argument("--p-inc", type=float, default=0.5)
    ap.add_argument("--alpha", type=float, default=0.05)
    ap.add_argument("--seeds", type=int, default=200)
    ap.add_argument("--seed0", type=int, default=0)
    ap.add_argument("--policies", default=",".join(DEFAULT_POLICIES))
    ap.add_argument("--schedule", action="store_true", help="per-candidate alpha from the CTHS spend schedule")
    ap.add_argument("--harm-alpha", type=float, default=None, help="enable the harm martingale in gate policies")
    ap.add_argument(
        "--attack",
        default=",".join(ATTACKS),
        help=f"adversarial regime: comma-separated subset of {','.join(ATTACKS)} "
        f"(K/m come from --k-select, --k-repropose, --targeted-m)",
    )
    ap.add_argument("--k-select", type=int, default=20, help="candidates privately screened on one dev set")
    ap.add_argument("--k-repropose", type=int, default=20, help="re-submissions of the same candidate")
    ap.add_argument("--targeted-m", type=int, default=8, help="hand-picked discordant instances")
    ap.add_argument("--min-informative", type=int, default=20, help="guarded gate's floor on discordant pairs")
    ap.add_argument("--rewind-evals", type=int, default=200, help="evaluation budget of the rewinding adversary")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    a = ap.parse_args(argv)
    try:
        cfg = BenchConfig(
            regime=a.regime,
            n=a.n,
            lift=a.lift,
            k_null=a.k_null,
            n_harmful=a.n_harmful,
            harm=a.harm,
            t_candidates=a.t_candidates,
            p_inc=a.p_inc,
            alpha=a.alpha,
            seeds=a.seeds,
            seed0=a.seed0,
            policies=tuple(p.strip() for p in a.policies.split(",") if p.strip()),
            schedule=a.schedule,
            harm_alpha=a.harm_alpha,
            attacks=tuple(x.strip() for x in a.attack.split(",") if x.strip()),
            k_select=a.k_select,
            k_repropose=a.k_repropose,
            targeted_m=a.targeted_m,
            min_informative=a.min_informative,
            rewind_evals=a.rewind_evals,
        )
    except ValueError as exc:  # a bad --policies / --attack is a usage error, not a traceback
        ap.error(str(exc))
    res = run_adversarial(cfg) if cfg.regime == "adversarial" else run_bench(cfg)
    print(json.dumps(res.as_dict(), indent=2) if a.json else res.table())
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
