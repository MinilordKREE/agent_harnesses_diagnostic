# Definitions (M3)

Terms used by the diagnosis modules, and where they depart from HarnessEvolve
(arXiv 2609.00829v1, "HarnessEvolve: Learning from Reference Trajectories for Reliable Agent
Self-Evolution", Jiang et al.).

## Diagnosis fields: WHERE, WHY, HOW

A diagnosis of one failed rollout is `(WHERE, WHY, HOW, severity)`:

- **WHERE** = a harness component id from `configs/harness/seed_components.yaml` plus a step
  index. Components with `where_eligible: false` (observability) are never a WHERE.
- **WHY** = a cause label from the closed taxonomy in `ahd.diagnosis.schema.CAUSE_LABELS` plus
  one mechanism sentence.
- **HOW** = one fix-hint sentence.
- **severity** ∈ {low, medium, high, critical}.

HarnessEvolve's error signal is `(s_i, m_i, h_i)`: severity, cause, fix hint (§3.4). ahd adds
WHERE as a separate field and strips identifiers (paths, symbols, tool names, component ids,
layer names) from WHY and HOW at render time, replacing them with class placeholders, so that
WHERE can only be conveyed through the WHERE field.

## Reference run, genuineness

A **reference run** is a rollout of the same task on the same harness snapshot whose first user
message additionally carries the evaluator's reference (expected answer, rubric text, or
Claw-Eval scoring components) in a fixed block (`configs/harness/reference_block.md`). Up to
`reference_max_attempts` attempts are made and the loop stops at the first that passes the
Scorer (HarnessEvolve §3.2: "attempts up to T_att times; after each attempt, the evaluation
agent verifies whether the produced trajectory is genuine").

A passing reference run is **genuine** when it executed the task rather than restated the
reference (HarnessEvolve §3.3: "invokes appropriate tools, processes intermediate observations,
and arrives at the answer through valid steps rather than trivially restating the provided
answer"). ahd's rubric: G1 required actions executed and G4 effort are computed
deterministically; G2 answer derivation and G3 reference dependence by one judge call. Verdicts
are `genuine`, `shortcut`, `undetermined`; only `genuine` references are oracles.

## Divergence candidates, oracle step

Given the failed and the reference `trajectory.json`, actions are compared step by step. The
**exact** comparison (`t_exact`) uses normalised text; it fires at step 1 for almost every pair
under a temperature-1.0 policy and is recorded only. The **class** comparison (`t_class`)
compares action classes (read-only shell, mutating shell with targets, injected tool by name
and identity arguments, finish, final) and yields the ordered candidate list: class
divergences by step, then steps whose tools and identities match but other arguments differ
(`argument_variant`).

The **oracle step** t* is the earliest candidate that replay validation finds *sufficient*.
HarnessEvolve defines t* as "the earliest step at which the action in τ_i^- deviates from that
in τ_i^+" and lets the optimiser LLM identify it; ahd uses deterministic rules plus replay.

## Replay validation, sufficient set

For a candidate step t, a **replay** recreates the failed run's state up to t-1 (recorded
context verbatim, prefix actions re-executed for filesystem and mock-service state), then
runs two arms of k rollouts each: **substitute** (the reference's action at t, then the policy
continues) and **control** (the policy re-samples at t from the same prefix). The step is
**sufficient** iff the substitute pass fraction is ≥ 2/3 and the control pass fraction is
≤ 1/3 (owner decision 3, following the contrastive logic of CAR's `do_resample` and Credit
Without Ground Truth). The **sufficient set** of a failure is the set of sufficient candidate
steps among the first five candidates. A failure with no sufficient candidate is
`oracle_step: unvalidated` and its cluster is excluded from oracle arms.

A replay is **unreplayable** when a prefix action's fresh exit code differs from the recorded
one, or when a mutating action's output (or an output later quoted by the policy) differs
after masking timestamps, dates, ports, pids, temp paths and the workspace path. Shell
results are compared on stdout and stderr (never on duration). Read-only differences are
warnings.
Unreplayable is a status, never a verdict.

## Failure type (M3.1)

Replay validation classifies every failure (owner decision, M3.1):

| failure_type | evidence | oracle step |
|---|---|---|
| `deterministic` | some candidate is sufficient (substitute ≥ 2/3, control ≤ 1/3) | earliest sufficient step, `oracle_step_basis: sufficient` |
| `stochastic` | no sufficient step and the control arm passes (> 1/3) at every tested candidate: re-sampling from the prefix recovers, the failure was a policy-level random event that the harness let through | the **manifestation step**, i.e. the last class candidate (`no_tool_call`, `premature_finish`, `late_finish`, `error`, `budget` or the last differing action), `oracle_step_basis: manifestation`; the component follows the rule table for that candidate (R3 to R6 in practice) |
| `unrepairable` | at some candidate both arms fail and no candidate is sufficient | none; excluded from oracle arms |
| `unreplayable` | no arm could be scored (prefix drift, infra) | none; excluded from oracle arms |
| `unresolved` (M3.2) | no validated-positive step and some candidate still undecided at the end of the adaptive schedule | none; excluded from oracle arms and never counted as "invalid" for corruption placement |

When `economize` skipped every control arm, one control arm is run at the first insufficient
candidate purely to classify (`classification_control: true`). The distribution of failure
types per source is an E0 finding in its own right. Excluded failures still get a SYSTEM-arm
diagnosis. `ahd diag signal` refuses failures without a replay verdict unless
`--allow-unvalidated` (then `oracle_step_basis: unvalidated`, step = `t_class`).

## Adaptive replay and the unresolved verdict (M3.2)

Fixed k = 3 is replaced by an escalation schedule n ∈ {3, 5, 8, 12} per candidate step, both
arms (substitute, control), same prefix. After each stage the arms give p_sub, p_ctl (unscored
rollouts count against both: p_sub = passes / n, p_ctl = (passes + unscored) / n) and
d = p_sub − p_ctl:

| verdict | rule | confirmation |
|---|---|---|
| validated-positive | p_sub ≥ 0.6 and p_ctl ≤ 0.34 and d ≥ 0.4 | n ≥ 5 |
| validated-negative | p_sub ≤ 0.4 or d ≤ 0.1 | n ≥ 5 |
| escalate | neither | next n |
| `unresolved` | still undecided at n = 12 | final |

Economize: the control arm is skipped only when p_sub ≤ 0.4 at n = 3; validated-negative then
needs n = 5 of the substitute arm alone, and if the substitute recovers (p_sub > 0.4 at n = 5)
the control arm runs from n = 5 on. The sufficient set is the set of validated-positive steps;
the **negative set** the validated-negative steps. Unresolved steps are never oracle steps and
never count as "invalid" when a decoy step is placed (they are neither in the sufficient set nor
in the negative set). A failure with a positive step is `deterministic`; with no positive step
and an unresolved one, `unresolved`; the other types are as in M3.1.

Rationale. At k = 3 a single rollout moves a pass fraction by a third, so "2/3 vs 1/3" is one
sample from a decision boundary; the E0b headroom subset showed such marginal verdicts on about
a fifth of the Claw candidates. The thresholds separate the arms by at least 0.4 (a difference a
harness patch is expected to reproduce), require every verdict to rest on at least five rollouts
per arm, and stop escalating at twelve, where the remaining budget buys little: a candidate that
is still within the undecided band at n = 12 is reported as such rather than forced into a type.
Pre-M3.2 results (fixed k) are read back with verdicts derived from the same thresholds at n = k
and `confirmed: false`; E0d-B re-opens the marginal ones (substitute 2/3, control 1/3 with
substitute ≥ 2/3, or undecided) through the schedule, keeping their rollouts.

## Decoy exclusion (M3.2)

A `where` or coherent-wrong decoy is drawn from the patchable, where-eligible components
outside the **full rule-table candidate set** recorded on the true diagnosis (every component
the attribution rule considered plausible), not merely different from the chosen component.
The pre-M3.2 rule excluded the chosen component only; the E0 report recomputes both and lists
the clusters whose near/far feasibility the stricter rule removes.

## Coherent-wrong arm (COH-WRONG, M3.2)

Arm `coherent_wrong`, corruption `coherent`: a decoy component c' (per decoy exclusion) at a
step s' where c' is active and which is **validated-negative** for the failure; when no
validated-negative step exists the step falls back to one outside the sufficient set and the
assignment records `step_basis: not_sufficient`. The mechanism, fix hint and severity are
generated for c' by the `forced_where` diagnoser variant from the cluster's failed trajectories
only (never the reference), from the controlled vocabulary, and rendered with the same
identifier stripping and template as every other arm. **Plausibility parity**: a blind judge
(`deepseek-v4-pro`, temperature 0, cached) sees the failed trajectories and the rendered REF and
COH-WRONG diagnoses in a seeded random order and scores each 1 to 5; per source the arm is
accepted when the paired mean difference (REF − COH-WRONG) is within ±0.3 and a two-sided
paired sign test is not significant at α = 0.10. On violation the texts are regenerated with a
new variant seed (the judge's scores are never shown to the generator), at most three times; a
persistent violation is recorded, not hidden.

## Privileged-information probe (M3.2)

A blind model receives only one rendered diagnosis plus the mining pool's task ids with
one-line descriptions, the source's tool vocabulary and its answer categories, and guesses the
task (top-1 / top-3), the required tool calls and the answer category. Run per arm
(REF, SELF = system, SHUF = shuffled, COH-WRONG); recovery rates per arm and the REF − SELF gap
per cluster are covariates. Cached, ledgered as arm `probe`.

## Component ambiguity (M3.2)

From the attribution records: the fraction of failures whose component was rule-determined
(candidate set of size one) versus chosen by the model among several candidates, the
candidate-set size distribution, and per cluster the flag `component_unique` (every member's
candidate set had size one).

## Recorded for M4 (owner, M3.2)

- Arms: 11 (COH-WRONG added). The repair-stage budget is identical across all diagnosis arms;
  the end-to-end evolver budget is matched only for REF vs SEARCH-K vs REFINE; both budgets are
  reported separately in every cell record.
- Every accepted patch is evaluated on held-out with 2 independent passes (Claw) so that
  proposal stochasticity and rollout stochasticity are separable; analysis uses a two-way
  (cluster × held-out task) bootstrap.

## Opaque shell actions (M3.1)

A shell command that runs an interpreter, a script or a converter (`python3 build.py`,
`bash run.sh`, `soffice --convert-to`) is class `shell_opaque`: whether it writes files
cannot be read off the command line. Alignment compares opaque actions by normalised command
and does not derive `missing_mutation` / `extra_mutation` when either side is opaque. The
ground truth of mutation is observed at replay: the instrument hashes the workspace tree before
and after every prefix shell action (`mutating_observed`), and the drift rule uses that flag,
not the regex prior (`mutating_prior`).

## Cause vocabulary (M3.1)

WHY labels come from `configs/prompts/diagnosis/causes.yaml` (15 ids seeded from
HarnessEvolve's examples, Harness-Bench's failure modes and HarnessFix's layered view) or the
escape hatch `other:<short text>`; the model may not invent labels. Clustering keys on the
label.

## Clusters

Failures are grouped by (cause label, WHERE component). The representative is the member with
the highest severity, then the earliest oracle step. Single-member clusters are kept
(HarnessEvolve §3.4: "Preserve single-member clusters"). Membership is hashed into the run
manifest (`diagnosis.clusters_sha256`).

## Rendering (M3.1)

No filler. For each cluster and field the cap is the longest identifier-stripped text among
the arms of that seed; longer texts are trimmed at a word boundary, shorter ones are left as
they are. Character counts per field per arm are written to the assignment table
(`rendered_lengths`) and to `rendered.json`; the caps to `caps.json`. Length is analysed as a
covariate in M6.

## Corruption and distance

For a cluster, an arm and a seed, `ahd.diagnosis.corrupt.assign` draws deterministically:

- `corrupt_where_near`: another component in the **same layer**; `corrupt_where_far`: a
  component in a **different layer**; step t' active for that component and outside the
  sufficient set. An empty pool falls back to the other tier with `distance_fallback: true`.
  Distance is recorded as two covariates, `same_layer` and `same_file`; ETCLOVG index
  differences are not used (owner decision 5).
- `corrupt_why`: another cluster's cause and mechanism; `corrupt_how`: another cluster's fix
  hint; `shuffled`: another cluster's whole diagnosis; `coherent_wrong`: see above (M3.2).
- Coincidence exclusion: a corrupted value always differs from the true one; when impossible
  the assignment says so.

## Departures from HarnessEvolve, in one place

| Topic | HarnessEvolve | ahd |
|---|---|---|
| divergence point | judged by the optimiser LLM | deterministic class rules + replay validation |
| diagnosis fields | severity, cause, fix hint | plus WHERE (component, step); WHY/HOW identifier-stripped |
| clustering key | error cause | (cause label, component); membership hashed |
| reference verification | evaluation agent | G1/G4 deterministic + G2/G3 judge; `undetermined` never an oracle |
| candidate generation, quality gate | part of the loop | out of scope (M4) |
