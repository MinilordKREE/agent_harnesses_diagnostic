# M3 feasibility audit: oracle, replay validation, clustering, corruption (Phase A)

Audited 2026-09-05 against the staged M2 (`third_party/evo-bench` at `e1dc9386…`,
`configs/harness/seed_components.yaml`, `ahd.runner`). Two prerequisites named in the M3 brief
do not exist yet: M2 is staged, not committed, and there is no M2.1 (parallel rollouts and
resume were listed as open gaps in the M2 packet). Neither blocks this audit; parallelism will
matter for replay cost in wall-clock terms (section a.6).

I do not have the HarnessEvolve text. Sections b, e and the clustering rules follow the brief's
description of §3.2 and §3.4; where the paper specifies something different, it wins.

Worked examples below use two live rollouts of `claw-T007zh_todo_management` produced in M2
(normal r1, 7 steps, passed 0.922; reference attempt 1, 5 steps, passed). Both passed, so they
illustrate alignment mechanics, not a real failure.

## a. Prefix replay

**What the seed harness offers.** The loop starts from an empty context every time:
`components.context.initialize(state, system_prompt, user_prompt)` sets `state.messages` to
exactly two messages (`agent/loop.py:52-54`, `agent/components.py:45-49`), then `for step in
range(1, max_steps + 1)` (`loop.py:56`) calls the model on `context.model_messages(state)`
(`loop.py:65-68`), parses tool calls (`loop.py:99`), and executes each action through
`router.execute` (`loop.py:108-146`; shell at `actions.py:44-76`, injected tools at
`actions.py:81-95`). The worker passes the request's `task` dict through untouched
(`worker.py:91, 94`); `ClawRolloutEnv` reads only `claw_public` from it (`claw_runtime.py:237`).
So a harness copy can read replay instructions from an extra task key without any change to
the worker, and our runner already constructs the request.

### (i) Instrumented harness copy

A verbatim copy of the seed tree with one addition in `agent/loop.py` (and a matching hook in
`harness.py` to pass it through): if `task["_ahd_replay"]` is present, the loop

1. sets `state.messages` to `prefix.messages` (the failed run's messages up to and including
   the tool results of step t*-1, with the first user message taken from the failed run, not
   the reference, so the continuation sees exactly what the failed policy saw);
2. sets `state.trajectory` to the failed run's entries for steps 1..t*-1 and `current_step`
   accordingly, so step numbering, `steps` and `max_steps` accounting continue from t*
   (`state.py:73-74` counts assistant entries);
3. **re-executes the prefix's actions** in order, for state only: shell commands through
   `tools.run_shell_command` in the fresh workspace (`tools/shell.py:45-93`), injected tools
   through `injected_tools.dispatch` against the fresh mock services (`injected_tools.py:
   145-175`); the fresh outputs are compared with the recorded `tool_output` and the
   comparison is written to `replay_report.json` in the output dir, but the **recorded**
   outputs stay in the context;
4. at step t* skips the model call and uses the reference's assistant message
   (`tool_calls` or content) as the assistant turn, executes its tool calls fresh, appends
   the observations, and records `substituted: true` on that trajectory entry;
5. from step t*+1 on runs the unmodified loop (real model calls, real execution).

Everything else in the tree is byte-identical to the seed. The instrument is stored under
`ahd/diagnosis/instrument/`, hashed like a snapshot, validated by the same rules (budget keys,
tools, imports), and its hash goes into every `ReplayResult`; it is never a snapshot in any
arm and never a proposer target.

### (ii) Filesystem prefix

Re-executing the prefix's `run_shell_command` calls in a fresh `prepare_task_workspace`
workspace (`tasks.py:64-102`, `inputs/` copied, `outputs/` empty) recreates the filesystem
state the failed run had at t*-1 whenever the commands are deterministic. The trajectory has
every command verbatim (`tool_call.arguments.command`, `loop.py:127-137`) and its recorded
`stdout`, `stderr`, `exit_code` (`shell.py:71-76`).

### (iii) Claw dispatch replay

