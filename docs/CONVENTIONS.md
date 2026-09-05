# Conventions

These rules apply to every module. `docs/reuse/survey.md` records where each one comes from;
section 5 of that survey lists the rules no reference repo follows, which are therefore fresh
work here.

## Toolchain

- Python 3.12 (`requires-python = ">=3.12"`). `uv` manages the environment; `uv.lock` is
  committed and CI installs with `uv sync --locked`.
- `src/` layout: the package is `src/ahd`, built with hatchling.
- `ruff` lints and formats (line length 100, rule set in `pyproject.toml`); `mypy --strict`
  with the pydantic plugin checks `src/` and `tests/`; `pytest` runs tests.
- `make setup lint typecheck test` are the developer entrypoints. Pre-commit runs ruff, mypy,
  whitespace and private-key checks, and refuses any commit that stages `.env`.
- GitHub Actions (`.github/workflows/ci.yml`) runs lint, format check, mypy and the offline
  unit tests on every push and pull request.

## Configuration

- Run configuration is a YAML file under `configs/` validated into pydantic v2 models
  (`ahd.core.config.RunConfig`). Every model extends `StrictModel`: unknown keys are rejected,
  instances are frozen. No hydra, no dict configs, no `.get()` with inline defaults.
- Every config file carries `schema_version`. The loader rejects any version other than the
  one it was written for.
- `require_clean_tree` defaults to `true` for `kind: confirmatory` and `false` for
  `kind: exploratory`. A confirmatory run on a dirty tree refuses to start. Untracked files
  count as dirty.
- The config hash recorded in the manifest is the SHA-256 of the canonical JSON of the
  *validated* model, so comments and key order do not change it but any default does.

## Secrets

- Secrets are read only by `ahd.settings.Settings` (pydantic-settings) from the environment
  or `.env`. `.env` is gitignored; `.env.example` lists the variable names with empty values.
- Nothing in `src/` prints, logs, or writes a secret. Values are `SecretStr`; if a config
  echo ever needs to show one, use `ahd.settings.mask_secret` (first four and last four
  characters).

## Provenance headers

Every file reused or adapted from a reference repository starts with a module docstring that
names the source, in this form:

```
Adapted from: <owner>/<repo> @ <full commit sha>
Original path: <path in that repo> (<function names>; lines <a>-<b>)
License: <SPDX id>, <copyright line> -- see THIRD_PARTY_NOTICES.md
Changes: <one or two sentences>
```

Files with no reference source say so explicitly: `No reference source: written fresh for ahd
(see docs/reuse/M0.md).` Algorithms implemented from a paper cite the paper (arXiv id) and
the algorithm or section number in the docstring of the function that implements them.
`THIRD_PARTY_NOTICES.md` lists every source repo, its sha, license and the files reused from
it; `docs/reuse/M<n>.md` gives the per-module provenance table.

Code from a repository without a license file is not copied. Where the same file exists in a
licensed repository, that one is cited instead.

## Errors

- No silent fallbacks. A missing file, a corrupt cache entry, an unknown model in the price
  table, or a response without a usage block raises; it is never patched over with a default.
- Two runtime families, defined in `ahd.errors`, never conflated:
  - `InfraError`: provider 429/5xx, network errors, timeouts, missing or corrupt files, git
    unavailable. Carries `kind`, `status_code`, `retryable`, `attempts`.
  - `TaskFailure`: the agent or harness failed the task. `BudgetExhausted(TaskFailure)` is a
    task-level outcome and part of the estimand; running out of budget is never an
    infrastructure fault.
- `ConfigError` is raised only before a run starts (invalid config, dirty tree, bad CLI
  usage). It is not a ledger event.
- Exceptions that are neither (a `ValueError` from a bug) propagate unchanged; they are not
  wrapped into either family.
- CLI exit codes: 0 ok, 2 `ConfigError`, 3 `InfraError`, 4 `TaskFailure`.

## LLM calls

- Every provider implements `ahd.llm.provider.Provider.complete(ChatRequest) -> ChatResponse`.
- `ChatRequest` fields: model, messages, temperature, seed, max_tokens, thinking,
  reasoning_effort, timeout_s, plus the bookkeeping fields use_cache and attribution
  (arm, unit_id).
