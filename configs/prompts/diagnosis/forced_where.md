<!-- COH-WRONG generator (ahd M3.2, no paper counterpart): a diagnosis written for a forced
(decoy) component and step from the failed trajectories alone. The reference trajectory is
never shown, and the plausibility judge's scores are never shown to this generator. Output
JSON only. -->
You are diagnosing why an agent harness failed a task. You see only failed runs of one failure
cluster (no successful run). An engineer has already localised the fault: it lies in the harness
component `{component}` ({layer} layer: {component_role}) and it acted at step {step}. Take that
localisation as given and explain the failure through it.

Failed runs (condensed):
{failed_trajectories}

Answer with a single JSON object with these keys:
- "severity": one of "low", "medium", "high", "critical"
- "cause_label": exactly one id from this controlled vocabulary (or `other:<short text>` only
  when none fits), chosen to fit the component above:
{cause_labels}
- "mechanism": one or two sentences explaining how `{component}` produced the failing behaviour
  seen at step {step}; describe behaviour, not file names; never attribute the failure to any
  other component
- "fix_hint": one sentence describing what `{component}` should do differently
(variant {variant})
