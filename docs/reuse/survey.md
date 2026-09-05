# Reference repository survey (M0, Phase A)

Surveyed 2026-09-04. Clones live at `~/work/refs/<name>`; commit shas and licenses are in
[`THIRD_PARTY_NOTICES.md`](../../THIRD_PARTY_NOTICES.md). Paths below are relative to each
repo root. Nothing has been copied into `ahd` yet.

Paper code locations:

- arXiv 2607.12227 "Rethinking the Evaluation of Harness Evolution for Agents" ->
  https://github.com/rethinking-harness-evolution/code (public, **no LICENSE file**; a
  near-verbatim derivative of agentic-harness-engineering with that repo's MIT file dropped).
- arXiv 2602.22480 VeRO -> https://github.com/scaleapi/vero (public, MIT).
- arXiv 2509.25370 AgentDebug -> https://github.com/ulab-uiuc/AgentDebug (public, MIT).

## 1. Survey table

| Repo | License | Python | Package manager / lockfile | Layout | Config system | Runs & artifacts on disk | Trace format | Secrets | Tests | CI |
|---|---|---|---|---|---|---|---|---|---|---|
| **meta-harness** (stanford-iris-lab) | MIT | 3.12 (`>=3.12,<3.13` in `reference_examples/terminal_bench_2/pyproject.toml`, `experimental/harbor_meta_harness/pyproject.toml`); `>=3.11` for `text_classification` | uv per sub-project; **no lockfile committed** (`uv.lock` gitignored, `.gitignore:101`) | Flat; three independent sub-projects under `reference_examples/` and `experimental/`, no `src/`, no root pyproject | Mixed: YAML->dict untyped (`text_classification/benchmark.py:15`), TOML->frozen dataclass typed (`experimental/.../controller.py:52-124`), module constants, argparse, env vars. No hydra/pydantic | `logs/<run_name>/` + `jobs/<run_name>/`, run_name = CLI arg or `%Y%m%d_%H%M%S` (`terminal_bench_2/meta_harness.py:577`); `evolution_summary.jsonl`, `frontier_val.json`, `results/`, `reports/`; per-Claude-session `<ts>_<slug>/meta.json,events.jsonl,artifacts/` (`claude_wrapper.py:323-425`) | JSONL; records `{"type","t",...}` via thread-safe `JSONLLogger` (`inner_loop.py:15-40`); no schema version | `os.environ` + `python-dotenv` `load_dotenv(override=True)`; `.env.example` (10 keys); `.env` gitignored | unittest (ref examples) + pytest (experimental, `testpaths=["tests"]`); 4 files; mocked; no markers | **none**; no pre-commit; ruff `exclude` only |
| **agentic-harness-engineering** (AHE) | MIT | `>=3.13` (`pyproject.toml:6`) | uv; **no lockfile committed** (`.gitignore:8`); setuptools backend | Flat; `evolve.py` (4.7k lines) + `trace_converter.py` + `agents/` package; console script `evolve:main` | YAML->dict with `_base` inheritance + `deep_merge` + `${VAR}` interpolation (`evolve.py:115-165`); argparse. **No typed schema** (pydantic v2 only in one middleware) | `experiments/<%Y-%m-%d__%H-%M-%S>__<name>/` (`evolve.py:259-303`); `config_snapshot.yaml` (merged), `experiment_overlay.yaml`, copy of `evolve_agent/`, `runs/iteration_NNN/{input,evolve}/`, `change_manifest.json`; harbor trial dirs `reward.txt`, `exception.txt`, `result.json` | Whole-file JSON (`evolve_trace.json`, `nexau_in_memory_tracer.cleaned.json`), atomic tmp+`os.replace` (`evolve.py:2941-2976`); normalized by `trace_converter.py`; no schema version | `load_dotenv(PROJECT_DIR/".env", override=True)` (`evolve.py:39`); `${LLM_API_KEY}` in YAML; masked on echo; `.env.example` (12 keys); `.env` gitignored | pytest, only in vendored `agents/.../agent-debugger-cli/_source/tests/` (15 files, monkeypatch); none for `evolve.py`; no markers | **none** (no `.github/`); no ruff/mypy/pre-commit config |
| **AutoSaddler** (microsoft) | MIT | `>=3.12,<3.15` (`pyproject.toml:11`); CI 3.12 | uv + **committed `uv.lock`**; CI `uv sync --extra dev --locked`; setuptools backend | **`src/autosaddler/{v1,v2}`**; `python -m autosaddler.v2.cli`; no console scripts | YAML `safe_load` -> frozen dataclasses with strict key check and `CONFIG_SCHEMA_VERSION="autosaddler/v2"` asserted (`v2/config/models.py:14,74-80`). No pydantic/hydra | `<storage.run_root>/<run-id>/`, **run id is a required CLI arg** (`v2/cli.py:13`); `events.jsonl` (authoritative), `manifest.json` (checksummed, `local.py:222-239`), `snapshot.json`, `metrics.jsonl`, `metrics-summary.json`, `resolved_config.yaml`, `sessions/<sha256>/`; atomic tmp+fsync+replace (`local.py:475`) | JSONL `RunEvent` `{schema_version,sequence,timestamp,run_id,event_type,operation_id,payload}`, `EVENT_SCHEMA_VERSION="autosaddler-event/v1"` (`v2/core/events.py:11,54`); sequence validated on read | `os.environ` only; no dotenv; **no `.env.example`; `.env` not gitignored**; test asserts key never in subprocess argv | pytest, `tests/` 26 files (~7.9k lines), fakes + `FakeAgentProvider`; no markers; no conftest; no pytest ini | GH Actions `ci.yml`: setup-uv, `uv sync --locked`, `ruff check`, grep guard, `pytest`, `uv build`. ruff only (line 120, py312); no mypy/pre-commit |
| **Evo-Bench** (RUCAIBox) | Apache-2.0 + `NOTICE`; vendors Archipelago grading (Apache-2.0) in `third_party/` | `>=3.10` (`pyproject.toml:9`), 3.11 recommended; ruff `py310` | setuptools; `uv pip install -e .` or pip; **no lockfile** | Flat `evobench/` package + `policy_harness_seed/`, `data_construction/`; console scripts `evobench`, `run-evolve`, `run-evaluation` | argparse + JSON model configs -> frozen dataclass `ModelConfig` with `api_key_env`/`api_base_env` indirection (`models/client.py:11-80`); `EvolveRunConfig` dataclass. No hydra/pydantic | `<run_root>/<%Y%m%d-%H%M%S>-<uuid4[:8]>/` (`evolution/harness.py:3607`); `run_manifest.json` (run_id, created_at, seed, config paths, `framework_version`, `resumed_from`), `evolver_trajectory.jsonl`, `run_result.json`, `snapshots/iteration_NNN/`; eval `rollouts/<task>/trajectory.json+metadata.json+score.json+rollout.log` | JSONL for evolver (`common/jsonl.py:8-13`, records `{type,step,message,usage,attempts,time}`); pretty JSON for rollouts; no schema version on traces (only `artifacts/construction/anchor_manifest.json` has `schema_version: 1`) | `os.environ` via `api_key_env` names in config; no dotenv; `.env.example`; `.gitignore` has `.env`, `*.env`, `!.env.example` | unittest (23 files, `unittest.mock`), `python -m unittest discover`; no markers; no pytest ini | **none**; ruff dev extra (line 100); no mypy/pre-commit |
| **AgentRx** (microsoft) | MIT (`LICENSE.txt`) | `>=3.10` (`pyproject.toml:11`) | pip + setuptools; `requirements.txt` (`>=` floors); **no lockfile** | Flat `agentrx/` + root `run.py` driver loaded by path from `agentrx/cli.py:19-27`; console script `agentrx` | Env vars (`AGENT_VERIFY_*`) + argparse + module globals (`pipeline/globals.py`); no yaml/json/pydantic/hydra; typed only `DomainConfig` dataclass | `runs/<run_name>/`, run_name = arg or `<stem>_<%Y%m%d_%H%M%S>` (`run.py:611`); `state.json` manifest `{completed_stages, config{input,domain,endpoint,started}}` rewritten per stage; `trajectory_ir.json`, `checker_results/`, `judge_output/`, `plots/`; `checkpoint.json` | JSON; IR schema is a dict literal `{trajectory_id, instruction, steps[{index, substeps[{sub_index, role, content}]}]}` (`ir/trajectory_ir.py:5-16`); loader accepts JSON/JSONL/markdown; no schema version | `os.environ` + `python-dotenv`; Azure AAD token providers; `.env.example`; `.env` gitignored | 1 unittest file; no API calls; no markers; no pytest config | GH Actions **CodeQL only** (no lint/test); dependabot; no ruff/mypy/pre-commit |
| **rethinking-harness-evolution** (arXiv 2607.12227) | **No LICENSE file**; `pyproject.toml:7` says `license = {text="MIT"}`; no attribution to AHE | `>=3.13` (`pyproject.toml:6`) | uv; **no lockfile** (gitignored); deps are git URLs + path source | Flat; `evolve.py` (253 KB), `evolve_ahe.py` (220 KB), `evolve_seq.py`, `run_*.py` + `agents/`; console scripts `harness-scaling`, `harness-evolution` | Same as AHE: YAML `_base` + `deep_merge` + `${VAR}` (`evolve.py:159`), argparse; **no typed schema** | Same as AHE: `experiments/<ts>__<name>/` (`evolve.py:282-290`), `config_snapshot.yaml`, `runs/iteration_NNN/`, plus `tasks/`, `per_task_evolution_summary.json`; workspaces git-tagged per iteration | Whole-file JSON, atomic write + background flush thread (`evolve.py:3013-3057`); no schema version | `load_dotenv(..., override=True)`; `${VAR}` in YAML; `--api-key`/`--api-key-env` CLI; `_redact_secrets` helper (`run_blind_rollout_selector.py:49-60`); `.env.example`; `.env` gitignored | pytest only in vendored `_source/tests/` (16 files); none for top-level scripts; no markers | **none**; no ruff/mypy/pre-commit |
| **VeRO** (scaleapi, arXiv 2602.22480) | MIT | `>=3.11,<3.14` (`vero/pyproject.toml:10`); CI 3.12 | uv + hatchling; **committed `vero/uv.lock`** (+ per-benchmark locks); CI `uv sync --locked` | **`vero/src/vero/`** and `vero-tasks/src/vero_tasks/`; click CLIs `vero`, `evals`, `vero-task` | TOML -> **pydantic v2** `VeroConfig.model_validate` (`config.py:371`); all models extend `StrictModel(extra="forbid")` (`models.py:8`); YAML only for harbor build specs. No hydra/argparse/pydantic-settings | `$VERO_HOME/sessions/<session_id>/` (`config.py:410-424`), id = config value, else `uuid4`, else resumed from manifest; `manifest.json` (`SessionManifest.schema_version: Literal[3]`, per-component `config_digest` sha256), `events.jsonl`, `artifacts/`, `evaluations/<uuid4>/evaluation.json`, `cases/<sha256(case_id)>.json` | JSONL `RuntimeEvent{id,session_id,kind,created_at,payload}` via async-locked `JsonlEventSink` (`runtime/events.py:22,107`); gateway request log `requests-%05d.jsonl` with `"schema_version":1`, `latency_ms`, `input_tokens`, `output_tokens`, `cached_input_tokens`, cost fields (`gateway/inference.py:483-486,885-905`) | `os.environ`; hand-rolled 20-line dotenv parser (`interpret/config.py:19-48`); `secrets.env.example`; `.gitignore` `.env*`, `secrets.env`, `!*.example`; redaction regex in harbor CLI | pytest + pytest-asyncio; `vero/tests/` 47 files, `testpaths=["tests"]`; markers only built-in (`skipif` on docker/uv); mocked; CI sets fake key + unreachable base URL | GH Actions `tests.yml`: `uv sync --locked` + `uv run pytest -q` for `vero` and a benchmark matrix. **No lint/type step**; ruff config (line 88, `E4,E7,E9,F,I`); no mypy/pre-commit |
| **AgentDebug** (ulab-uiuc, arXiv 2509.25370) | MIT | `>=3.10` (`pyproject.toml:11`) | pip + setuptools; **no lockfile**; pydantic/requests/PyYAML imported but undeclared | Flat `agentdebug/` + `detector/` (no `__init__.py`, not packaged); `python -m` entrypoints; README references nonexistent `examples/`, `docs/` | argparse only (30+ flags, `rollout/rollout.py:510-593`); `SimpleNamespace`; detector config is a plain dict; no typed schema | No unified run dir: `logs/{env}/unified_run_{ts}.log`, `--dump_path` JSONL, `{chat_root}/trajectories/{run_ts}/{env}/{model}/chat_{id}.json` (`rollout.py:614-638`); id has `sha1(task_id)[:8]`; no manifest, no config copy | Step-level JSONL rows (`batch_idx,...,prompt,action,reward,done,won`) + per-episode JSON `{messages, metadata}` (`rollout.py:373-458`); no schema version | `os.getenv` + optional dotenv; `OPENAI_API_KEY` defaults to literal `"EMPTY"`; CLI `--summary_api_key`; **no `.env.example`**; `.env` gitignored | **none** | **none**; pre-commit listed as dep but unconfigured |

