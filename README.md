# ahd: agent harnesses diagnostic

Infrastructure for reproducible experiments on LLM agent harnesses: typed run configs, a
DeepSeek client with explicit retry, opt-in response cache and a per-run cost ledger, and run
directories that record enough (git sha, config hash, pricing version) to be re-interpreted
later.

M0 ships the scaffold, the LLM client layer and the run-context primitives. M1 adds the
task substrate on Evo-Bench: loader, stratified sampling, and judge and scorer adapters. M2
adds the harness model (snapshots, component manifest, validation) and the runner that drives
Evo-Bench's policy worker per rollout. M3 adds diagnosis: reference genuineness, trajectory
alignment, replay validation of the divergence step, error signals, clustering, deterministic
corruption arms and a leakage probe (`docs/DEFINITIONS.md`). Proposal logic comes in M4.

**Data use.** Evo-Bench data (CC-BY-4.0, with upstream terms) is used here exclusively for
the evaluation of harness evolution. It is never used for training, fine-tuning or parameter
fitting, never copied into this repository, and never crawled. See
`docs/reuse/evobench.md` section f.

## Layout

```
configs/            pricing.yaml (versioned DeepSeek prices), runs/*.yaml (RunConfig instances)
docs/               CONVENTIONS.md, reuse/ (reference survey, per-module provenance, permissions)
src/ahd/            the package
  cli.py            ahd version | ahd llm ping | ahd runs new
  errors.py         InfraError / TaskFailure (+ BudgetExhausted) / ConfigError
  settings.py       secrets from .env via pydantic-settings
  logs.py           stdlib logging with a JSON-lines formatter
  core/             hashing, atomic io, config models, run context, manifest, trace writer, environment probe
  llm/              types, Provider protocol, DeepSeek client, retry, cache, pricing, ledger
  tasks/            Evo-Bench loader, stratified sampling, judge and scorer adapters, metrics
  harness/          snapshots (hash, parent, diff), component manifest with line spans, patch apply, validation
  runner/           RunSpec, Runner over Evo-Bench's worker, trajectory events, reference mode, summary
  diagnosis/        schema, align, attribution, replay (+ instrument/ copy of the seed), genuineness, signal, cluster, corrupt, leakage, pipeline
configs/harness/        seed_components.yaml (the WHERE vocabulary), reference_block.md
configs/prompts/diagnosis/  diagnosis template, reference/system signal, genuineness and leakage prompts, causes.yaml
third_party/evo-bench   git submodule (Apache-2.0), imported as the `evo-bench` path dependency
external/claw-eval      (gitignored) Claw-Eval checkout made by `make setup-claw`
tests/unit/         offline; the provider is a fake transport
tests/integration/  real provider; @pytest.mark.integration
runs/               (gitignored) one directory per run
```

## Setup

```
cp .env.example .env      # DEEPSEEK_API_KEY, HF_TOKEN, SERPER_API_KEY
git submodule update --init third_party/evo-bench
make setup                # installs uv if missing, uv sync --locked, pre-commit install
make setup-claw           # Claw-Eval checkout for the General domain (external/claw-eval)
uv run ahd tasks fetch    # Evo-Bench snapshot into the Hugging Face cache (needs HF_TOKEN)
sudo apt-get install -y --no-install-recommends libreoffice   # GDPval rendering; version lands in the manifest
```

## Running tests and checks

```
make test                 # offline unit tests
make lint typecheck       # ruff, ruff format --check, mypy --strict
make check                # all of the above
make test-integration     # one real DeepSeek call; needs DEEPSEEK_API_KEY
```

## CLI

```
uv run ahd version
uv run ahd runs new --config configs/runs/example.yaml
uv run ahd llm ping --config configs/runs/example.yaml [--cache]
uv run ahd tasks list --split validation --domain office
uv run ahd tasks show <task-id> [--show-gold]
uv run ahd tasks sample --n 20 --seed 0
uv run ahd harness snapshot --from third_party/evo-bench/policy_harness_seed
uv run ahd harness components <snapshot_id>
uv run ahd harness diff <a> <b>
uv run ahd run --harness <snapshot_id> --tasks claw-T007zh_todo_management --replicates 2 --mode normal --workers 4
uv run ahd run resume <run_id> [--workers 4]
uv run ahd run summarize <run_id>
uv run ahd diag reference <reference_run_id>                      # genuineness verdicts
uv run ahd diag align <run_id> --reference-run <reference_run_id>
uv run ahd diag replay <run_id> --reference-run <reference_run_id> --k 3 --max-candidates 5
uv run ahd diag signal <run_id> --reference-run <reference_run_id> [--allow-unvalidated]
uv run ahd diag cluster <run_id> --reference-run <reference_run_id>
uv run ahd diag corrupt <run_id> --seed 0 [--arm corrupt_where_near]   # all arms' tables first, then rendering
uv run ahd diag leakage <run_id>
uv run ahd diag cost <run_id>
```

`ahd llm ping` makes one real call, writes a run directory under `runs/ping-*/` with
`manifest.json`, `ledger.jsonl` and `trace.jsonl`, and prints token counts, latency, the
pricing tier and the usd charged.

See `docs/CONVENTIONS.md` for the rules every module follows and `docs/reuse/` for where the
code came from.
