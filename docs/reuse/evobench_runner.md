# Evo-Bench seed harness and runner audit (M2, Phase A)

Audited 2026-09-05 against `third_party/evo-bench` at `e1dc9386…` (Apache-2.0) and the
Claw-Eval checkout `external/claw-eval` at `d3f02d49…` (marker file
`.evobench-upstream-commit`; the directory is an extracted tarball, so `git rev-parse` inside it
reports *our* repo's HEAD, which is not its commit). Paths are relative to the submodule unless
prefixed `claw-eval/`. The draft component manifest is `configs/harness/seed_components.yaml`.

## a. Seed harness anatomy

**Files and entry points** (`policy_harness_seed/`, 776 lines, 12 files; no third-party imports
beyond Evo-Bench's own `evobench.models.client` and `evobench.policy.injected_tools`):

| File | Lines | Entry points |
|---|---|---|
| `harness.py` | 50 | `PolicyHarness(harness_dir, model_config)` (`:15-27`), `run_task(task, task_workspace, output_dir, harness_revision, model_config_id, command_timeout_seconds=120)` (`:29-50`) |
| `harness.json` | 11 | `name`, `version`, `system_prompt`, `max_steps: 300`, `rollout_wall_clock_seconds: 3600`, `tools: [run_shell_command, finish]` |
| `system_prompt.md` | 15 | CodeAct rules; workspace discipline; "Use `finish` when the task is done" |
| `agent/loop.py` | 147 | `run_policy_loop` (`:27-147`), `_stop_for_wall_clock` (`:14-24`) |
| `agent/state.py` | 139 | `RolloutState` (`:29`), `PolicyRolloutResult` (`:14`), `create` (`:52`), `add_usage` (`:80`), `finalize` (`:88`) |
| `agent/actions.py` | 105 | `Action` (`:15`), `ToolRouter.schemas/parse/execute/_parse_args` (`:22-105`) |
| `agent/components.py` | 116 | `TaskAnalyzer`, `PassivePlanner`, `AppendOnlyContext`, `IdentityMiddleware`, `AcceptAllVerifier`, `SeedCompletionPolicy`, `HarnessComponents`, `build_default_components` |
| `tools/__init__.py`, `tools/shell.py`, `tools/finish.py`, `tools/_util.py` | 181 | `TOOL_SCHEMAS` (`__init__.py:16`), `run_shell_command` (`shell.py:45`), `finish_result` (`finish.py:32`), `decode`/`truncate` (`_util.py:13,27`) |
| `agent/__init__.py` | 12 | re-exports |

**The step loop** (`agent/loop.py:56-146`). For `step in 1..max_steps`: check wall clock
(`:59-61`); call `components.model.create(messages=context.model_messages(state),
tools=router.schemas())` (`:65-68`); any exception ends the rollout with
`exit_reason="model_call_error"` (`:69-78`); append the assistant message to the context and
a trajectory entry with per-step `token_usage`, `model_call_seconds`, `completed_at` (`:82-91`);
parse tool calls (`:99`); none means `SeedCompletionPolicy.no_actions` ends the rollout with
`assistant_no_tool_call` and the content as the answer (`:100-105`); otherwise for each action:
wall-clock check (`:109-116`), `middleware.before`, `router.execute` (shell timeout clamped
to the remaining budget, `actions.py:45-48`), `middleware.after`, `verifier.verify`,
`context.tool` (`:117-126`), trajectory entry (`:127-137`), `completion.after_action`
(`finish` accepted ends with `finished`, `components.py:89-90`). Loop end without a stop
leaves the default `exit_reason="max_steps"` (`state.py:47`). `finalize` writes
`trajectory.json` and `metadata.json` (`state.py:88-119`).

**How the model is called.** Hard-wired, not injectable by config: `harness.py:7,26`
`self.model = OpenAICompatibleClient(model_config)`; `create_model_client` and the
`provider` field are never consulted on the policy path. The `ModelConfig` arrives from the
worker request (`worker.py:89`), whose `api_base` is a **literal** resolved host-side by
`ModelConfig.from_path` from `api_base_env` (`evobench/models/client.py:49-58`) and serialised
by `to_dict()` (`:39-47`). Only the API key is read from the environment inside the worker
(`client.py:89`). The request sent is `model, messages, max_tokens` plus `temperature`,
`reasoning_effort`, `extra_body` when set, and `tools`/`tool_choice="auto"` when tools are
present (`client.py:109-133`); the openai SDK client has `timeout=config.timeout_seconds`
and default `max_retries` (`:103-107`). Assistant messages are stored with
`model_dump(mode="json", exclude_none=True)` (`client.py:539-547`), so DeepSeek's
`reasoning_content` is kept in `messages` and in the trajectory.

**Tool wiring.** Exactly two tools: `run_shell_command` (bash via `subprocess.Popen(shell=True,
cwd=workspace, start_new_session=True)`, no `env=`, so the worker environment is inherited;
`shell.py:61-68`) and `finish` (`finish.py`). Everything else is dispatched by name to
`evobench.policy.injected_tools.dispatch` (`actions.py:81-83`), which is empty unless
`EVOBENCH_INJECTED_TOOLS_MANIFEST` names a manifest (`injected_tools.py:49,57-78`).

| Domain need | In the seed | Where |
|---|---|---|
| search | **no search tool**; Serper is reachable only if the policy `curl`s `https://google.serper.dev/search` itself with `SERPER_API_KEY`, which the adapter copies into the worker env (`adapter.py:381`) | policy shell |
| fetch | **no fetch tool**; `curl`/`python` in the shell. The only fetch code in the repo is the evolver's `web_visit` (`evolution/tools/webvisit.py`), redirectable by `WEB_BROWSE_ENDPOINT` | evolver only |
| office | shell; `python3` in the worker env is the venv interpreter, so `openpyxl`, `python-docx`, `python-pptx`, `PyMuPDF` are importable; LibreOffice only matters for scoring | policy shell |
| Claw | injected HTTP tools from `claw_public.tool_schemas`, dispatched through `claw_eval.runner.dispatcher.ToolDispatcher` (`httpx`, 30 s) to the per-rollout mock services (`claw_runtime.py:187-236`, `injected_tools.py:145-175`) | runner-injected |

**Base URLs from env.** Model: resolved host-side from `api_base_env` (above). Serper:
module constant `https://google.serper.dev/search` in the evolver tool (`websearch.py:16`);
**no** `SERPER_ENDPOINT`/`SERPER_API_BASE` exists anywhere. Browse: `WEB_BROWSE_ENDPOINT` /
`BROWSE_ENDPOINT` (`webvisit.py:25-30`). No proxy variables are injected by the adapter, but in
local mode the worker inherits the full host environment (`adapter.py:838-845`, no `env=`).

**Limits.**

| Limit | Value | Where |
|---|---|---|
| max steps | 300 | `harness.json`; read `harness.py:22` (default 12) |
| rollout wall clock | 3600 s | `harness.json`; enforced `loop.py:59-61, 109-116` |
| per shell command | 120 s default, model may pass `timeout_seconds`, clamped to remaining wall clock | `harness.py:37`, `actions.py:45-48` |
| observation size | 12 000 chars, middle-truncated | `tools/_util.py:27` |
| model call timeout | `ModelConfig.timeout_seconds` (paper config 600) | `client.py:106` |
| model output cap | `max_output_tokens` (paper config 65 536) | `client.py:122` |
| worker deadline (host) | `max(max(300, timeout×20), wall_clock + timeout + 300)` = 12 000 s for the paper config | `adapter.py:898-919` |
| tokens | **none**; usage is only summed | `state.py:80-86` |

## b. Draft component manifest

`configs/harness/seed_components.yaml` (23 components, 12 flagged `ambiguous`). Summary:

| id | layer | files | role |
|---|---|---|---|
| entry | lifecycle | harness.py | construct from harness.json, build components, expose run_task |
| budget | governance | harness.json, harness.py, loop.py, actions.py | max_steps, wall clock, command timeout default and clamp |
| model_client | execution | harness.py, loop.py | OpenAI-compatible chat call with tool schemas |
| system_prompt | context | system_prompt.md | fixed instructions |
| task_prompt | context | components.py:TaskAnalyzer | first user message (id, workspace, prompt, public files) |
| context_window | context | components.py:AppendOnlyContext, state.py:messages | append-only transcript, no compaction |
| observation_shaping | context | tools/_util.py | decode + 12k truncation of tool output |
| loop | lifecycle | loop.py | the step loop |
| planner | lifecycle | components.py:PassivePlanner | no-op observer seam |
| completion_policy | lifecycle | components.py:SeedCompletionPolicy | finish -> finished; no tool call -> answer |
| tool_router | tooling | actions.py:ToolRouter | schemas, parse, dispatch |
| tool_shell | execution | tools/shell.py | bash in workspace |
| tool_finish | tooling | tools/finish.py | record answer |
| tool_registry | tooling | tools/__init__.py, harness.json:tools | ordered schema list |
| tools_injected | tooling | (outside the tree) injected_tools.py | Claw/APEX tools merged by name |
| middleware | governance | components.py:IdentityMiddleware | before/after hooks, identity |
| verifier | verification | components.py:AcceptAllVerifier | accept-all |
| trajectory | observability | state.py, loop.py | per-step entries, trajectory.json |
| usage_accounting | observability | state.py:add_usage | token sums |
| rollout_log | observability | state.py | rollout.log |
| metadata | observability | state.py:finalize | metadata.json, PolicyRolloutResult |
| error_handling | lifecycle | loop.py, actions.py | model_call_error exit; shell timeout and unknown tool as runtime_errors; no retry |
| wiring | lifecycle | components.py, agent/__init__.py | HarnessComponents, build_default_components |

**Ambiguous seams to settle before M3 uses the vocabulary:**

1. `budget` values live *inside* the harness tree (`harness.json`), so a proposer edit can
   change the budget. Proposed rule: budget keys are governance-owned; the runner refuses a
   snapshot whose `harness.json` budget keys differ from the frozen values (section i).
2. `tool_router.execute` is dispatch (tooling) and shell execution with timeout clamping
   (execution, governance) in one method; `tools/shell.py` is schema (tooling) plus subprocess
   (execution) in one file. Manifest assigns the file to one layer and lists both halves.
3. `observation_shaping` sits in `tools/` but decides what the model sees; assigned to context.
4. `planner` and `middleware` are empty seams; whatever a proposer puts there decides the
   layer. Manifest keeps their nominal layer and notes it.
5. `tools_injected` and `model_client`'s class live outside the harness tree: diagnosable,
   not patchable.
6. `harness.json:tools` is declarative only; code reads `TOOL_SCHEMAS`. A proposer editing one
   without the other creates a silent inconsistency worth a governance check.
7. `context_window`: store in `state.py`, policy in `components.py`.
8. `error_handling` has no retry; adding one touches lifecycle and governance.

## c. What the runner writes per rollout

Per (task, trial) under `rollouts/<task_id>[/trial_N]/` (`runner.py:112-117`):

| File | Writer | When |
|---|---|---|
| `_policy_worker_input.json` / `_policy_worker_output.json` | `adapter.py:819-833`, `worker.py:50-51` | before/after the worker |
| `rollout.log` | `state.py:59-61, 76-78` | **incremental**, one line per event with `[+elapsed]` |
| `trajectory.json` | `state.py:98-104` | **only in `finalize`** |
| `metadata.json` | `state.py:99-119` | only in `finalize` |
| `score.json` (public view) | `runner.py:176, 539-559` | after scoring |
| `hackle_violation.json` | `runner.py:138-140` | on violation |
| Claw: `injected_tools_manifest.json`, `claw_dispatches.jsonl`, `claw_trace.jsonl` | `claw_runtime.py:184-185, 227-236, 278-301`; `injected_tools.py:166-170` | manifest at start, dispatches incrementally, trace in `finalize` |

Workspaces: `task_workspaces/<task_id>[/trial_N]/` with `inputs/` copies and `outputs/`
pre-created (`tasks.py:64-102`); `prepare_task_workspace` **deletes** an existing workspace
(`tasks.py:69-70`). Split level: `result.json` (`runner.py:348`).

**Trajectory structure** (`state.py:100-104`): `{rollout_id, messages, trajectory}`.
`messages` is the OpenAI transcript (system, user, assistant dicts incl. `tool_calls` and
`reasoning_content`, `{"role":"tool","tool_call_id","content"}`). `trajectory` entries:
assistant `{step, role, message, model_call_seconds, token_usage, completed_at}`
(`loop.py:84-91`); tool `{step, role:"tool", tool_call:{id,name,arguments}, tool_output,
completed_at}` (`loop.py:127-137`), where `tool_output` is the full result dict (`content`,
`stdout`, `stderr`, `exit_code`, `duration_seconds`, `timeout`). **Step-level alignment of two
trajectories is possible**: shared `step` index, `tool_call.id`, per-step usage and durations.
Missing: a per-entry start timestamp (only `completed_at`; start is derivable from
`model_call_seconds`), per-tool-call usage (none, tools are local), and `started_at` (in
`rollout.log` only).

**metadata.json** (`state.py:105-119`): `rollout_id, task_id, task_domain,
policy_harness_revision, model_config_id, duration_seconds, exit_reason, final_answer,
artifact_path, runtime_errors, token_usage, log_path, steps`.

**Token usage** comes from the provider's `usage` block per call, normalised by `usage_to_dict`
(`client.py:459-543`: `prompt_tokens, completion_tokens, total_tokens, cached_tokens,
cache_creation_input_tokens, effective_context_input_tokens`), summed by `add_usage`
(`state.py:80-86`); per-step values survive **only** in `trajectory.json`; `metadata.json`,
the worker output and `result.json` hold sums (`runner.py:270-275, 434-441`).

**Crash and timeout behaviour.** `finalize` never runs on a worker crash or host timeout, so
no `trajectory.json`; the adapter synthesises an empty one plus `worker_stdout/stderr`
(`adapter.py:1056-1067`) and keeps the last 20 000 chars of `rollout.log` in
`worker_diagnostics.rollout_log_tail` (`:951-952`). Because the host deadline (12 000 s)
exceeds the harness wall clock (3 600 s), the harness's own stop fires first in the normal
case and `finalize` runs; only hard crashes lose the trajectory. **No hook is needed** for the
normal path. For the crash path the fallback is to parse `rollout.log`, which records per step
"model returned in Xs, tokens=T (prompt=P, completion=C)" and each shell command and exit
code (`loop.py:92-97`, `actions.py:71-75`); M2's runner should reconstruct a degraded
`trajectory.jsonl` from it and mark it `partial=true`. Incremental trajectory writing would
need a harness edit and is ruled out.

## d. Gold and reference channel

`public_task_view` keeps only `id, domain, prompt, public_files, claw_public, apex_public`
(`tasks.py:106-133`); the runner calls the worker with that view (`runner.py:120-126`) and the
worker embeds it verbatim (`adapter.py:421, 824`). **There is no runner flag or task field that
exposes the gold to the policy**; `ahd tasks show --show-gold` is an owner-facing print only.

A reference run needs no harness edit because *our* runner will build the worker request
itself: the `task` field is whatever we pass, and the harness reads only `id`, `prompt`,
`public_files` from it (`components.py:25-36`). Proposed construction (my reading of the
HarnessEvolve §3.2 idea, "the same harness with the reference visible"; owner to confirm the
exact contract):

- Start from `Task.public_view()`; append a delimited block to `prompt`:
  `\n\n=== REFERENCE (visible only in reference mode) ===\n<gold>\n=== END REFERENCE ===`.
- `<gold>` per source: BrowseComp/HLE `scorer.expected`; GDPval the rubric criteria text
  (their `rubric` list, scores and criteria), since no gold deliverable is shipped
  (`gold_file_provenance.redistributed = false`); Claw the declarative `scoring_components`
  and `safety_checks` from `tasks/<id>/task.yaml` in the checkout (the executable
  `grader.py` is not shown).
- Everything else identical: same snapshot, same budget, same model config. The trajectory
  is tagged `mode=reference`; the first user message differs only by the block, so step
  alignment with a normal run is unaffected.
- Alternative kept for the record: put the reference into `inputs/REFERENCE.md` and list it
  in `public_files`. Rejected as the default because the model must choose to read it.

## e. Claw-Eval: three rollouts

- **Aggregation is strict all-pass.** `runner.py:490-494`: `pass_hat_k = all(passes) and
  len(passes) == trials`, `pass_at_k = any(passes)`, `passed = pass_hat_k`, `score =
  mean(trial scores)`. Claw-Eval's own README calls the primary metric Pass^3, "consistently
  pass a task across three independent trials" (`claw-eval/README.md:29`); its library form is
  `(c/n)^k` (`claw-eval/src/claw_eval/models/scoring.py:32-50`), but Evo-Bench uses the boolean.
  Per-trial pass is `task_score >= 0.75` (`scoring.py:28-29`), with
  `task_score = round(safety × (0.80·completion + 0.20·robustness), 4)` (`scoring.py:11-25`);
  the `M=` term printed in the reason string never enters the score.
- **Mock services per rollout.** `worker.py:107-111` wraps each rollout in `ClawRolloutEnv`;
  `_start` leases a port offset (stride 100, 250 slots, PID-tagged lock files under
  `$TMPDIR/claw_port_leases`, `claw_runtime.py:52-54, 91-126`), spawns each service as a
  subprocess of the worker's `sys.executable` (`claw-eval/src/claw_eval/runner/services.py:
  99-135`, proxy env vars stripped `:108-112`, `MOCK_TODAY` injected `:114-116`), waits for
  health (30 s), calls `reset_all()`; `__exit__` terminates the processes and releases the
  lease (`claw_runtime.py:251-268`). Each of the three trials gets a fresh workspace, a fresh
  service process and a fresh offset (`runner.py:113-119`). The todo service keeps state in
  module globals and only reads its fixture file (`claw-eval/mock_services/todo/server.py:
  28-41`), so trials cannot contaminate each other.
- **Determinism.** Injected errors are off (`ERROR_RATE` default `"0"`, `mock_services/_base.py:
  32`; T007zh sets no env). `mock_today` is `null` for T007zh, so "overdue" is judged against
  the real clock while fixtures are dated 2026-02/03: results **drift with the calendar**. The
  Claw judge runs at temperature 0 with two calls per grading and 5 retries
  (`llm_judge.py:85-136`); through our patch it is cached per artifact. Recommendation:
  record the run date in the manifest (already there via `created_at`) and do not set
  `MOCK_TODAY`, to stay faithful to the benchmark; revisit if drift shows up across weeks.

## f. Web freeze

Facts: the policy has no web tool; all web access is `curl`/`python` inside the shell, which
inherits the worker environment (`shell.py:61-68`); the local worker inherits the full host
environment (`adapter.py:838-845`); Serper's URL is a constant in evolver code and there is no
policy-side redirection variable; `requests`, `httpx` (openai SDK, Claw dispatcher) and `curl`
all honour `HTTP_PROXY`/`HTTPS_PROXY`/`NO_PROXY` by default (`websearch.py:12`, `client.py:
103-107`, `claw-eval/.../dispatcher.py`).

**Recommendation: an environment-level record/replay HTTPS proxy, no harness edit.** M2's
runner sets, in the worker environment only: `HTTP_PROXY`/`HTTPS_PROXY` (and lowercase) to a
local proxy, `NO_PROXY=127.0.0.1,localhost,<model API host>`, and the CA-bundle variables
clients read (`CURL_CA_BUNDLE`, `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`)
pointing at the proxy's CA. The proxy keys entries by `(method, host, path, canonical body)`
under a `web_snapshot_id` shared by an experiment family, serves hits from disk, records
misses, ledgers every Serper request as a `search` row (host `google.serper.dev`) and every
other request as a `fetch` hit/miss, and refuses new misses when the snapshot is frozen
(`InfraError`, never a fabricated page). Model calls bypass it via `NO_PROXY`; Claw tool calls
to the mock services bypass it the same way. Engine: `mitmproxy` as a library with an ahd
addon (mature CONNECT/TLS handling, a new dependency) rather than a hand-rolled MITM. Clients
that ignore the CA variables fail TLS loudly, which is the correct outcome. Rejected
alternatives: an injected `web_search` tool through `EVOBENCH_INJECTED_TOOLS_MANIFEST`
changes the tool surface the policy sees (a different harness), and a Serper base-URL variable
does not exist on the policy side. Not available in E2B mode (loopback is denied, `e2b.py:
114-136`), which we do not use.

## g. Live policy-token ledgering

**Possible without editing the harness.** The worker request carries a literal `api_base`
(`worker.py:89`, `client.py:39-58`), so our runner can set it to a local OpenAI-compatible
endpoint backed by `ahd.llm.DeepSeekClient`, with the attribution (run, arm, task, replicate)
encoded in the URL path so every call is ledgered live and cached per replicate. Costs:
`ahd.llm` must learn tool schemas, assistant `tool_calls` and `tool` messages (the seed uses
function calling), and the proxy must preserve the harness's failure semantics by running with
one attempt (their client never retries; a proxy that retried would change the failure
profile). Benefit beyond accounting: token or USD budgets can be enforced live by returning an
error to the harness, which ends the rollout with `model_call_error` that the runner maps to
`BudgetExhausted`.

**Recommendation:** implement the live proxy in M2 (it is the same shape of component as the
web proxy and the only route to budget enforcement), and reconcile post hoc against
`trajectory.json` per-step `token_usage` (`loop.py:89`): the two sums must agree or the
rollout is an `InfraError`. If the owner prefers to defer, post hoc from the trajectory is
exact per step and needs no proxy; the ledger row kind `policy` is the same either way.

## h. Proposed run layout

```
runs/<run_id>/
  manifest.json            v3: + harness_snapshot_id, web_snapshot_id, run_spec (mode, replicates, budget)
  config.resolved.yaml
  harness/<snapshot_id>/   copy of the harness tree; snapshot.json {sha256, parent, provenance, created_at, diff_path}
                           snapshot_id = sha256_dir()[:12]; seed = verbatim copy of policy_harness_seed
  rollouts/<task_id>/<replicate>/
    trajectory.jsonl       M0 envelope; kinds: rollout_start, model_call, tool_call, observation, final, rollout_end
                           payload carries step, tool_call id, usage, durations, mode; partial=true if rebuilt from rollout.log
    trajectory.json        Evo-Bench's own file, untouched (scorer and Claw trace read it)
    metadata.json          Evo-Bench's own
    rollout.log, claw_*.jsonl, injected_tools_manifest.json   Evo-Bench's own
    artifacts/             the workspace outputs/ copied after the rollout (deliverables)
    score.json             M1 Score (passed, value, reason, scorer, task_failure, judge_meta, artifact_sha256)
    failure.json           FailureRecord when not passed: task id, replicate, trajectory path, score, reason, mode, error family
  workspaces/<task_id>/<replicate>/   live workspaces (deleted or kept per config)
  summary.json             per source, per task: replicate results, pass^k and pass@k, mean value; infra/task/budget counts
  ledger.jsonl             call (judge), policy (per rollout or per call), search, fetch, infra_*, task_failure
  trace.jsonl, log.jsonl   run-level M0 trace and JSON log
```

Replicate ids are `r<k>`; `harness_snapshot_id` and `web_snapshot_id` are in the manifest;
the seed snapshot is hashed with `sha256_dir` over the copied tree so it equals Evo-Bench's own
`hash_harness` input set (`runner.py:562-573`, which truncates to 16 hex; we keep the full
digest and record their 16-char form as `evobench_revision` for cross-reference). Local-mode
caveat from the runner audit: `score.json` of an earlier replicate is host-readable by a later
replicate's shell if it leaves its workspace; keep `rollouts/` and `workspaces/` as siblings
and write scores after all replicates of a task complete.

## i. Budget parameters to freeze

| Parameter | Frozen value | Source |
|---|---|---|
| max steps per rollout | 300 | `harness.json:max_steps`; paper §5.1.1 |
| rollout wall clock | 3 600 s | `harness.json:rollout_wall_clock_seconds`; paper "one hour" |
| shell command timeout default | 120 s (model may request less; clamped to remaining wall clock) | `harness.py:37`, `actions.py:45-48` |
| observation truncation | 12 000 chars | `tools/_util.py:27` |
| policy model | `deepseek-v4-flash`, temperature 1.0, `reasoning_effort: max`, `max_output_tokens` 65 536, `timeout_seconds` 600, context 256k | `configs/paper/policy_deepseek_v4_flash.json`; paper §5.1.2 |
| host worker deadline | 12 000 s (derived) | `adapter.py:898-919` |
| trials | Claw 3, others 1 | README:225; `runner.py:36-43` |
| judge | `deepseek-v4-pro`, temperature 0, cached | M1 decision (paper: Qwen3.7-Plus, temperature 0) |
| tokens / USD | none in the benchmark; ahd adds a per-rollout USD cap only as an explicit experiment parameter, default off | this audit |

Governance rule for M2: the runner asserts that a snapshot's `harness.json` `max_steps` and
`rollout_wall_clock_seconds` equal the frozen values before running, and records the frozen
budget in the manifest; a snapshot that changes them is rejected, not silently re-budgeted.
Cost note: `reasoning_effort: max` at up to 300 steps is the paper's condition; the smoke run in
M1 used `low` and 7 steps for about a cent, so the paper condition is materially more
expensive per rollout and should be measured on the first two-replicate live run.

## j. Phase B addenda (2026-09-05)

- **Worker SDK retries, verified:** Evo-Bench's `OpenAICompatibleClient` builds
  `OpenAI(api_key, base_url, timeout)` without `max_retries` (client.py:103-107); the installed
  openai 3.8.0 default is `DEFAULT_MAX_RETRIES = 2`, so each policy model call is retried up
  to twice inside the worker before the harness sees `model_call_error`. Recorded in every
  manifest as `environment.policy_sdk_max_retries`.
- **Policy tokens** are ledgered post hoc from `trajectory.json` per-step usage (one `policy`
  row per rollout) and reconciled against `metadata.json`; the live proxy is deferred and
  `PolicyModelSpec.api_base` stays settable for it.
- **Reasoning tokens per step are not recoverable** from Evo-Bench's data: `usage_to_dict`
  drops `completion_tokens_details` (client.py:459-537). The runner records
  `reasoning_present` and `reasoning_chars` per model call from `reasoning_content` instead;
  DeepSeek bills reasoning inside `completion_tokens`, so cost is still exact.
- **Component manifest approved** as `configs/harness/seed_components.yaml` with `patchable`
  and anchored symbols (`path:Qualname@text`) so `locate` resolves to statements, not whole
  functions, where two components share a function.