### Columns not in the table but needed for M0

| Repo | LLM client & retry | Response cache | Token / cost ledger | Infra vs task errors | Logging |
|---|---|---|---|---|---|
| meta-harness | litellm 1.84.10; **tenacity** `wait_exponential(2..32)+random`, 6 attempts, retries only status 429 + message substrings (`text_classification/llm.py:90-109,197-202`) | Disk JSON, one file per key: sha256 of `{version, model, api_base, system_prompt, prompt, kwargs}`, `CACHE_VERSION=1`, atomic tmp+replace (`llm.py:164-195`) | `litellm.completion_cost` (no local table); `get_usage()` totals (`llm.py:385`) | Only `BlockError` in TB2 agent; controller collapses infra timeouts into `reward=0.0` | `print()` dominant; stdlib logging in `llm.py` only |
| AHE | NexAU framework; openai + anthropic SDKs in failover middleware; hand-rolled: circuit breaker on `[500,502,503]` + class names `RateLimitError`/`InternalServerError` (`middleware/llm_failover.py:60-61`); `_run_with_retry` 3 attempts `sleep(2**i)` bare except | none | Sums provider-reported `calculatedTotalCost`; no table (`trace_converter.py:692-728`) | Per-task `pass|fail|exception` + `infra_only` bucket (`evolve.py:612-670,791-826`); timeouts a separate axis; `HarborJobTimeoutError`, `ExperimentTimeoutError` | `print()` in scripts; stdlib logging in `agents/` |
| AutoSaddler | Agent SDKs behind `AgentTransport` protocol; hand-rolled session backoff `base*2**(attempt-1)`, `SessionRetriesExhausted`; bounded `infrastructure_retries`; **no status-code logic** | none | `Usage` dataclass, provider-reported cost, `metrics.jsonl`; no table | `Disposition = Literal["success","task_failure","execution_error","invalid"]` (`v2/core/domain.py:15`); `infrastructure-error.json` per attempt; `SessionStatus` timeout/failed/interrupted | stdlib logging + much `print`; no JSON formatter |
| Evo-Bench | openai + anthropic SDKs wrapped (`models/client.py`); hand-rolled 6 attempts, base 120 s doubling, retries any exception; transient = `{408,409,425,429}` or `>=500` (`evolution/lib/dissector.py:422`) | Disk JSON sha256-of-sorted-payload for **web tools only** (`tools/websearch.py:43-56`); no LLM cache | `usage_to_dict` normalizes prompt/completion/cached tokens across OpenAI/DeepSeek/Anthropic (`client.py:456-543`); no table; `scripts/compute_run_cost.py` referenced but **missing** | `_INFRA_EXIT_REASONS` -> `infra_failures`/`infra_failure_rate` (`evaluation/metrics.py:104-163`); `E2bExecTransportError` retried, nonzero exit not | stdlib logging console+file; no JSON formatter |
| AgentRx | Azure OpenAI via AAD; hand-rolled `MAX_RETRIES=5`, `4*2**attempt`, catches only `openai.RateLimitError` (`llm_clients/trapi.py:40-54`) | none | `TokenUsage`/`LLMCallTelemetry` dataclasses with uuid4 call id (`reports/metrics.py`); no usd | Not distinguished; broad `except Exception` + print + continue; task failures as data | `print()` only |
| rethinking | As AHE (failover middleware); hand-rolled 3 attempts backoff 2.0 doubling | none | As AHE | As AHE; exception type regex-parsed from `exception.txt` | `print(flush=True)` ~180 sites; stdlib logging in `agents/` |
| VeRO | `AsyncOpenAI(max_retries=0)` + hand-rolled jittered `2**attempt+random`, 4 retries (`interpret/labeling/client.py:74-81`); declarative `RetryPolicy` retries `[429,503,529]` + `RateLimitError` names + "rate limit" patterns (`evaluation/models.py:130-145`) | Content-addressed disk JSON cache, `key_of()` sha256, atomic rename (`interpret/cache.py`) | `InferenceUsage` -> `usage.json` per scope; request JSONL has tokens+latency+cost; **no pricing table** (comment at `gateway/inference.py:645-653`: a public price table ran 3.1x high, so cost comes from proxy headers) | `EvaluationInfrastructureError` (transient, retried) vs `EvaluationTerminatedError` (non-retryable) vs `EvaluationExecutionError` (`evaluation/exceptions.py:26-44`); `EvaluationStatus.INVALID` when too many infra losses; `CaseError.retryable/terminal`; `tests/test_v05_error_taxonomy.py` | stdlib logging + `click.echo`; no `print` in package; no JSON formatter |
| AgentDebug | openai/anthropic/gemini/together thin wrappers; hand-rolled bare-except retry, on final failure **returns a fabricated default action** (`rollout/rollout.py:87-104`); engines have no retry | none (`enable_cache=True` passed to a nonexistent param, `TypeError` swallowed) | none | Not distinguished; no custom exceptions | stdlib logging root config |

