<!-- Follows HarnessEvolve §3.4 "Trajectory Comparison" for the no-reference case: "when no
reference exists, only s, m, h are produced" (the SYSTEM arm). Same template, same model, no
divergence point. Output JSON only. -->
You are diagnosing why an agent harness failed a task. You are given the task and the failed
trajectory only; there is no reference run.

Task id: {task_id}
Task prompt (as the failed run saw it):
{task_prompt}

Failed trajectory (condensed):
{failed_trajectory}

Scorer verdict: {score_reason}

Candidate harness components (choose exactly one id from this list; the rule that produced
it is {rule_id}: {rule_note}):
{candidates}

Answer with a single JSON object with these keys:
- "severity": one of "low", "medium", "high", "critical"
- "cause_label": exactly one id from this controlled vocabulary (or `other:<short text>` only
  when none fits):
{cause_labels}
- "mechanism": one or two sentences explaining the mechanism by which the harness produced the
  failure; describe behaviour, not file names
- "fix_hint": one sentence describing what the harness should do differently
- "component": one id from the candidate list
- "step": the step number you consider the root of the failure, or null
