"""Per-source pass rates and a labelled macro. No 2:2:1 Overall Score.

No reference source: written fresh for ahd (see docs/reuse/M1.md). Evo-Bench's
``evaluation/metrics.py`` (Apache-2.0) computes the paper's weighted Overall; with APEX
excluded and a different judge that number is not reproducible here, so it is not computed.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from ahd.core.config import StrictModel
from ahd.errors import ConfigError
from ahd.tasks.models import Score, Task

MACRO_DISCLAIMER = (
    "weighted mean of per-source pass rates over the sources present; not comparable to the "
    "Evo-Bench paper's 2:2:1 Overall Score (APEX excluded, judge differs)"
)


class SourceMetrics(StrictModel):
    source: str
    n: int
    passed: int
    pass_rate: float
    mean_value: float
    task_failures: int


class MetricsReport(StrictModel):
    n_scored: int
    per_source: dict[str, SourceMetrics]
    macro: float | None
    macro_weights: dict[str, float]
    macro_label: str


def summarize_scores(
    tasks: Sequence[Task],
    scores: Mapping[str, Score],
    *,
    weights: Mapping[str, float] | None = None,
) -> MetricsReport:
    """One ``Score`` per task id. Tasks without a score are not counted (report them elsewhere)."""
    by_id = {t.id: t for t in tasks}
    unknown = sorted(set(scores) - set(by_id))
    if unknown:
        raise ConfigError(f"scores for unknown task ids: {unknown[:5]}")
    groups: dict[str, list[Score]] = defaultdict(list)
    for task_id, score in scores.items():
        groups[by_id[task_id].source_benchmark].append(score)
    per_source: dict[str, SourceMetrics] = {}
    for source in sorted(groups):
        items = groups[source]
        per_source[source] = SourceMetrics(
            source=source,
            n=len(items),
            passed=sum(1 for s in items if s.passed),
            pass_rate=sum(1 for s in items if s.passed) / len(items),
            mean_value=sum(s.value for s in items) / len(items),
            task_failures=sum(1 for s in items if s.task_failure is not None),
        )
    if not per_source:
        return MetricsReport(
            n_scored=0, per_source={}, macro=None, macro_weights={}, macro_label=MACRO_DISCLAIMER
        )
    resolved = (
        {s: 1.0 for s in per_source}
        if weights is None
        else {s: float(weights[s]) for s in per_source if s in weights}
    )
    if weights is not None and set(resolved) != set(per_source):
        missing = sorted(set(per_source) - set(resolved))
        raise ConfigError(f"macro weights missing for sources: {missing}")
    total = sum(resolved.values())
    macro = sum(per_source[s].pass_rate * w for s, w in resolved.items()) / total
    label = f"{MACRO_DISCLAIMER}; weights {dict(sorted(resolved.items()))}"
    return MetricsReport(
        n_scored=len(scores),
        per_source=per_source,
        macro=macro,
        macro_weights=resolved,
        macro_label=label,
    )