## 2. Cross-cutting observations

- **Nobody uses hydra.** Config formats split YAML (4), TOML (2), JSON (1), none (2 env/argparse only).
- **Typed config schemas exist in 4 of 8** and each uses a different mechanism: frozen dataclass
  with strict keys and a schema-version string (AutoSaddler), pydantic v2 with `extra="forbid"`
  (VeRO), plain frozen dataclass (Evo-Bench, meta-harness experimental). The 4 untyped repos
  access config by string key with inline defaults.
- **JSONL is the majority trace format (5 of 8)**; AHE, rethinking and AgentRx write whole-file
  JSON. Only **AutoSaddler and VeRO put a schema version on every record**.
- **Run ids are timestamp strings in 6 of 8** (Evo-Bench adds a `uuid4[:8]` suffix); VeRO uses
  a bare `uuid4`; AutoSaddler requires the caller to supply one.
- **A JSON manifest is written by 4 of 8** (`manifest.json` in AutoSaddler/VeRO,
  `run_manifest.json` in Evo-Bench, `state.json` in AgentRx). A resolved-config copy is written
  by 3 (AHE, rethinking, AutoSaddler). **No repo records its own git sha in the manifest**;
  Evo-Bench records `framework_version`, VeRO records per-component sha256 `config_digest`.