`claw_dispatches.jsonl` records each injected call as `{tool_name, request_body,
response_status, response_body, tool_use_id, endpoint_url, latency_ms}` (7 entries in the M2
live rollout). The fresh `ClawRolloutEnv` starts services from fixtures and calls
`reset_all()` (`claw_runtime.py:246-249`), so re-issuing the prefix's calls in order through
`injected_tools.dispatch` rebuilds service state; the dispatcher logs the replayed calls to the
new rollout's dispatch log, and `finalize` assembles the Claw trace from the full trajectory
(`claw_runtime.py:278-301`), so the replayed rollout grades like any other. Endpoint URLs
differ per rollout (port offsets, `claw_runtime.py:91-152`) but never reach the model.

### (iv) What cannot be replayed, and detection

| Source of drift | Example in the live data | Detection |
|---|---|---|
| time | `pwd && ls -la && date` at step 1; `date; date -u; env \| grep -iE "date\|ti…"` at step 3; Claw "overdue" judgments when `mock_today` is null | fresh vs recorded output diff after masking ISO dates and clock strings; `mock_today` fixed study-wide (M2) |
| network | `curl` to Serper or any URL | command pattern match (`curl`, `wget`, `pip`, `python -c "import requests`); output diff |
| randomness, pids, temp names | `$RANDOM`, `mktemp`, `ps` | pattern match; output diff |
| model-visible environment | `cat "$EVOBENCH_INJECTED_TOOLS_MANIFEST"` at step 4 (prints per-rollout ports) | output diff, tolerated (ports masked) |
| injected-tool responses | `created_at` uses `datetime.now` (`mock_services/todo/server.py:145`) | response diff after timestamp masking |
| wall clock budget | the failed run had `elapsed` seconds spent by t*-1 | not replayable; the continuation gets a fresh clock, `max_steps` continues from t* |

Rule: compute a normalised diff (mask ISO timestamps, `HH:MM:SS`, epoch floats, pids, ports,
temp paths) between recorded and fresh outputs for every prefix action. Any diff in a
**mutating** action, or in any action whose output the failed policy later quoted, marks the
replay `unreplayable` with the step and diff as the reason. Diffs in read-only actions are
recorded as warnings. A reference action at t* that fails outright because the prefix state
does not support it (missing file, unknown task id) is `inapplicable`, not a verdict.

### Recommendation

Design (i)+(ii)+(iii) as one mechanism, "execute the prefix's actions, keep the recorded
context, substitute at t*, continue", with the drift rule from (iv). Alternatives rejected:
feeding the model the *fresh* prefix outputs (changes what the failed policy saw, so the
counterfactual is no longer about step t*); replaying only the context without executing
actions (GDPval deliverables and Claw state would be missing, the continuation would fail for
the wrong reason); a live proxy that records and replays model calls (not built in M2).

Failure modes: (1) drift undetected by masking, mostly time phrases inside free text
(mitigated by the mutating-action rule and by `mock_today`); (2) a failed run that never
mutated state can still be `unreplayable` if its inspection outputs drifted and it quoted
them; (3) the reference action may carry several tool calls (the live reference issued eight
`todo_update_task` calls in one step); all are substituted; (4) the continuation policy is
stochastic, hence k replays and a pass fraction, never a single verdict; (5) the instrument
diverges from the seed by construction; every result records both hashes; (6) shell commands
that spawn background processes (`&`, `nohup`) leave state the replay cannot reproduce.

### a.6 Cost per replay

From the M2 live rollouts (paper policy config, off-peak): 0.0105 USD for 7 steps, 0.0114 for
10, 0.0212 for 20, i.e. about 0.001 to 0.0015 USD per model call thanks to prefix caching;
judge 0.0026 per call, two calls per Claw grading. A replay costs nothing for the prefix, then
`(F - t* + 1)` model calls plus grading: roughly 0.01 to 0.03 USD and 10 to 20 s per remaining
step. With k = 3 that is about 0.05 to 0.10 USD per validated step; a sufficient-set search
over S candidate steps costs S times that, so S must be bounded (t* plus the later divergences
of the same failure, capped at 4). Wall clock is the binding constraint without M2.1: three
sequential replays of a 10-step continuation take about 10 minutes.

