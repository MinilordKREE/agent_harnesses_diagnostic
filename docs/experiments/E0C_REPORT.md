# E0 calibration report

Spec: `experiments/E0/spec_e0c.yaml`; runs: `runs/E0c`; policy {'model': 'deepseek-v4-flash', 'temperature': 1.0, 'reasoning_effort': 'low'}; judge {'model': 'deepseek-v4-pro', 'temperature': 0.0, 'cached': True}; mock_today 2026-03-02; replay k=3, candidates<=5, economize=True; T_att=5; workers=4.

Run git sha(s): 3940c95beae68aec877edf5044e58605a8c8608f; spec sha(s) recorded in manifests: 9b32200728715313ff6243bd20f34858115fdc6e33e5dd81239d38ed63b213d0.

## E0a pilot

E0a has not run (no runs/E0/e0a_tasks.json).

## E0c mining and held-out passes

### E0b: frozen splits

| source | split | tasks |
|---|---|---|
| browsecomp | validation | 32 |
| browsecomp | eval_dev | 24 |
| browsecomp | heldout | 30 |
| claw_eval | validation | 32 |
| claw_eval | eval_dev | 24 |
| claw_eval | heldout | 30 |
| gdpval | validation | 32 |
| gdpval | eval_dev | 24 |
| gdpval | heldout | 30 |
| hle | validation | 32 |
| hle | eval_dev | 24 |
| hle | heldout | 30 |

### E0b: gdpval text vs vision judge (every artifact)

compared=0, disagreement=

| run_id | task_id | replicate | text_passed | text_value | vision_model | vision_passed | vision_value | vision_used_images | agree | vision_error |
|---|---|---|---|---|---|---|---|---|---|---|

### E0b: baseline

| source | run_id | tasks | rollouts | rollout_pass_rate | pass_hat_k_rate |
|---|---|---|---|---|---|
| claw_eval | e0c-b1-claw_eval-p1 | 66 | 198 | 0.8434 | 0.7879 |
| claw_eval | e0c-b1-claw_eval-p2 | 66 | 198 | 0.8384 | 0.7576 |
| claw_eval | e0c-b1-claw_eval-p3 | 66 | 198 | 0.8376 | 0.7273 |

### E0b: A/A bands

| source | split | tasks | agreement | delta_points | ci95_low | ci95_high |
|---|---|---|---|---|---|---|
| claw_eval | validation | 66 | 0.8889 | 4.04 | 0.00 | 13.64 |
| claw_eval | validation:p1-p2 | 66 | 0.9091 | 3.03 | 0.00 | 10.61 |
| claw_eval | validation:p1-p3 | 66 | 0.8788 | 6.06 | 0.00 | 13.64 |
| claw_eval | validation:p2-p3 | 66 | 0.8788 | 3.03 | 0.00 | 10.61 |
| claw_eval | heldout | 30 | 0.8333 | 10.00 | 0.00 | 23.33 |

### E0b: failure types

| source | failure_type | count |
|---|---|---|
| claw_eval | deterministic | 29 |
| claw_eval | stochastic | 9 |
| claw_eval | unrepairable | 8 |
| claw_eval | unreplayable | 2 |

### E0b: references

| source | failed_tasks | references | genuine | shortcut | undetermined |
|---|---|---|---|---|---|
| claw_eval | 21 | 35 | 34 | 1 | 0 |

### E0b: replay

| source | failures_replayed | oracle_validated | deterministic | stochastic | unrepairable | unreplayable | mean_candidates | full_arms_failures | control_pass_fraction_mean |
|---|---|---|---|---|---|---|---|---|---|
| claw_eval | 57 | 38 | 29 | 9 | 8 | 2 | 3.86 | 0 |  |

### E0b: clusters

| source | n_clusters | sizes | singleton_fraction | deterministic_fraction | clusters_with_two |
|---|---|---|---|---|---|
| claw_eval | 15 | [6, 5, 5, 5, 3, 3, 2, 2, 1, 1, 1, 1, 1, 1, 1] | 0.4667 | 0.8667 | 8 |

### E0b: leakage