- `seed` is a replicate identifier. DeepSeek's API does not accept a seed parameter, so it is
  never sent on the wire; it is part of the cache key and every ledger row.
- Thinking mode is toggled with `extra_body={"thinking": {"type": ...}}`; in thinking mode
  `temperature` is not sent because the provider ignores it.
- `reasoning_effort` is sent as a **top-level** request parameter, only when thinking is on.
  Verified live on 2026-09-04 against `deepseek-v4-flash`: the API reference documents it
  nested inside the `thinking` object, but the server accepted an invalid nested value
  without complaint (it is ignored), while an invalid top-level value was rejected with a
  400 listing the real set `none, minimal, low, medium, high, xhigh, max`. The guide's
  placement is the correct one; `ReasoningEffort` in `ahd.core.config` uses the server's set.
  `tests/integration/test_deepseek_live.py::test_live_thinking` re-checks this on every
  integration run.
- The SDK client is created with `max_retries=0`. All retrying happens in
  `ahd.llm.retry.run_with_retry` so that every attempt is visible.

### Retry policy

- Retried: HTTP 408, 409, 425, 429, every 5xx, connection errors, timeouts, and a response
  whose `finish_reason` is `insufficient_system_resource`.
- Never retried: 400, 401, 403 (and any other 4xx not listed above).
- Backoff: `min(max_delay_s, initial_delay_s * multiplier ** (attempt - 1)) + uniform(0,
  jitter_s)`, bounded by `max_attempts` and by `total_timeout_s` of wall-clock time.
- Every retry writes one `infra_retry` ledger row and one WARNING log line. Exhaustion writes
  one `infra_failure` row and raises `InfraError` with the attempt count.

### Response cache

- Opt-in per call (`ChatRequest.use_cache`). Asking for the cache on a client that has none
  is an error, not a silent miss.
- One JSON file per key under `<cache_dir>/<provider>/<key[:2]>/<key>.json`, written
  atomically.
- Key = SHA-256 of the canonical JSON of `{cache_version, provider, request}` where request
  is exactly `{model, messages, temperature, seed, max_tokens, thinking, reasoning_effort,
  cache_scope}`. Timeout, use_cache and attribution are excluded. Any change to the key
  contents bumps `CACHE_VERSION` (v2 added `cache_scope`).
- A cache hit is a ledger `call` row with `cached: true` and `usd: 0.0`; token counts are
  copied from the stored response for information.
- A corrupt or mismatched entry raises `InfraError(kind="corrupt_file")`.

### Cost ledger

- `ledger.jsonl` in the run directory, append-only, one row per event
  (`ahd.llm.ledger.LedgerRow`). Columns: schema_version, ts, run_id, event, arm, unit_id,
  seed, model, prompt_tokens, completion_tokens, cache_hit_prompt_tokens, reasoning_tokens,
  cached, latency_ms, usd, pricing_version, pricing_tier, attempt, status_code, error_kind,
  error, request_sha256.
- Event kinds: `call`, `infra_retry`, `infra_failure`, `task_failure`, `search`. `summarize()`
  counts infra and task events separately and never adds them together. `search` rows are
  one per web-search provider call, priced per query from the `search` section of
  `configs/pricing.yaml` (own `pricing_version`); their spend is reported as `search_usd`
  next to LLM `usd`, never folded into it, because environment interaction is part of the
  budget a matched-compute baseline must match. Among `task_failure` rows,
  `error_kind: budget_exhausted` has its own `budget_exhausted` column and is excluded from
  `task_failures`; budget exhaustion is part of the estimand and is never hidden inside a
  generic failure count.
- `usd` is computed from `configs/pricing.yaml` at call time using the request's UTC start
  timestamp to pick the `peak` or `off_peak` tier. Raw token counts and `pricing_version` are
  always stored so `usd` can be recomputed when prices change.
- `configs/pricing.yaml` carries `pricing_version`, `as_of` and `source`. Any change to a
  number bumps `pricing_version`.

## Judge

The judge is a measurement instrument: fixed, strong, deterministic, cached.

- Model `deepseek-v4-pro`, temperature 0, thinking off, cache on (`JudgeConfig`). Every
  judge call is a `ChatRequest` with `arm="judge"` and `unit_id=<task id>`, so judge spend
  is a separate ledger column from policy spend.
