"""Frozen task splits for E0b (owner decision P2): per source, ``validation`` (all usable),
``eval_dev`` (24 from evaluation, seed 0, per-source stratified) and ``heldout`` (30 from the
remaining evaluation tasks, seed 0). Pairwise disjoint by construction and asserted.

No reference source: written fresh for ahd.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from ahd.core.config import StrictModel
from ahd.core.hashing import sha256_file
from ahd.core.io import atomic_write_text, read_json
from ahd.errors import ConfigError
from ahd.tasks.models import TaskSet
from ahd.tasks.sampling import sample_per_source

SPLITS_PATH = Path("experiments/splits_v1.json")


class SourceSplits(StrictModel):
    validation: tuple[str, ...]
    eval_dev: tuple[str, ...]
    heldout: tuple[str, ...]


class Splits(StrictModel):
    schema_version: int = 1
    seed: int
    eval_dev_per_source: int
    heldout_per_source: int
    sources: dict[str, SourceSplits]

    def mining_pool(self, source: str) -> tuple[str, ...]:
        s = self.sources[source]
        return tuple(sorted(set(s.validation) | set(s.eval_dev)))


def build_splits(
    validation: TaskSet,
    evaluation: TaskSet,
    *,
    sources: tuple[str, ...],
    eval_dev_per_source: int = 24,
    heldout_per_source: int = 30,
    seed: int = 0,
) -> Splits:
    evaluation = evaluation.select(sources=list(sources))
    dev = sample_per_source(evaluation, per_source=eval_dev_per_source, seed=seed)
    dev_ids = {t.id for t in dev.tasks}
    remaining = evaluation.model_copy(
        update={"tasks": tuple(t for t in evaluation.tasks if t.id not in dev_ids)}
    )
    held = sample_per_source(remaining, per_source=heldout_per_source, seed=seed)
    out: dict[str, SourceSplits] = {}
    for source in sources:
        val = tuple(
            sorted(
                t.id for t in validation.tasks if t.source_benchmark == source and not t.excluded
            )
        )
        d = tuple(sorted(t.id for t in dev.tasks if t.source_benchmark == source))
        h = tuple(sorted(t.id for t in held.tasks if t.source_benchmark == source))
        assert_disjoint(source, val, d, h)
        out[source] = SourceSplits(validation=val, eval_dev=d, heldout=h)
    return Splits(
        seed=seed,
        eval_dev_per_source=eval_dev_per_source,
        heldout_per_source=heldout_per_source,
        sources=out,
    )


def assert_disjoint(source: str, *groups: tuple[str, ...]) -> None:
    seen: set[str] = set()
    for group in groups:
        overlap = seen & set(group)
        if overlap:
            raise ConfigError(f"splits for {source} overlap: {sorted(overlap)[:5]}")
        seen |= set(group)


def freeze(splits: Splits, path: Path = SPLITS_PATH) -> tuple[Path, str]:
    """Write once; afterwards the file is the truth and must equal the recomputed splits."""
    if path.is_file():
        try:
            existing = Splits.model_validate(read_json(path))
        except ValidationError as exc:
            raise ConfigError(f"corrupt {path}: {exc}") from exc
        if existing != splits:
            raise ConfigError(f"{path} differs from the recomputed splits; not overwriting")
    else:
        atomic_write_text(path, json.dumps(splits.model_dump(mode="json"), indent=2) + "\n")
    return path, sha256_file(path)


def load_splits(path: Path = SPLITS_PATH) -> Splits:
    try:
        return Splits.model_validate(read_json(path))
    except ValidationError as exc:
        raise ConfigError(f"corrupt {path}: {exc}") from exc
