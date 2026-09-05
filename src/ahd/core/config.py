"""Typed run configuration: pydantic v2 models loaded from YAML.

Adapted from: scaleapi/vero @ 0b0e86764d836c456aee5b8dff80d765fdbba77c
Original path: vero/src/vero/models.py (``StrictModel``; lines 8-16)
License: MIT, Copyright (c) 2026 Scale AI -- see THIRD_PARTY_NOTICES.md

The ``schema_version`` check follows AutoSaddler's ``CONFIG_SCHEMA_VERSION`` assertion
(src/autosaddler/v2/config/models.py lines 14, 74-80, MIT) as a pattern; the models here
are fresh.

Changes: ``StrictModel`` is additionally frozen. Everything else in this file is new.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from ahd.core.hashing import sha256_of
from ahd.core.io import read_text
from ahd.errors import ConfigError
from ahd.tasks.kinds import (
    EVOBENCH_DATASET_ID,
    EVOBENCH_PINNED_REVISION,
    Domain,
    SourceBenchmark,
    Split,
)

CONFIG_SCHEMA_VERSION = 1

type RunKind = Literal["exploratory", "confirmatory"]
type ProviderName = Literal["deepseek", "fake"]
type ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh", "max"]
"""The set DeepSeek's server validates (from its 400 message, probed 2026-09-04); the API
reference lists only low/high/max."""


class StrictModel(BaseModel):
    """Base model that rejects unknown fields and is immutable once built.

    A typo'd key fails loudly at load time instead of being dropped in silence.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)


class RetryConfig(StrictModel):
    """Exponential backoff with jitter and a total wall-clock cap.

    Status codes 408, 409, 425, 429 and every 5xx are retried, as are connection errors and
    timeouts. 400, 401 and 403 are never retried. See :mod:`ahd.llm.retry`.
    """

    max_attempts: int = Field(default=5, ge=1)
    initial_delay_s: float = Field(default=1.0, ge=0.0)
    max_delay_s: float = Field(default=30.0, ge=0.0)
    multiplier: float = Field(default=2.0, ge=1.0)
    jitter_s: float = Field(default=1.0, ge=0.0)
    total_timeout_s: float = Field(default=300.0, ge=0.0)
    retry_status_codes: tuple[int, ...] = (408, 409, 425, 429)


class LLMConfig(StrictModel):
    provider: ProviderName = "deepseek"
    model: str = "deepseek-v4-flash"
    base_url: str = "https://api.deepseek.com"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=4096, ge=1)
    thinking: bool = False
    reasoning_effort: ReasoningEffort | None = None
    timeout_s: float = Field(default=120.0, gt=0.0)
    cache_dir: Path = Path(".cache/ahd/llm")
    retry: RetryConfig = RetryConfig()


class JudgeConfig(StrictModel):
    """The measurement instrument: fixed, strong, deterministic, cached.

    All judge calls go through :class:`ahd.llm.deepseek.DeepSeekClient` with
    ``arm="judge"`` so they are ledgered; the cache key includes the judged artifact's hash.
    """

    model: str = "deepseek-v4-pro"
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    max_tokens: int = Field(default=8192, ge=1)
    timeout_s: float = Field(default=300.0, gt=0.0)
    use_cache: bool = True
    thinking: bool = False


class TaskSourceConfig(StrictModel):
    """Which Evo-Bench tasks a run draws from."""

    dataset_id: str = EVOBENCH_DATASET_ID
    revision: str = EVOBENCH_PINNED_REVISION
    split: Split = "validation"
    domains: tuple[Domain, ...] | None = None
    sources: tuple[SourceBenchmark, ...] | None = None
    include_excluded: bool = False
    n: int | None = Field(default=None, ge=1)
    """Stratified subsample size (by source, seeded with the run seed); ``None`` = all."""


class RunConfig(StrictModel):
    schema_version: Literal[1]
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9._-]+$")
    kind: RunKind = "exploratory"
    seed: int = 0
    require_clean_tree: bool
    llm: LLMConfig = LLMConfig()
    judge: JudgeConfig = JudgeConfig()
    tasks: TaskSourceConfig | None = None
    pricing_path: Path = Path("configs/pricing.yaml")
    runs_root: Path = Path("runs")

    @model_validator(mode="before")
    @classmethod
    def _default_require_clean_tree(cls, data: Any) -> Any:
        """Confirmatory runs require a clean tree unless the config says otherwise."""
        if isinstance(data, dict) and data.get("require_clean_tree") is None:
            data = dict(data)
            data["require_clean_tree"] = data.get("kind", "exploratory") == "confirmatory"
        return data


def load_run_config(path: Path) -> RunConfig:
    """Parse and validate a YAML run config.

    A missing file is an :class:`~ahd.errors.InfraError`; bad YAML or a failed validation is a
    :class:`~ahd.errors.ConfigError`.
    """
    text = read_text(path)
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"run config {path} must be a mapping at top level")
    try:
        return RunConfig.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"invalid run config {path}:\n{exc}") from exc


def config_sha256(config: RunConfig) -> str:
    """Hash of the resolved config (post-validation, defaults applied), not of the file text."""
    return sha256_of(config.model_dump(mode="json"))