| source | run_id | n | top1 | top3 | chance_top1 |
|---|---|---|---|---|---|
| claw_eval | e0c-b1-claw_eval-p1 | 6 | 0.1667 | 0.3333 | 0.0556 |
| claw_eval | e0c-b1-claw_eval-p2 | 10 | 0.5000 | 0.6000 | 0.0556 |
| claw_eval | e0c-b1-claw_eval-p3 | 8 | 0.3750 | 0.6250 | 0.0556 |

### E0b: judge calibration

| judge | artifacts | rejudged | self_consistency | released_labels |
|---|---|---|---|---|

## E0c calibration (low effort)

E0c: policy reasoning_effort **low**; mining pool ['validation', 'eval_dev', 'eval_rest']; splits `experiments/splits_v2.json`; cap 40.0 USD; replay {'first_stage': 3, 'confirm': "oracle steps and corruption-placement steps confirmed at n >= 5 ONLY for clusters entering E2: per merged (cause, component) key with >= min_members members, the per-run representative's unconfirmed candidates and each other member's oracle-step candidate; rounds repeat while a confirmation changes the clusters", 'max_confirm_rounds': 3, 'priority': 'failures of tasks with the most failing rollouts across the mining passes first, so the cap cuts singletons'}.

### E0d-A: seed noise band (held-out passes of the seed harness)

| source | passes | mean | sigma_seed | pairs | |d| mean | p90 | p95 |
|---|---|---|---|---|---|---|---|
| claw_eval | 4 | 67.50 | 5.00 | 6 | 6.11 | 10.00 | 10.00 |

### E0d-B: replay escalation (marginal fixed-k verdicts re-opened)

| source | run | failures | candidates | type changed | oracle changed | transitions | usd |
|---|---|---|---|---|---|---|---|
| claw_eval | e0c-b1-claw_eval-p1 | 11 | 20 | 0 | 0 | {"negative->negative": 5, "positive->negative": 1, "positive->positive": 13, "unresolved->unresolved": 1} | 0.2001 |
| claw_eval | e0c-b1-claw_eval-p2 | 10 | 28 | 0 | 0 | {"negative->negative": 15, "positive->positive": 11, "unresolved->unresolved": 2} | 0.5723 |
| claw_eval | e0c-b1-claw_eval-p3 | 10 | 33 | 2 | 3 | {"negative->negative": 16, "negative->positive": 1, "negative->unresolved": 1, "positive->negative": 1, "positive->positive": 11, "positive->unresolved": 2, "unresolved->unresolved": 1} | 1.1300 |

### E0d-B: failure types before and after M3.2

| source | failure_type | before | after |
|---|---|---|---|
| claw_eval | deterministic | 31 | 29 |
| claw_eval | stochastic | 9 | 9 |
| claw_eval | unrepairable | 8 | 8 |
| claw_eval | unreplayable | 2 | 2 |
| claw_eval | unresolved | 7 | 9 |
| claw_eval | candidates_confirmed_n>=5 |  | 81 |
| claw_eval | candidates_unconfirmed_fixed_k |  | 109 |

### E0d-C: COH-WRONG plausibility parity (per source, pooled over passes)

| source | rounds | final seed | n | mean diff | ref> | coh> | ties | p | status |
|---|---|---|---|---|---|---|---|---|---|
| claw_eval | 4 | 3 | 10 | 0.0000 | 4 | 3 | 3 | 1.000 | ok |

### E0d-C: COH-WRONG assignments and scores

| source | run | cluster | decoy | step | basis | cause | sev | gen | ref | coh | error |
|---|---|---|---|---|---|---|---|---|---|---|---|
| claw_eval | e0c-b1-claw_eval-p1 | c63b3e797 | planner | 1 | not_sufficient | insufficient_evidence | high | 3 | 1 | 1 |  |
| claw_eval | e0c-b1-claw_eval-p1 | cd43b738f | observation_shaping | 5 | validated_negative | context_loss | high | 3 | 1 | 4 |  |
| claw_eval | e0c-b1-claw_eval-p1 | c95548d17 | tool_registry | 2 | validated_negative | insufficient_evidence | high | 3 | 4 | 1 |  |
| claw_eval | e0c-b1-claw_eval-p2 | c61133d38 | middleware | 2 | validated_negative | over_exploration | high | 3 | 4 | 1 |  |
| claw_eval | e0c-b1-claw_eval-p2 | c1605dd26 | tool_router | 6 | validated_negative | tool_hallucination | high | 3 | 1 | 1 |  |
| claw_eval | e0c-b1-claw_eval-p2 | c33ef13ee | verifier | 2 | validated_negative | error_recovery | high | 3 | 2 | 1 |  |
| claw_eval | e0c-b1-claw_eval-p2 | c4b6ab060 | loop | 2 | validated_negative | over_exploration | high | 3 | 1 | 1 |  |
| claw_eval | e0c-b1-claw_eval-p2 | c3622ecfc | tool_registry | 4 | validated_negative | tool_hallucination | high | 3 | 2 | 4 |  |
| claw_eval | e0c-b1-claw_eval-p3 | c695f431f | verifier | 5 | validated_negative | state_corruption | high | 3 | 2 | 1 |  |
| claw_eval | e0c-b1-claw_eval-p3 | c52391f1d | task_prompt | 3 | validated_negative | context_loss | high | 3 | 1 | 4 |  |

