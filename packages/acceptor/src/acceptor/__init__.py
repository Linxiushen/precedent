# Copyright 2026 The Precedent authors.
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License. You may obtain a copy of
# the License at http://www.apache.org/licenses/LICENSE-2.0
# SPDX-License-Identifier: Apache-2.0
"""acceptor -- the pure decision core of an acceptance layer for self-modifying agents.

Anytime-valid paired gates (testing by betting), horizon-free error-budget schedules,
acceptor-owned instance sampling, stability-gated per-task floors, mechanical hard
constraints, a hash-chained certificate ledger and an acceptor benchmark.  Stdlib only;
deterministic; every stateful object serialises to JSON so a decision can be paused and
resumed across days.

The alpha guarantee is a statement about a *protocol*, not just about arithmetic: the
acceptor must own instance selection and order (``instances.InstanceSampler`` +
``gate.bind_instances``), the wealth process may be stopped but never rewound
(``ledger.Ledger`` enforces this per candidate), rates need denominators
(``objectives.Constraint.support_metric``) and an unevaluated floor is ``INCOMPLETE``,
not a pass (``floor.FloorVerdict.verdict``).  ``python -m acceptor.bench --regime
adversarial`` measures each of those, defence off and on.

References: PACE (arXiv 2606.08106), SEA (arXiv 2607.00871), Ville (1939),
Howard, Ramdas, McAuliffe & Sekhon (Ann. Stat. 2021), Waudby-Smith & Ramdas (JRSS-B 2024),
Foster & Stine (JRSS-B 2008), PROCTOR / JIT-Agent / EvoHarnessBench design rules
(research/synthesis_v2_rsi.md §3).
"""

from .eprocess import BETTING_STRATEGIES, GateState, OrderViolation, PairedBinaryGate, PairedBoundedGate, load_gate
from .floor import FloorVerdict, ProtectedCorpus, TaskFloor, TransferReport, false_block_rate, transfer_report
from .instances import InstanceOrder, InstanceSampler, content_hash
from .ledger import (
    CandidateMonitor,
    Certificate,
    ConcurrentWrite,
    Event,
    FunnelSummary,
    Ledger,
    LedgerCorrupt,
    LedgerViolation,
    funnel,
    verify_chain,
)
from .objectives import (
    Constraint,
    ConstraintResult,
    HardConstraints,
    MultiObjective,
    default_constraints,
    pareto_front,
    pareto_tiebreak,
)
from .schedule import Z_CTHS, AlphaInvesting, SpendSchedule, cths_constant

__version__ = "0.2.0"

# The bench is imported lazily so that ``python -m acceptor.bench`` does not import the
# module twice (runpy warning) and so that importing the decision core stays cheap.
_LAZY_BENCH = {
    "AcceptorBench",
    "BenchConfig",
    "BenchResult",
    "PolicyStats",
    "AttackStats",
    "AdversarialResult",
    "run_bench",
    "run_adversarial",
}


# The artefact-level half of the bench (labelled streams over SKILL.md-shaped
# bundles) is likewise lazy: it carries a seed corpus and a pile of regexes that
# a caller who only wants the decision core should not pay for.
_LAZY_STREAMS = {
    "Acceptor",
    "Budget",
    "Bundle",
    "Candidate",
    "Seed",
    "ScoreCard",
    "StreamResult",
    "Verdict",
    "FAMILIES",
    "VARIANTS",
    "SEEDS",
    "EVALUATOR_SURFACE",
    "DEFAULT_LIFTS",
    "behaviour_identical",
    "behaviour_key",
    "make_candidate",
    "reference_acceptors",
    "score",
    "stream",
    "verify_label",
}


def __getattr__(name: str):
    if name in _LAZY_BENCH:
        from . import bench

        return getattr(bench, name)
    if name in _LAZY_STREAMS:
        from . import streams

        return getattr(streams, name)
    raise AttributeError(f"module 'acceptor' has no attribute {name!r}")


def __dir__() -> list[str]:
    # Without this the lazily-exported bench names are in __all__ but invisible to dir().
    return sorted(set(globals()) | _LAZY_BENCH | _LAZY_STREAMS)


#: Canonical names. Two aliases are kept for backwards compatibility and are *not* listed:
#: ``TaskFloor`` (= ``ProtectedCorpus``) and the gate property ``e_value`` (= ``wealth``).
#: ``funnel`` / ``verify_chain`` are exported as functions; ``Ledger`` has methods of the
#: same name that call them on its own records.
__all__ = [
    "__version__",
    # eprocess
    "GateState",
    "PairedBinaryGate",
    "PairedBoundedGate",
    "BETTING_STRATEGIES",
    "OrderViolation",
    "load_gate",
    # instances
    "InstanceSampler",
    "InstanceOrder",
    "content_hash",
    # schedule
    "SpendSchedule",
    "AlphaInvesting",
    "cths_constant",
    "Z_CTHS",
    # floor
    "ProtectedCorpus",
    "FloorVerdict",
    "TransferReport",
    "false_block_rate",
    "transfer_report",
    # objectives
    "Constraint",
    "ConstraintResult",
    "HardConstraints",
    "MultiObjective",
    "default_constraints",
    "pareto_tiebreak",
    "pareto_front",
    # ledger
    "Certificate",
    "Event",
    "Ledger",
    "FunnelSummary",
    "CandidateMonitor",
    "LedgerViolation",
    "LedgerCorrupt",
    "ConcurrentWrite",
    "funnel",
    "verify_chain",
    # bench
    "AcceptorBench",
    "BenchConfig",
    "BenchResult",
    "PolicyStats",
    "AttackStats",
    "AdversarialResult",
    "run_bench",
    "run_adversarial",
    # streams (labelled artefact-level streams; lazy)
    "Acceptor",
    "Budget",
    "Bundle",
    "Candidate",
    "Seed",
    "ScoreCard",
    "StreamResult",
    "Verdict",
    "FAMILIES",
    "VARIANTS",
    "SEEDS",
    "EVALUATOR_SURFACE",
    "DEFAULT_LIFTS",
    "behaviour_identical",
    "behaviour_key",
    "make_candidate",
    "reference_acceptors",
    "score",
    "stream",
    "verify_label",
]