- **Committed lockfile in only 2 of 8** (AutoSaddler, VeRO), and those are the only two with a
  CI job that runs tests. Three uv users explicitly gitignore `uv.lock`.
- **`src/` layout in 2 of 8** (AutoSaddler, VeRO), the same two.
- **Retry is hand-rolled in 7 of 8** (tenacity only in meta-harness). Retried status codes vary
  widely: 429 only; 500/502/503; 429/503/529; 408/409/425/429/5xx; `RateLimitError` class only.
- **Response caching exists in 3** (meta-harness, VeRO, Evo-Bench web tools) and all three use
  one-JSON-file-per-sha256-key with atomic rename. **None uses sqlite.** All three include an
  explicit cache/format version in the key or path.
- **No repo has a pricing table.** Cost is either delegated to litellm, taken from provider
  headers, or absent. VeRO documents that a public price table over-estimated by 3.1x. Every
  repo that tracks cost also records raw prompt/completion token counts.
- **Infra vs task failure is a first-class distinction in 4 of 8** (AutoSaddler, Evo-Bench,
  VeRO, AHE/rethinking) via a status literal or exception subclass; AgentDebug is the
  anti-pattern (LLM failure becomes a fake action recorded as if the agent chose it).
- **Every repo mocks the provider in tests; none has an `integration` marker.**
- **No repo runs mypy or has a pre-commit config; none has a JSON log formatter.** Structured
  output is always the JSONL event file, never the logger.