- The cache key includes `cache_scope="artifact:<sha256>"` where the hash covers the final
  answer, everything under `outputs/`, and the trajectory. The same artifact always gets the
  same verdict; a changed artifact is always re-judged.
- Evo-Bench's scorer is called with an `AhdJudgeClient`; Claw-Eval's grader is routed by
  patching the single name `claw_eval.graders.llm_judge.OpenAI` (`patched_claw_judge`).
  No proxy, no fork of either codebase.
- The judge is text-only. GDPval's multimodal rubric prompt falls back to Evo-Bench's own
  text-only path and the fallback is recorded in `judge_meta.judge_detail.image_grading`.
- The paper's judge is Qwen3.7-Plus; absolute scores are not comparable to the paper. Reports
  give per-source pass rates and a labelled macro, never the 2:2:1 Overall Score.

## Scoring outcomes

`ahd.tasks.scorer.REASON_RULES` maps every reason string Evo-Bench's scorer can return to a
family: `infra` raises `InfraError` (and writes an `infra_failure` row), `task` returns a
`Score` with `task_failure` set (and writes a `task_failure` row), `judged` returns the
verdict. An unknown string is `InfraError(kind="unknown_reason")`. A missing resource is
never a 0.

## Run directory

`runs/<run_id>/` with `run_id = YYYYMMDDTHHMMSSZ-<6 hex>` unless supplied. Contents:

| File | Writer | Contents |
|---|---|---|
| `manifest.json` | `ahd.core.manifest` | schema_version (2), run_id, created_at, seed, config_sha256, config_schema_version, config_path, git_sha, git_dirty, ahd_version, python_version, platform, out_dir, environment (openai/pydantic versions, LibreOffice version, unshare availability, Evo-Bench submodule sha, dataset id and snapshot sha, Claw-Eval commit), web_snapshot_id (reserved, `null` until M2) |
| `config.resolved.yaml` | `ahd.core.manifest` | the validated config with defaults applied |
| `trace.jsonl` | `ahd.core.trace.TraceWriter` | events, envelope below |
| `ledger.jsonl` | `ahd.llm.ledger.Ledger` | cost and infra/task events |
| `log.jsonl` | `ahd.logs` | JSON-formatted stdlib logging |

A run directory is never overwritten; an existing directory is a `ConfigError`.

## Trace envelope and schema versions

Every trace line is `{"schema_version", "seq", "ts", "run_id", "kind", "payload"}`.
`seq` is contiguous from 1 within a file; the reader rejects gaps, a changed run_id, or a
schema_version it does not know. `payload` is free-form per `kind`.

Bump rule for every versioned schema (`TRACE_SCHEMA_VERSION`, `LEDGER_SCHEMA_VERSION`,
`MANIFEST_SCHEMA_VERSION`, `CONFIG_SCHEMA_VERSION`, `CACHE_VERSION`): **any field added to or
removed from the envelope or record bumps the version.** Renaming is a removal plus an
addition. Changing the meaning of an existing field also bumps. Adding a new `kind` of trace
event or a new key inside a payload does not.

## Logging

- stdlib `logging`, loggers named `logging.getLogger(__name__)`. No `print` in `src/`
  outside `ahd/cli.py` (enforced by ruff rule T20).
- `ahd.logs.configure_logging` installs a console handler and, per run, a JSON-lines file
  handler writing `log.jsonl`. Extra fields passed via `extra=` become top-level JSON keys.

## Benchmark data

Evo-Bench data (CC-BY-4.0, evaluation only) is read from the Hugging Face cache and never
copied into the repository, a run directory, a test fixture, or a prompt that could end up in
a training corpus. Tests use synthetic records in `tests/evobench_fixtures.py`. The canonical
records are `suites/evobench_<split>.json` in the snapshot; the `datasets` viewer rows
(stringified nested fields) are never used as a source. APEX tasks are loaded and flagged
`excluded` with a reason; they are never sampled or scored.

## Tests

- Unit tests are fully offline: the provider is a scripted fake transport
  (`tests/conftest.py`). They must pass with no `.env` present; CI sets a dummy key.
- Integration tests are marked `@pytest.mark.integration`, deselected by default
  (`addopts` in `pyproject.toml`) and run with `make test-integration`.
- Tests that need git create a throwaway repository in `tmp_path`.