## b. Trajectory alignment and first divergence t*

**Actions.** At step s an assistant entry carries either a list of tool calls (the seed runs
all of them in order, `loop.py:108-146`) or a content-only message. The action at s is the
ordered list of `(name, arguments)` pairs, or `("final", content)`. Two trajectories of the
same task are aligned by step index; the first user message differs only by the reference
block, so step indices are comparable.

**Normalisation.**

| Action | Normalised form |
|---|---|
| `run_shell_command` | `command` with whitespace runs collapsed and stripped; `description` dropped; `timeout_seconds` kept only if set; trailing `;` and `&& true` removed |
| injected tool | name plus arguments parsed as JSON, keys sorted, string values whitespace-collapsed |
| `finish` | `answer` whitespace-collapsed, case-folded |
| content-only | content whitespace-collapsed, case-folded, tagged `final` |

**Finding from the worked example.** Under exact normalised equality the two live runs
diverge at **step 1**: `pwd && ls -la` versus `pwd && ls -la && date`. Every later step also
differs textually although both runs passed. A temperature-1.0 policy does not reproduce
commands verbatim, so exact equality yields t* = 1 for almost every pair and carries no
information. Two definitions are therefore proposed and both recorded:

- `t_exact`: the owner's definition, exact normalised equality, kept for the record.
- `t_class` (recommended for attribution): actions are compared by **class**, and t* is the
  first step whose class sequence differs. Classes: shell **read-only** (no redirection, no
  `cp mv rm mkdir touch sed -i tee python -c … open(…, "w")`, no `curl -o`), shell
  **mutating** with its normalised target paths, injected tool by **name** plus identity
  arguments (`task_id`, `event_id`, `message_id`, …), `finish`, `final`. Within a step the
  multiset of classes is compared; identity arguments must match, other argument values are
  recorded as `argument_variant` but do not make a divergence on their own.

On the worked example `t_class` = **3**: steps 1 and 2 are read-only inspection in both runs;
at step 3 the reference issues eight `todo_update_task` calls while the failed run is still
inspecting (`date; env | grep …`). Divergence type `missing_mutation`. The failed run made
equivalent updates only at step 5, which is exactly the kind of delay a diagnosis should name.

**Divergence types** recorded with t*: `different_action` (same class family, different
target), `missing_mutation` (reference mutates, failed inspects), `extra_mutation`,
`premature_finish` (failed finishes while the reference continues), `late_finish` (reference
finishes at R, failed still acting at R), `no_tool_call` (failed ends with content only),
`error` (failed ends with `model_call_error`), `budget` (failed hits `max_steps` or wall clock
after matching the whole reference prefix), `early` (t* = 1).