- Secrets: `os.environ` everywhere; python-dotenv in 5; `.env.example` in 6; `.env` gitignored
  in 7 (AutoSaddler is the exception). pydantic-settings: none. Two repos redact keys before
  echoing config (AHE/rethinking, VeRO).

## 3. Recommended layout for `ahd`

The intersection of the two production-grade references (AutoSaddler, VeRO), with the
majority choice elsewhere.

```
agent_harnesses_diagnostic/
├── pyproject.toml              # uv project; [tool.ruff] [tool.mypy] [tool.pytest.ini_options]
├── uv.lock                     # committed
├── Makefile                    # setup / lint / typecheck / test
├── README.md  CONTRIBUTING.md  THIRD_PARTY_NOTICES.md
├── .env.example                # key names only; .env is gitignored
├── .pre-commit-config.yaml
├── .github/workflows/ci.yml    # uv sync --locked; ruff check; ruff format --check; mypy; pytest -m "not integration"
├── configs/
│   ├── pricing.yaml            # per-model usd per 1M tokens, with a pricing_version
│   └── runs/
│       └── example.yaml        # RunConfig instance (schema_version, seed, model, ...)
├── docs/
│   ├── CONVENTIONS.md
│   └── reuse/                  # survey.md, M0.md, ...
├── src/ahd/
│   ├── __init__.py             # __version__
│   ├── cli.py                  # argparse: ahd version | llm ping | runs new
│   ├── errors.py               # InfraError, TaskFailure
│   ├── settings.py             # pydantic-settings: DEEPSEEK_API_KEY, HF_TOKEN, DEEPSEEK_BASE_URL
│   ├── logging.py              # stdlib logging + JSON formatter
│   ├── core/
│   │   ├── config.py           # pydantic v2 models, extra="forbid", load_yaml()
│   │   ├── context.py          # RunContext
│   │   ├── manifest.py         # manifest.json writer
│   │   ├── trace.py            # JSONL writer, versioned envelope
│   │   └── hashing.py          # canonical_json, sha256_digest, file/dir hashing
│   └── llm/
│       ├── types.py            # ChatRequest, ChatResponse, Usage (pydantic)
│       ├── provider.py         # Provider protocol
│       ├── deepseek.py         # DeepSeekClient (openai SDK, max_retries=0)
│       ├── retry.py            # backoff loop, InfraError after N
│       ├── cache.py            # sqlite cache keyed by sha256(canonical request + seed)
│       ├── pricing.py          # pricing.yaml loader
│       └── ledger.py           # append-only JSONL cost ledger
├── tests/
│   ├── conftest.py             # FakeProvider fixture, tmp run dir
│   ├── unit/                   # offline, provider mocked
│   └── integration/            # @pytest.mark.integration, real DeepSeek
└── runs/                       # gitignored
    └── <run_id>/
        ├── manifest.json       # schema_version, run_id, created_at, seed, config_sha256, git_sha, git_dirty, ahd_version
        ├── config.resolved.yaml
        ├── ledger.jsonl        # one row per LLM call
        ├── trace.jsonl         # {"schema_version","seq","ts","run_id","kind","payload"}
        └── log.jsonl           # JSON-formatted stdlib logging
```

### Per-choice justification

