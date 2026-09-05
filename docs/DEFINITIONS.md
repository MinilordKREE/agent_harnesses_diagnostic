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

## Clusters

Failures are grouped by (cause label, WHERE component). The representative is the member with
the highest severity, then the earliest oracle step. Single-member clusters are kept
(HarnessEvolve §3.4: "Preserve single-member clusters"). Membership is hashed into the run
manifest (`diagnosis.clusters_sha256`).

## Corruption and distance

For a cluster, an arm and a seed, `ahd.diagnosis.corrupt.assign` draws deterministically:

- `corrupt_where_near`: another component in the **same layer**; `corrupt_where_far`: a
  component in a **different layer**; step t' active for that component and outside the
  sufficient set. An empty pool falls back to the other tier with `distance_fallback: true`.
  Distance is recorded as two covariates, `same_layer` and `same_file`; ETCLOVG index
  differences are not used (owner decision 5).
- `corrupt_why`: another cluster's cause and mechanism; `corrupt_how`: another cluster's fix
  hint; `shuffled`: another cluster's whole diagnosis.
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