**Length cases.** Reference shorter (finished at R): compare steps 1..R; if all classes match,
t* = R with `late_finish`. Reference longer: if the failed run finished at F < R, t* = F with
`premature_finish`; if it ended with an error or budget stop, t* = F + 1 with `error`/`budget`.
Divergence at step 1 is allowed and tagged `early`; the attribution candidates then come from
the context layer (the model's first decision) and the diagnosis is expected to be weaker.
Root-cause priority (§3.4 as described): t* is the diagnosed step; later divergences of the
same failure are listed as `later_divergences` and are candidates for the sufficient set, not
for WHERE.

## c. Component attribution rule table

Deterministic candidate sets from `seed_components.yaml`; the LLM chooses among the
candidates only (`attribution: llm`), or the rule fixes a single candidate (`attribution:
rule`). "State at t*" refers to the failed trajectory's own evidence before t*.

| # | Action type at t* (failed run) | Extra condition | Candidate components |
|---|---|---|---|
| R1 | shell command, different from reference | none | system_prompt, task_prompt, context_window, planner |
| R1a | shell command | a prior observation had `exit_code != 0` or `timeout: true` that the failed run did not react to | observation_shaping, verifier, error_handling |
| R1b | shell command | the observation before t* was truncated (`…[truncated …` in stdout) | observation_shaping |
| R2 | injected tool, different name or identity args | none | tool_router, task_prompt, context_window |
| R2a | injected tool | the failed run never called a tool the reference used at any step | tool_registry, system_prompt |
| R3 | `finish` (premature) | reference continues | completion_policy, verifier, system_prompt |
| R3a | `finish` with an answer that contradicts a tool output | none | verifier, observation_shaping |
| R4 | content-only message (`assistant_no_tool_call`) | none | completion_policy, system_prompt |
| R5 | `model_call_error` | none | error_handling, model_client (rule) |
| R6 | `max_steps` / `rollout_wall_clock_timeout` | none | budget, loop, completion_policy, context_window |
| R7 | shell timeout at t* | none | budget, tool_shell, error_handling |
| R8 | unknown tool name | none | tool_router, tool_registry, system_prompt |
| R9 | `missing_mutation` (reference mutates, failed inspects) | none | planner, system_prompt, context_window |
| R10 | `early` (t* = 1) | none | task_prompt, system_prompt |

`tools_injected` (not patchable) and the observability components are never candidates; a
diagnosis can mention them in WHY but a fix must target a patchable component. `middleware`
and `planner` are empty seams: they appear where the fix is "add a check between decision and
execution" (middleware) or "add an explicit plan" (planner).

## d. Corruption pools by layer distance

ETCLOVG order: execution 0, tooling 1, context 2, lifecycle 3, observability 4,
verification 5, governance 6; distance = index difference. Patchable components per layer:
execution {model_client, tool_shell}; tooling {tool_router, tool_finish, tool_registry};
context {system_prompt, task_prompt, context_window, observation_shaping}; lifecycle {entry,
loop, planner, completion_policy, error_handling, wiring}; observability {trajectory,
usage_accounting, rollout_log, metadata}; verification {verifier}; governance {budget,
middleware}. `tools_injected` is excluded (not patchable).

| Component | d = 0 | d = 1 | d ≥ 2 |
|---|---|---|---|
| model_client, tool_shell | 1 (each other) | 3 (tooling) | 17 |
| tool_router, tool_finish, tool_registry | 2 | 6 (execution + context) | 13 |
| system_prompt, task_prompt, context_window, observation_shaping | 3 | 9 (tooling + lifecycle) | 9 |
| entry, loop, planner, completion_policy, error_handling, wiring | 5 | 8 (context + observability) | 8 |
| trajectory, usage_accounting, rollout_log, metadata | 3 | 7 (lifecycle + verification) | 11 |
| verifier | **0** | 6 (observability + governance) | 15 |
| budget, middleware | 1 | 1 (verifier) | 19 |

Every patchable component has at least one candidate at d ≥ 1 (minimum 1, for budget and
middleware, whose only d = 1 neighbour is verifier). `verifier` has no d = 0 candidate; a
`where` corruption at distance 0 for a verifier diagnosis must fall back to d = 1 and record
that. The sufficient-set exclusion and the "c' active at t'" constraint further shrink these
pools at run time; activity is defined per component (system_prompt, task_prompt,
context_window, model_client, loop: every step; tool_shell: steps with a shell call;
tool_router / tool_registry: steps with any tool call; completion_policy: the final step;
verifier, middleware, observation_shaping: steps with a tool result; budget: the final step
when the exit reason is a budget stop, else every step; error_handling: steps with a failed
tool result or the error step; observability components: every step).

## e. Genuineness judge

Evidence available from a reference trajectory (`trajectory.json` + `metadata.json` +
workspace `outputs/` + Claw dispatch log):

- tool calls made after the first user message, by class (read-only, mutating, injected
  by name) and count; in the worked example 14 calls including 8 `todo_update_task` and a
  written summary file, i.e. the task was executed;
- artifacts: files under `outputs/` (GDPval deliverables), dispatch log entries with
  `response_status < 400` (Claw), curl/search evidence (Search);
- the final answer versus the reference block: for Search the gold string is by construction
  present in both, so copying is unavoidable and genuineness must come from the actions;
- reasoning: whether `reasoning_content` at any step cites the reference *instead of* an
  observation (the live reference run mentioned it at step 3, then performed the updates: use
  as guidance, not substitution);
- score: the reference run must pass the Scorer (already required by the attempt loop).

Rubric (deterministic checks first, then one judge call at temperature 0 with the rendered
trajectory; verdict `genuine`, `shortcut`, or `undetermined`):

| # | Check | Genuine if | Shortcut if |
|---|---|---|---|
| G1 | required actions | Claw: every `tool_called` check in the task's `scoring_components` is satisfied by a dispatch with status < 400; GDPval: at least one deliverable in `outputs/`; Search: at least one information-gathering action (`curl`, search) | none of the required actions occurred |
| G2 | answer derivation | the answer's specific content (numbers, ids, filenames) appears in tool outputs or produced files | the answer's specifics appear only in the reference block |
| G3 | reference dependence | reasoning cites observations for the decisive facts; the reference is used to choose or check | reasoning cites the reference for the decisive facts and no observation supports them |
| G4 | effort | steps ≥ 2 and at least one observation read before finishing | a single step ending in `finish` |

G1 and G4 are computed without a model; G2 and G3 need the judge (arm `diagnosis`, model
`deepseek-v4-pro`, cached). `genuine` requires G1 and G4 to pass and the judge to find G2 and
G3 satisfied; `shortcut` when G1 fails or the judge finds G2 or G3 violated; anything else is
`undetermined`, and undetermined references are not used as oracles.

## f. Identifier stripping for WHY/HOW sentences

**Token set** (derived from the manifest at snapshot time, so it tracks the tree): the 76
manifest tokens (file paths such as `agent/loop.py`, symbols such as `AppendOnlyContext`,
`run_policy_loop`, `TaskAnalyzer.build_prompt`, `TOOL_SCHEMAS`, component ids such as
`context_window`, `harness.json` keys `max_steps`, `rollout_wall_clock_seconds`, `tools`,
`system_prompt`), the two seed tool names (`run_shell_command`, `finish`), the 51 injected Claw
tool names in the validation suite (`todo_update_task`, `calendar_list_events`, …), the
Evo-Bench identifiers (`evobench`, `OpenAICompatibleClient`, `injected_tools`), and the seven
layer names when used as labels. Matching is case-sensitive on word boundaries for
`snake_case`/`CamelCase` identifiers and for paths; replacements are class placeholders:
`[path]`, `[routine]`, `[tool]`, `[component]`, `[setting]`, `[layer]`. Placeholders are
counted so that length matching across arms can pad or trim on non-identifier text.

Worked examples:

1. "The `AppendOnlyContext` in `agent/components.py` hands the model every truncated
   observation, so by step 12 the `run_shell_command` output that mattered was cut at 12 000
   characters" → "The [routine] in [path] hands the model every truncated observation, so
   by step 12 the [tool] output that mattered was cut at 12 000 characters".
2. "`SeedCompletionPolicy.no_actions` treats any content-only reply as the final answer, so
   the policy finished after a `todo_list_tasks` call without updating anything" → "[routine]
   treats any content-only reply as the final answer, so the policy finished after a [tool]
   call without updating anything".
3. "Raise `max_steps` handling in the lifecycle layer: `run_policy_loop` should re-prompt
   before hitting the budget" → "Raise [setting] handling in the [layer] layer: [routine]
   should re-prompt before hitting the budget".

Residual leakage (step numbers, "truncated", "content-only") is by design what the
localization probe (M3 item 8) measures.

## Decisions requested

1. Alignment: adopt `t_class` for attribution with `t_exact` recorded alongside, or keep exact
   equality as the only definition.
2. Replay drift rule: `unreplayable` on any diff in a mutating prefix action or a quoted
   output; warnings otherwise. Masking set as in a(iv).
3. Sufficient-set search bound: t* plus at most 4 later divergences, k = 3, threshold to be
   set (proposal: pass fraction ≥ 2/3).
4. The rule table in c, in particular R9 (missing_mutation → planner, system_prompt,
   context_window) and the exclusion of observability components from WHERE.
5. `verifier` corruption at distance 0 falls back to distance 1 with a flag.
6. Genuineness rubric G1 to G4 and the rule that `undetermined` references are not oracles.
7. Stripping placeholders and the decision to strip layer names.
8. The HarnessEvolve §3.2 and §3.4 text, so the implementation can cite it.
