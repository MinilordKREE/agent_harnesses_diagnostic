"""Evo-Bench loader: canonical suites JSON from the Hugging Face snapshot cache.

No reference source: written fresh for ahd (see docs/reuse/M1.md). Uses Evo-Bench's own
``evobench.evaluation.tasks.load_suite`` (Apache-2.0, imported from the submodule) so that
``_asset_files_abs`` and the prompt contract are exactly what its scorer expects.

The ``datasets`` viewer rows (``viewer/*.jsonl``) store nested fields as JSON strings and are
never read here; ``suites/evobench_<split>.json`` is byte-identical to the repo's
``benchmark/suites/`` (verified 2026-09-04, see docs/reuse/evobench.md).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from ahd.core.hashing import JsonValue, to_json_value
from ahd.errors import ConfigError, InfraError
from ahd.tasks.kinds import (
    APEX_EXCLUSION_REASON,
    DOMAIN_BY_SOURCE,
    EVOBENCH_DATASET_ID,
    EVOBENCH_PINNED_REVISION,
    RUNNABLE_SOURCES,
    SOURCE_BY_CANARY,
    SPLITS,
    SourceBenchmark,
    Split,
)
from ahd.tasks.models import JUDGE_SCORER_TYPES, EvaluatorSpec, Task, TaskResources, TaskSet

logger = logging.getLogger(__name__)

SUITE_FILES: dict[str, str] = {
    "validation": "suites/evobench_validation.json",
    "evaluation": "suites/evobench_evaluation.json",
}


def cached_snapshot_dir(dataset_id: str, revision: str) -> Path | None:
    """The local snapshot directory if the dataset revision is in the HF cache, else ``None``."""
    from huggingface_hub import snapshot_download
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        return Path(
            snapshot_download(
                dataset_id, repo_type="dataset", revision=revision, local_files_only=True
            )
        )
    except (LocalEntryNotFoundError, FileNotFoundError, OSError):
        return None


def fetch_snapshot(dataset_id: str, revision: str, *, token: SecretStr | None) -> Path:
    """Download (or refresh) the dataset revision into the HF cache. Network."""
    from huggingface_hub import snapshot_download

    try:
        return Path(
            snapshot_download(
                dataset_id,
                repo_type="dataset",
                revision=revision,
                token=token.get_secret_value() if token else None,
            )
        )
    except Exception as exc:  # network / auth / not-found are all infra here
        raise InfraError(
            f"could not download {dataset_id}@{revision[:12]}: {type(exc).__name__}: {exc}",
            kind="dataset_download",
        ) from exc


def _source_of(record: dict[str, Any]) -> SourceBenchmark:
    canary = (record.get("metadata") or {}).get("canary")
    if canary in SOURCE_BY_CANARY:
        return SOURCE_BY_CANARY[str(canary)]
    prefix = str(record.get("id", "")).split("-", 1)[0]
    by_prefix: dict[str, SourceBenchmark] = {
        "bc": "browsecomp",
        "hle": "hle",
        "gdpval": "gdpval",
        "claw": "claw_eval",
        "apex": "apex",
    }
    if prefix in by_prefix:
        return by_prefix[prefix]
    raise ConfigError(f"task {record.get('id')!r}: cannot determine source benchmark")


def _json_dict(value: object, *, where: str) -> dict[str, JsonValue]:
    converted = to_json_value(value)
    if not isinstance(converted, dict):
        raise ConfigError(f"{where}: expected a JSON object")
    return converted


class EvoBenchLoader:
    """Loads a split from the cached snapshot; never writes to it, never mutates records."""

    def __init__(
        self,
        *,
        dataset_id: str = EVOBENCH_DATASET_ID,
        revision: str = EVOBENCH_PINNED_REVISION,
        snapshot_dir: Path | None = None,
    ) -> None:
        self.dataset_id = dataset_id
        self.revision = revision
        self._snapshot_dir = snapshot_dir

    @property
    def snapshot_dir(self) -> Path:
        if self._snapshot_dir is not None:
            return self._snapshot_dir
        found = cached_snapshot_dir(self.dataset_id, self.revision)
        if found is None:
            raise InfraError(
                f"dataset {self.dataset_id}@{self.revision[:12]} is not in the Hugging Face "
                "cache; run `ahd tasks fetch` (needs HF_TOKEN in .env)",
                kind="missing_dataset",
            )
        return found

    def suite_path(self, split: str) -> Path:
        if split not in SPLITS:
            raise ConfigError(f"unknown split {split!r}; expected one of {SPLITS}")
        return self.snapshot_dir / SUITE_FILES[split]

    def load(self, split: Split) -> TaskSet:
        from evobench.evaluation.tasks import load_suite

        path = self.suite_path(split)
        if not path.is_file():
            raise InfraError(f"suite file missing: {path}", kind="missing_file")
        suite: dict[str, Any] = load_suite(path)
        records = suite.get(split)
        if not isinstance(records, list) or not records:
            raise ConfigError(f"suite {path} has no non-empty {split!r} list")
        assets_dir = (path.parent / str(suite.get("assets_dir", ""))).resolve()
        tasks = tuple(
            self._to_task(record, split=split, assets_dir=assets_dir) for record in records
        )
        ids = [t.id for t in tasks]
        if len(set(ids)) != len(ids):
            raise ConfigError(f"suite {path} has duplicate task ids")
        logger.info(
            "loaded evobench split",
            extra={"split": split, "n": len(tasks), "revision": self.revision[:12]},
        )
        return TaskSet(
            dataset_id=self.dataset_id,
            revision=self.revision,
            split=split,
            suite_name=str(suite.get("name", path.stem)),
            tasks=tasks,
        )

    def _to_task(self, record: dict[str, Any], *, split: Split, assets_dir: Path) -> Task:
        task_id = str(record["id"])
        source = _source_of(record)
        domain = DOMAIN_BY_SOURCE[source]
        if record.get("domain") != domain:
            raise ConfigError(
                f"task {task_id}: released domain {record.get('domain')!r} disagrees with "
                f"source {source!r} -> {domain!r}"
            )
        scorer = record.get("scorer")
        if not isinstance(scorer, dict) or "type" not in scorer:
            raise ConfigError(f"task {task_id}: missing scorer block")
        scorer_type = str(scorer["type"])
        asset_files = {str(k): str(v) for k, v in (record.get("asset_files") or {}).items()}
        excluded = source not in RUNNABLE_SOURCES
        return Task(
            id=task_id,
            domain=domain,
            split=split,
            source_benchmark=source,
            prompt=str(record.get("prompt", "")),
            evaluator=EvaluatorSpec(
                type=scorer_type,
                judge_required=scorer_type in JUDGE_SCORER_TYPES,
                spec=_json_dict(scorer, where=f"task {task_id} scorer"),
            ),
            resources=TaskResources(
                assets_dir=assets_dir if asset_files else None,
                asset_files=asset_files,
                public_files=tuple(str(p) for p in (record.get("public_files") or [])),
                claw_public=_json_dict(record["claw_public"], where=f"task {task_id} claw_public")
                if record.get("claw_public")
                else None,
                apex_public=_json_dict(record["apex_public"], where=f"task {task_id} apex_public")
                if record.get("apex_public")
                else None,
            ),
            metadata=_json_dict(record.get("metadata") or {}, where=f"task {task_id} metadata"),
            excluded=excluded,
            exclusion_reason=APEX_EXCLUSION_REASON if excluded else None,
            raw=_json_dict(record, where=f"task {task_id}"),
        )
