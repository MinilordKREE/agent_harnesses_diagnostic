"""Literal kinds and pinned identifiers shared by config and task code (no imports).

No reference source: written fresh for ahd (see docs/reuse/M1.md). The source-to-domain
mapping mirrors Evo-Bench ``data_construction/shared/v3_lib.py`` lines 50-78 (Apache-2.0)
as data, not code.
"""

from __future__ import annotations

from typing import Literal

type Domain = Literal["search", "office", "general"]
type Split = Literal["validation", "evaluation"]
type SourceBenchmark = Literal["browsecomp", "hle", "gdpval", "claw_eval", "apex"]

EVOBENCH_DATASET_ID = "RUC-AIBOX/Evo-Bench"
EVOBENCH_PINNED_REVISION = "46bf7acf5c76250541c44d3794c38bb2e5ecba35"

SPLITS: tuple[Split, ...] = ("validation", "evaluation")
DOMAINS: tuple[Domain, ...] = ("search", "office", "general")
SOURCES: tuple[SourceBenchmark, ...] = ("browsecomp", "hle", "gdpval", "claw_eval", "apex")
RUNNABLE_SOURCES: frozenset[str] = frozenset({"browsecomp", "hle", "gdpval", "claw_eval"})

SOURCE_BY_CANARY: dict[str, SourceBenchmark] = {
    "browsecomp": "browsecomp",
    "hle": "hle",
    "gdpval": "gdpval",
    "claw": "claw_eval",
    "apex": "apex",
}
DOMAIN_BY_SOURCE: dict[str, Domain] = {
    "browsecomp": "search",
    "hle": "search",
    "gdpval": "office",
    "apex": "office",
    "claw_eval": "general",
}
APEX_EXCLUSION_REASON = (
    "APEX-Agents tasks need a per-rollout E2B sandbox built from the evobench-apex-spec image "
    "(Archipelago + mercor/apex-agents) with in-sandbox grading; not runnable with our keys. "
    "See docs/reuse/evobench.md section g."
)
