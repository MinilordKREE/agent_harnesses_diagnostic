<!-- Plausibility-parity judge (ahd M3.2, no paper counterpart): a blind judge scores two
diagnoses of the same failure cluster for plausibility given the failed runs only; the reference
trajectory and the provenance of either diagnosis are never shown; their order is randomised
per cluster. Output JSON only. -->
Below are failed runs of an agent harness on one task, followed by two diagnoses (A and B)
written by different analysts. Rate how plausible each diagnosis is as an explanation of these
failures on a 1 to 5 scale (1 = implausible, 5 = clearly the mechanism). Judge each on its own
merits: both may be good, both may be poor.

Failed runs (condensed):
{failed_trajectories}

Diagnosis A:
{diagnosis_a}

Diagnosis B:
{diagnosis_b}

Answer with a single JSON object: {"A": <1-5>, "B": <1-5>}
