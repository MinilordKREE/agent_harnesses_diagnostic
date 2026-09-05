<!-- Follows HarnessEvolve §3.4 "Trajectory Comparison" (arXiv 2609.00829v1): produce a
structured error signal (severity, cause, fix hint) from the failed trajectory and, when a
verified reference trajectory exists, the first action divergence point. ahd adds the
component choice, restricted to the candidate set below. Output JSON only. -->
You are diagnosing why an agent harness failed a task. You are given the task, the failed
trajectory, a verified reference trajectory of the same task that succeeded, and the step at
which the failed run first diverged from the reference.

Task id: {task_id}
Task prompt (as the failed run saw it):
{task_prompt}

Divergence step: {step} ({divergence_type}: {divergence_note})
Failed run's action at that step:
{failed_action}
Reference action at that step:
{reference_action}

Failed trajectory (condensed):
{failed_trajectory}

Reference trajectory (condensed):
{reference_trajectory}

Candidate harness components (choose exactly one id from this list; the rule that produced
it is {rule_id}: {rule_note}):
{candidates}

Answer with a single JSON object with these keys:
- "severity": one of "low", "medium", "high", "critical"
- "cause_label": one of {cause_labels}
- "mechanism": one or two sentences explaining the mechanism by which the harness produced the
  divergent action; describe behaviour, not file names
- "fix_hint": one sentence describing what the harness should do differently
- "component": one id from the candidate list