| Choice | What the references do | Why |
|---|---|---|
| `uv` + committed `uv.lock`, CI runs `uv sync --locked` | uv in 5/8; lock committed only in AutoSaddler and VeRO, both of which have green CI | The two repos with working CI both commit the lock and install with `--locked`; the three that gitignore it have no CI at all. |
| `src/ahd` layout | AutoSaddler, VeRO | Both engineered references use `src/`, and it stops tests importing the working tree instead of the installed package. |
| Build backend: hatchling | VeRO hatchling; AutoSaddler/Evo-Bench/AgentRx/AgentDebug setuptools | Hatchling discovers `src/<pkg>` with zero config, whereas setuptools needs `packages.find where=["src"]` (AutoSaddler); either works with uv. *Disagreement flagged in section 4; setuptools is the majority.* |
| Config: YAML files -> pydantic v2 models with `extra="forbid"` and a `schema_version` field | YAML majority (4); pydantic v2 + `StrictModel` in VeRO; `CONFIG_SCHEMA_VERSION` assertion in AutoSaddler | YAML is the majority format; VeRO's `extra="forbid"` makes a typo'd key fail at load time, and AutoSaddler's version string lets old configs be rejected explicitly. |
| Config hash in the manifest = sha256 of `canonical_json(model_dump())` | VeRO `config_digest` sha256 per component; AutoSaddler `canonical_json` (sorted keys, compact separators, ascii) | Hashing the parsed model rather than the file text makes the hash insensitive to comments and key order. |
| Run id: `<UTC %Y%m%dT%H%M%SZ>-<6 hex>` unless `--run-id` given | Timestamp in 6/8; Evo-Bench appends `uuid4[:8]`; AutoSaddler takes it as an argument | Timestamp keeps `ls runs/` sorted and human-readable; the random suffix avoids collisions when two runs start in the same second; accepting an explicit id supports resume. |
| Run dir contents: `manifest.json`, `config.resolved.yaml`, `trace.jsonl`, `ledger.jsonl`, `log.jsonl` | `manifest.json` in AutoSaddler/VeRO; resolved config copy in AHE/AutoSaddler; `events.jsonl` in AutoSaddler/VeRO/Evo-Bench | Every artifact a run needs to be re-interpreted later lives in one directory; the flat file set is the union of what the three manifest-writing repos emit. |
| Git sha and dirty flag in the manifest | **No reference does this** | Required by the M0 spec; fresh code (`git rev-parse HEAD` + `git status --porcelain`). |
| Trace: JSONL, one envelope per line `{schema_version, seq, ts, run_id, kind, payload}` | JSONL in 5/8; envelope is AutoSaddler's `RunEvent` minus `operation_id`, matching VeRO's `{id, session_id, kind, created_at, payload}` | Append-only lines survive crashes mid-run (whole-file JSON in AHE needed a background flush thread to approximate this); `seq` lets a reader detect truncation as AutoSaddler does. |
| Atomic writes for JSON files (tmp + fsync + `os.replace`) | AutoSaddler `_atomic_write`, VeRO `_atomic_write_json`, meta-harness cache, AHE trace dump | All four repos that care about crash safety use the same idiom. |
| LLM client: `openai` SDK against DeepSeek's OpenAI-compatible endpoint, `max_retries=0`, our own retry loop | openai SDK direct in 6/8; VeRO sets `max_retries=0` so the SDK never retries silently; hand-rolled retry in 7/8 | Owning the loop is the only way to log every retry as an infra event and count them in the ledger. |
| Retry set: 408, 409, 425, 429, 5xx, plus connection errors and timeouts; exponential backoff with jitter; N attempts then `InfraError` | Union of Evo-Bench (`408/409/425/429/>=500`), VeRO (`429/503/529`), AHE (`500/502/503`); jitter from meta-harness/VeRO | The references disagree on the set, so take the union of the codes any of them treats as transient; 4xx other than these are never retried. |
| Cache key = sha256 of canonical JSON of the full request including `seed` and a `cache_version` | meta-harness (`CACHE_VERSION` in key), VeRO `key_of`, Evo-Bench web cache | All three caching repos version the key; including the seed is an M0 requirement and matches "same inputs, same key". |
| Cache backend: sqlite (single file, opt-in per call) | **All three caching references use one JSON file per key; none uses sqlite** | M0 spec mandates sqlite; it gives one file, transactional writes, and cheap hit/miss statistics. *Divergence flagged in section 5.* |
| Ledger: append-only JSONL, one row per call, raw token counts always recorded, `usd` computed from `configs/pricing.yaml` and the `pricing_version` stored on the row | Token counts recorded in VeRO/AutoSaddler/AgentRx/meta-harness; **no reference has a pricing table** | Raw tokens plus a versioned pricing file mean `usd` can be recomputed when prices change, which is exactly the failure VeRO hit with a stale public table. |
| Errors: `InfraError` vs `TaskFailure`, never conflated, counted separately | VeRO `EvaluationInfrastructureError` vs `EvaluationTerminatedError`; AutoSaddler `execution_error` vs `task_failure`; Evo-Bench `infra` bucket | Matches the split the three best references make; VeRO's `INVALID` status (too many infra losses to trust the aggregate) is the pattern to adopt later in experiment code. |
| Logging: stdlib `logging` with a JSON formatter to `log.jsonl` + human console; no `print` in `src/` | stdlib logging in 5/8; VeRO has zero `print` in its package; **no reference has a JSON formatter** | JSON lines make the log greppable next to the trace; fresh code. |
| Secrets: pydantic-settings reading `.env`; `.env` gitignored; `.env.example` committed; keys redacted in any echoed config | dotenv in 5/8, `.env.example` in 6/8, `.env` gitignored in 7/8; masking in AHE, redaction in VeRO; **pydantic-settings in none** | pydantic-settings is a typed superset of `load_dotenv` + `os.environ` and fails at startup if a key is missing; masking rule follows AHE. |
| Tests: pytest, `testpaths=["tests"]`, provider fake fixture, `integration` marker deselected by default | pytest in AutoSaddler/VeRO/meta-harness-experimental; `testpaths` in VeRO; `FakeAgentProvider` in AutoSaddler; **no reference has an integration marker** | Offline unit tests are universal; the marker is fresh but cheap. |
| CI: GitHub Actions, `uv sync --locked`, ruff check + format, mypy strict, pytest | AutoSaddler (`uv sync --locked`, ruff, pytest, build); VeRO (`uv sync --locked`, pytest); **no reference runs mypy or format checks** | Take AutoSaddler's job and add the two steps the M0 spec requires. |
| CLI: argparse | argparse in 7/8; click in VeRO; typer in none | No extra dependency; nested `ahd runs new` is a few lines of subparsers. *Taste choice, flagged.* |
| Hashing helpers: `canonical_json` + `sha256_digest` + directory hash | AutoSaddler `domain.py:41-45`; Evo-Bench `hash_harness` (sorted rglob, path+bytes, NUL separators) | sha256 is what every reference uses; the canonical-JSON rule (sorted keys, compact separators, ascii) is copied verbatim so digests are stable across machines. |

