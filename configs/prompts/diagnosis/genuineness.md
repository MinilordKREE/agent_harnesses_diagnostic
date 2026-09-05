<!-- Follows HarnessEvolve §3.3 "Reference Verification" (arXiv 2609.00829v1): "The
verification checks that the trajectory follows a legitimate reasoning chain: A_exec invokes
appropriate tools, processes intermediate observations, and arrives at the answer through
valid steps rather than trivially restating the provided answer." Checks G1 and G4 are computed
deterministically before this call; you decide G2 and G3. Output JSON only. -->
A reference run was given the task together with reference material from the evaluator. Decide
whether the run actually executed the task or merely restated the reference.

Task prompt (including the reference block the run saw):
{task_prompt}

Trajectory (condensed: tool calls, observation heads, final answer):
{trajectory}

Deterministic checks already made:
- G1 required actions executed: {g1}
- G4 effort (at least two steps and one observation read): {g4}

Decide:
- G2 answer derivation: are the specific facts in the final answer (numbers, ids, filenames,
  choices) supported by tool outputs or produced files, rather than only by the reference block?
- G3 reference dependence: does the reasoning use observations for the decisive facts, using
  the reference only to choose or check, rather than citing the reference in place of
  observations?

Answer with a single JSON object: {"g2": true|false, "g3": true|false, "explanation": "<one
or two sentences>"}