### E0d-D: privileged-information probe (recovery from the rendered diagnosis alone)

| source | arm | n | top1 | top3 | origin top1 | tool P | tool R | category | chance1 | chance3 |
|---|---|---|---|---|---|---|---|---|---|---|

### E0d-E: component ambiguity (attribution records of the reference arm)

| source | failures | rule | llm | candidate-set sizes | clusters | component_unique |
|---|---|---|---|---|---|---|
| claw_eval | 38 | 0.0000 | 1.0000 | {"2": 8, "3": 25, "4": 5} | 15 | 0 |

### M3.2: decoy exclusion audit (old rule vs full candidate set)

Old-rule decoys that sat inside the candidate set: 4 of 48 (cluster, tier) draws.

| source | clusters | lose near | lose far |
|---|---|---|---|
| claw_eval | 24 | 0 | 0 |

### E0d-F: minimum detectable effect (cluster-level sign-flip test, alpha 0.05, power 0.8)

Cost model: 11 arms x N x k x (proposal call + passes x held-out pass); inputs from the E0b ledgers.

| source | N | k | passes | sigma | spread | MDE | cost | <=3 | obs N |
|---|---|---|---|---|---|---|---|---|---|
| claw_eval | 7 | 3 | 1 | 5.00 | 5.00 | 7.75 | 547.78 | 0 | 0 |
| claw_eval | 7 | 3 | 2 | 5.00 | 5.00 | 7.25 | 1095.03 | 0 | 0 |
| claw_eval | 7 | 5 | 1 | 5.00 | 5.00 | 7.50 | 912.96 | 0 | 0 |
| claw_eval | 7 | 5 | 2 | 5.00 | 5.00 | 7.25 | 1825.04 | 0 | 0 |
| claw_eval | 8 | 3 | 1 | 5.00 | 5.00 | 7.00 | 626.03 | 0 | 1 |
| claw_eval | 8 | 3 | 2 | 5.00 | 5.00 | 6.75 | 1251.46 | 0 | 1 |
| claw_eval | 8 | 5 | 1 | 5.00 | 5.00 | 6.75 | 1043.39 | 0 | 1 |
| claw_eval | 8 | 5 | 2 | 5.00 | 5.00 | 6.50 | 2085.76 | 0 | 1 |
| claw_eval | 10 | 3 | 1 | 5.00 | 5.00 | 6.00 | 782.54 | 0 | 0 |
| claw_eval | 10 | 3 | 2 | 5.00 | 5.00 | 5.75 | 1564.32 | 0 | 0 |
| claw_eval | 10 | 5 | 1 | 5.00 | 5.00 | 5.50 | 1304.23 | 0 | 0 |
| claw_eval | 10 | 5 | 2 | 5.00 | 5.00 | 5.25 | 2607.20 | 0 | 0 |
| claw_eval | 14 | 3 | 1 | 5.00 | 5.00 | 5.00 | 1095.55 | 0 | 0 |
| claw_eval | 14 | 3 | 2 | 5.00 | 5.00 | 4.50 | 2190.05 | 0 | 0 |
| claw_eval | 14 | 5 | 1 | 5.00 | 5.00 | 4.75 | 1825.92 | 0 | 0 |
| claw_eval | 14 | 5 | 2 | 5.00 | 5.00 | 4.25 | 3650.09 | 0 | 0 |