## 4. Where two or more references disagree

| Topic | Positions |
|---|---|
| Python floor | 3.10 (Evo-Bench, AgentRx, AgentDebug) / 3.11 (VeRO, meta-harness text_classification) / 3.12 (AutoSaddler, meta-harness TB2) / 3.13 (AHE, rethinking) |
| Lockfile | committed (AutoSaddler, VeRO) vs explicitly gitignored (meta-harness, AHE, rethinking) vs no lock tooling (Evo-Bench, AgentRx, AgentDebug) |
| Layout | `src/` (AutoSaddler, VeRO) vs flat package (Evo-Bench, AgentRx, AgentDebug, AHE, rethinking) vs loose sub-projects (meta-harness) |
| Build backend | setuptools (AutoSaddler, Evo-Bench, AgentRx, AgentDebug, AHE) vs hatchling (VeRO) |
| Config format | YAML (meta-harness, AHE, rethinking, AutoSaddler) vs TOML (VeRO, meta-harness experimental) vs JSON (Evo-Bench) vs env+argparse only (AgentRx, AgentDebug) |
| Config typing | pydantic v2 (VeRO) vs frozen dataclass (AutoSaddler, Evo-Bench, meta-harness experimental) vs untyped dict (meta-harness, AHE, rethinking) |
| Trace format | JSONL (meta-harness, AutoSaddler, Evo-Bench, VeRO, AgentDebug) vs whole-file JSON (AHE, rethinking, AgentRx) |
| Schema version on trace records | yes (AutoSaddler, VeRO) vs no (all others) |
| Run id | timestamp (meta-harness, AHE, rethinking, AgentRx, AgentDebug) vs timestamp+uuid8 (Evo-Bench) vs uuid4 (VeRO) vs caller-supplied (AutoSaddler) |
| Retry library | tenacity (meta-harness) vs hand-rolled (all others) |
| Retried status codes | 429 only (meta-harness) / `RateLimitError` class only (AgentRx) / 500,502,503 (AHE, rethinking) / 429,503,529 (VeRO) / 408,409,425,429,>=500 (Evo-Bench) / none (AutoSaddler, AgentDebug) |
| Cache | file-per-key JSON (meta-harness, VeRO, Evo-Bench web) vs none (5 repos) |
| Cost source | litellm (meta-harness) vs provider headers (VeRO) vs provider-reported in SDK result (AutoSaddler) vs summed from trace fields (AHE, rethinking) vs tokens only, no usd (AgentRx, Evo-Bench) vs none (AgentDebug) |
| Test framework | pytest (AutoSaddler, VeRO, meta-harness experimental, AHE/rethinking vendored) vs unittest (Evo-Bench, AgentRx, meta-harness ref examples) vs none (AgentDebug) |
| CI | ruff+pytest+build (AutoSaddler) vs pytest only (VeRO) vs CodeQL only (AgentRx) vs none (5 repos) |
| Console logging | stdlib logging (AutoSaddler v2, Evo-Bench, VeRO, AgentDebug) vs `print` (meta-harness, AHE scripts, rethinking scripts, AgentRx) |
| Secrets loading | python-dotenv (meta-harness, AHE, rethinking, AgentRx, AgentDebug) vs `os.environ` only (AutoSaddler, Evo-Bench) vs hand-rolled `.env` parser (VeRO) |
| `.env` hygiene | gitignored + `.env.example` (6 repos) vs gitignored, no example (AgentDebug) vs neither (AutoSaddler) |
| CLI library | argparse (7) vs click (VeRO) |
| Infra vs task errors | typed status/exception (AutoSaddler, Evo-Bench, VeRO, AHE, rethinking) vs not distinguished (meta-harness controller, AgentRx, AgentDebug) |

## 5. Where the M0 conventions diverge from what the references do

These are not objections; they are the places where Phase B will be writing fresh code rather
than adapting a reference.

1. **Python 3.11.** Above the floor of 5 references, below AutoSaddler (3.12), AHE and rethinking
   (3.13). Any file adapted from AutoSaddler must be checked for 3.12-only syntax (PEP 695 `type`
   aliases, generic class syntax) before it will pass `mypy --strict` on 3.11.
2. **pydantic-settings for secrets.** No reference uses it (5 use python-dotenv). Same behaviour,
   typed.
3. **sqlite response cache.** All three caching references use a JSON file per key. The key
   derivation is copied from them; only the storage differs.