### E0d-G: measurability funnel (per source; feasibility counts per-run clusters)

| source | rollouts | failed_rollouts | failed_tasks | genuine_references | replayable | validated_positive | validated_negative | unresolved | unreplayable | clusters | clusters_ge2 | feasible_where_near | feasible_where_far | feasible_why | feasible_how | feasible_shuffled | feasible_coherent_wrong |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| claw_eval | 594 | 95 | 21 | 34 | 57 | 29 | 17 | 9 | 2 | 15 | 8 | 24 | 24 | 24 | 24 | 24 | 24 |

## Incidents and operator interventions

| ts | kind | detail |
|---|---|---|
| 2026-09-09T03:51 | start | E0c launched at commit 3940c95 (spec_e0c.yaml, reasoning_effort low, splits_v2 sha 4a1e705c, cap 40 USD on runs/E0c ledgers). D1'' frozen as my reading: E2 under the condition with more E2-primary clusters, ties to max. |
| 2026-09-09T06:15 | restart | machine rebooted about 06:07 UTC (editor disconnect) and killed the runner during held-out pass 1; mining passes 1-3 complete; held-out pass 1 resumes from its done.json markers; relaunched at the same commit 3940c95 |
| 2026-09-09T10:57 | restart | second machine reboot about 10:55 UTC (editor disconnect) killed the runner during the mining pass 1 first-stage replay (5 of 19 failures had verdicts, 98 replay rollouts on disk with markers); relaunched at the same commit 3940c95, replays resume from the markers |

## Decision rules

**D1.** Source enters E2 iff: seed pass rate on the mining pool (validation U eval_dev) in [0.10, 0.90] (headroom both ways) AND per-source A/A |delta| <= 5 points AND >= 6 clusters with >= 2 members. (Amended: computed on the mining pool, not validation alone.)

**D1prime2.** E2 runs under the condition (max from E0b/E0d, low from E0c) with the larger number of E2-primary clusters (D2, confirmed at n >= 5); ties go to max (the paper policy). E2 starts regardless of D1' (owner: everything after E0c is E2) with 11 arms, k = 3 and 2 held-out passes per accepted patch; the paper states the D8 exclusion bound at the observed N. (My reading of "D1'' decision"; frozen before running.)

**D2.** A cluster is E2-primary iff >= 2 members, >= 1 genuine reference, oracle step validated (deterministic) or manifestation-based (stochastic), and near+far+why+how+all all feasible. Singletons and clusters failing any condition form the secondary pool.

**D4.** Replicates in E2: k=3 if projected E2 cost (from cost.csv, 8 arms x N primary clusters x k) <= owner budget entered in spec.yaml before running; else k=2; N is all primary clusters, never topped up.

**D5.** Held-out size: 30/source unless the A/A band on held-out exceeds 5 points, in which case 45/source (re-sampled from the same frozen seed, superset).

**D8.** delta_meaningful = 3 points. E2 uses the cheapest (N, k, passes) configuration with MDE <= 3 if one exists within owner_budget_usd; otherwise the best available configuration, and the paper states the exclusion bound (the smallest effect the design would have detected) rather than a null.

| rule | observed | decision |
|---|---|---|
| D1:claw_eval | pass_rate=0.8398 aa_delta=4.04 clusters_with_two=8 | enters E2 |
| D2:claw_eval | primary_clusters=8 | 8 primary; rest secondary |
| D3:browsecomp | missing A/A data | not evaluable |
| D3:hle | missing A/A data | not evaluable |
| D4 | projected_usd(k=3)=4.19 budget=600.0 | k=3 |
| D5:claw_eval | heldout_delta=10.00 | 45/source |
| D6 | no judge calibration | not evaluable |
| D8:claw_eval | N=7 k=3 passes=1 MDE=7.75 points cost=547.78 USD (delta_meaningful=3.0) | no configuration within owner_budget_usd reaches delta_meaningful: best available within the budget; the paper states the exclusion bound 7.75 points |
| D1prime2:claw_eval | primary clusters: low=8 max=6 (ties go to max) | E2 under low with N=8; 11 arms, k=3, 2 held-out passes per accepted patch; the paper states the D8 exclusion bound at that N |
| COH-WRONG:claw_eval | parity ok | arm admitted |