4. **`configs/pricing.yaml`.** No reference ships a pricing table. Row-level raw tokens and a
   `pricing_version` field are included so `usd` is always recomputable.
5. **JSON log formatter, `mypy --strict`, pre-commit, format check in CI.** None of the eight has
   any of these.
6. **`integration` pytest marker.** None of the eight has one; all mock the provider.
7. **Git sha in the manifest.** No reference records it.

## 6. Reuse candidates for M0 (file level, licensed sources only)

Only MIT/Apache-2.0 sources are listed. Everything in rethinking-harness-evolution also exists in
agentic-harness-engineering (MIT), which is the repo to cite if any of it is used.

| Need | Candidate | Repo (license) | Notes |
|---|---|---|---|
| `canonical_json`, `sha256_digest`, `to_json_value` | `src/autosaddler/v2/core/domain.py:19-45` | AutoSaddler (MIT) | Dependency-free; adopt verbatim as `ahd/core/hashing.py` basis. |
| Directory content hash | `evobench/evaluation/runner.py:562-574` `hash_harness` | Evo-Bench (Apache-2.0) | Sorted rglob, path+bytes with NUL separators, skips `.git`/`__pycache__`. |
| Versioned event envelope | `src/autosaddler/v2/core/events.py` `RunEvent` + `EVENT_SCHEMA_VERSION` | AutoSaddler (MIT) | Dataclass; port to pydantic to match config models. |
| JSONL writer | `evobench/common/jsonl.py` `append_jsonl` (22 lines) | Evo-Bench (Apache-2.0) | Simplest; VeRO `runtime/events.py:107` `JsonlEventSink` is the async-locked variant. |
| Atomic JSON write | `src/autosaddler/v2/storage/local.py:475` `_atomic_write`; `vero/src/vero/evaluation/store/persistence.py:37` `_atomic_write_json` | AutoSaddler / VeRO (MIT) | tmp + fsync + `os.replace`. |
| Manifest writer with schema version | `src/autosaddler/v2/storage/local.py:222-239`; `vero/src/vero/runtime/session.py:90` `SessionManifest` | AutoSaddler / VeRO (MIT) | Take the field set, not the code (both are entangled with their run stores). |
| Strict pydantic base | `vero/src/vero/models.py:8` `StrictModel` (16 lines) | VeRO (MIT) | `extra="forbid"`. |
| Cache key derivation | `reference_examples/text_classification/llm.py:164-195` | meta-harness (MIT) | sha256 over `{version, model, api_base, ...}`; add `seed`. Also `vero/src/vero/interpret/cache.py:22` `key_of`. |
| Retry predicate / status set | `evobench/evolution/lib/dissector.py:410-433`; `vero/src/vero/evaluation/models.py:130-145` `RetryPolicy` | Evo-Bench (Apache-2.0) / VeRO (MIT) | Union their sets; jittered backoff from `vero/src/vero/interpret/labeling/client.py:74-81`. |
| Usage normalization (prompt/completion/cached tokens, DeepSeek conventions) | `evobench/models/client.py:456-543` `usage_to_dict` | Evo-Bench (Apache-2.0) | Already handles DeepSeek's `prompt_cache_hit_tokens`. |
| Ledger row shape | `vero/src/vero/gateway/inference.py:885-905` request log fields; `src/autosaddler/v2/providers/fake.py` `PaidWorkLedger` | VeRO / AutoSaddler (MIT) | Field names for `latency_ms`, `input_tokens`, `output_tokens`, `cached_input_tokens`. |
| Error taxonomy | `vero/src/vero/evaluation/exceptions.py:26-44`; `src/autosaddler/v2/core/domain.py:15` `Disposition` | VeRO / AutoSaddler (MIT) | Names and docstrings only; `ahd` needs two classes. |
| Secret redaction | `evolve.py:48-54` key masking at startup echo (`v[:4]+"***"+v[-4:]`) | AHE (MIT) | Rethinking's fuller `_redact_secrets` (`run_blind_rollout_selector.py:49-60`, keys containing `api_key` or ending `token`) is **not** in AHE and is unlicensed; re-implement the rule, do not copy. |
| Fake provider fixture | `src/autosaddler/v2/providers/fake.py` | AutoSaddler (MIT) | Pattern for `tests/conftest.py`. |

Not available anywhere: sqlite cache, pricing table, JSON log formatter, git-sha capture,
argparse-based `ahd`-style CLI with subcommands (AgentRx `cli.py` is the closest and it loads
`run.py` by file path).

## 7. Environment notes for Phase B

- `uv` is **not installed** on this machine; Phase B `make setup` must install it (or document
  `curl -LsSf https://astral.sh/uv/install.sh | sh`, which is what VeRO's CI does).
- System Python is 3.12.9 (miniconda); uv will need to fetch 3.11.
- `ruff` and `mypy` are on PATH; `gh` is not (CI must be verified by pushing, not locally).
- `.env` contains `DEEPSEEK_API_KEY` and `HF_TOKEN`; a `.gitignore` excluding `.env` was added in
  Phase A so it cannot be committed by accident.
