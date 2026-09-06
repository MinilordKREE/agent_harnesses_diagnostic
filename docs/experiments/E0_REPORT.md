# E0 calibration report

Spec: `experiments/E0/spec.yaml`; runs: `runs/E0`; policy {'model': 'deepseek-v4-flash', 'temperature': 1.0, 'reasoning_effort': 'max'}; judge {'model': 'deepseek-v4-pro', 'temperature': 0.0, 'cached': True}; mock_today 2026-03-02; replay k=3, candidates<=5, economize=True; T_att=5; workers=4.

Run git sha(s): 51fee3e6c8c16a4d3f44d45ab6302f4462353a58, aca7d7ad2305b8e02d9b17f8888b556b7b78e7b9, c1c9a9f18ff7ee223de960f7e6ef1170824fc530; spec sha(s) recorded in manifests: c3877fd8d5fd7f74e0e89d0f67cbb7f7eb950db630ce37c0105f51efead92576.

## E0a pilot

### E0a pilot: replay verdicts and prefix drift

One row per replayed failure; `drift_reasons` counts why prefix re-execution was declared unreplayable (exit codes or mutated/quoted outputs differing after masking).

| source | failure_key | failure_type | oracle_step | oracle_step_basis | candidates | candidate_statuses | unreplayable_rollouts | drift_reasons | usd |
|---|---|---|---|---|---|---|---|---|---|
| browsecomp | bc-en-1084__r1__a1 | deterministic | 2 | sufficient | 5 | 1:insufficient;2:sufficient;3:insufficient;4:sufficient;5:unreplayable | 3 | mutating_or_quoted_output_differs=5 | 4.3525 |
| browsecomp | bc-en-772__r1__a1 | unreplayable |  | unvalidated | 5 | 3:unreplayable;4:unreplayable;5:unreplayable;6:unreplayable;7:unreplayable | 15 | exit_code_differs=30 | 0.0000 |
| claw_eval | claw-T012_expense_report__r1__a1 | deterministic | 2 | sufficient | 4 | 2:sufficient;3:sufficient;4:insufficient;1:insufficient | 0 |  | 0.0969 |
| claw_eval | claw-T012_expense_report__r2__a1 | deterministic | 2 | sufficient | 5 | 2:sufficient;3:sufficient;4:sufficient;5:sufficient;1:insufficient | 0 |  | 0.1104 |
| claw_eval | claw-T012_expense_report__r3__a1 | deterministic | 2 | sufficient | 5 | 1:insufficient;2:sufficient;3:sufficient;4:unreplayable;5:unreplayable | 6 | exit_code_differs=6 | 0.0966 |

### E0a pilot: web hosts fetched by the seed policy

Hosts in `curl`/`wget` commands of the pilot rollouts (top 8 per source). The seed harness has no search tool; Serper is only counted when the policy calls it explicitly, so `serper_calls_approx` understates web use.

| source | host | curl_commands |
|---|---|---|
| browsecomp | en.wikipedia.org | 82 |
| browsecomp | www.bing.com | 32 |
| browsecomp | api.gdeltproject.org | 28 |
| browsecomp | search.brave.com | 26 |
| browsecomp | web.archive.org | 20 |
| browsecomp | www.georgewpeck.com | 17 |
| browsecomp | news.google.com | 13 |
| browsecomp | translate.google.com | 12 |
| hle | www.bing.com | 17 |
| hle | www.google.com | 10 |
| hle | chessfox.com | 6 |
| hle | r.jina.ai | 6 |
| hle | subdl.com | 5 |
| hle | search.brave.com | 4 |
| hle | grep.app | 3 |
| hle | html.duckduckgo.com | 3 |
| gdpval | benefits.va.gov | 18 |
| gdpval | web.archive.org | 10 |
| gdpval | www.benefits.va.gov | 5 |
| gdpval | www.bing.com | 5 |
| gdpval | iris.who.int | 2 |
| gdpval | www.mojeek.com | 2 |
| gdpval | www.va.gov | 2 |
| gdpval | api.allorigins.win | 1 |
| claw_eval | www.sec.gov | 9 |
| claw_eval | data.sec.gov | 5 |
| claw_eval | 127.0.0.1 | 2 |
| claw_eval | stockanalysis.com | 1 |

### E0a pilot: cost per source

| source | tasks | rollouts | rollout_pass_rate | pass_hat_k_rate | policy_usd_mean | policy_usd_median | policy_usd_max | policy_usd_total | wall_s_mean | wall_s_median | wall_s_max | judge_usd | judge_calls | judge_cached | harness_failures | failed_tasks | reference_rollouts | reference_usd | reference_usd_per_failed_task | genuine_references | failures_replayed | replay_usd | replay_usd_per_failure | replay_rollouts | diagnosis_usd | diagnosis_usd_per_failure | ft_deterministic | ft_stochastic | ft_unrepairable | ft_unreplayable | infra_failures | usage_mismatch | partial_trajectories | serper_calls_approx | search_usd | source_total_usd |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| browsecomp | 5 | 5 | 0.6000 | 0.6000 | 0.1864 | 0.1707 | 0.3650 | 0.9320 | 1946.0 | 1543.5 | 3602.8 | 0.0064 | 27 | 0 | 2 | 2 | 2 | 0.1435 | 0.0717 | 2 | 2 | 4.3525 | 2.1762 | 24 | 0.0276 | 0.0138 | 1 | 0 | 0 | 1 | 0 | 0 | 0 | 1 | 0.0490 | 5.5109 |
| hle | 5 | 5 | 0.6000 | 0.6000 | 0.0434 | 0.0401 | 0.0863 | 0.2170 | 347.4 | 345.9 | 680.5 | 0.0025 | 5 | 0 | 2 | 2 | 2 | 0.0531 | 0.0265 | 0 | 0 | 0.0000 |  | 0 | 0.0022 |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.2748 |
| gdpval | 5 | 5 | 1.0000 | 1.0000 | 0.0513 | 0.0242 | 0.1251 | 0.2566 | 515.8 | 433.7 | 918.2 | 0.0538 | 5 | 0 | 0 | 0 | 0 | 0.0000 |  | 0 | 0 | 0.0000 |  | 0 | 0.0000 |  | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.3104 |
| claw_eval | 5 | 15 | 0.8000 | 0.8000 | 0.0093 | 0.0083 | 0.0147 | 0.1399 | 71.3 | 64.2 | 105.4 | 0.0861 | 71 | 0 | 3 | 1 | 1 | 0.0094 | 0.0094 | 1 | 3 | 0.3039 | 0.1013 | 60 | 0.0210 | 0.0070 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.0000 | 0.5603 |

### E0a pilot: wall clock

| source | rollouts | workers | run_wall_s | rollout_wall_sum_s | reference_wall_s | replay_wall_s | replay_wall_s_per_failure | diagnosis_wall_s |
|---|---|---|---|---|---|---|---|---|
| browsecomp | 5 | 4 | 3606 | 9730 | 812 | 17500 | 8750 | 21165 |
| hle | 5 | 4 | 733 | 1737 | 397 |  |  |  |
| gdpval | 5 | 4 | 962 | 2579 |  |  |  |  |
| claw_eval | 15 | 4 | 324 | 1069 | 66 | 1406 | 469 | 21050 |

### E0a pilot: extrapolation to E0b sizes

B1 = 32 validation tasks x benchmark trials x 2 passes; B2 = held-out per_source x trials x 2 passes; expected failures use the pilot's rollout fail rate; reference, replay and diagnosis costs use the pilot's per-unit costs (zero when the pilot had no failure in that source).

| source | b1_rollouts | b2_rollouts | rollout_fail_rate | expected_failures | expected_failed_tasks | policy_usd_b1 | policy_usd_b2 | judge_usd | reference_usd | replay_usd | diagnosis_usd | total_usd | wall_hours_at_workers |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| browsecomp | 64 | 60 | 0.4000 | 25.6 | 25.6 | 11.9292 | 11.1837 | 0.1592 | 1.8364 | 55.7120 | 0.3531 | 81.1735 | 33.6 |
| hle | 64 | 60 | 0.4000 | 25.6 | 25.6 | 2.7780 | 2.6043 | 0.0611 | 0.6795 | 0.0000 | 0.0000 | 6.1229 | 3.4 |
| gdpval | 64 | 60 | 0.0000 | 0.0 | 0.0 | 3.2846 | 3.0793 | 1.3350 | 0.0000 | 0.0000 | 0.0000 | 7.6989 | 4.4 |
| claw_eval | 192 | 180 | 0.2000 | 38.4 | 12.8 | 1.7905 | 1.6786 | 2.1346 | 0.1210 | 3.8898 | 0.2682 | 9.8827 | 3.2 |
| TOTAL | 384 | 360 |  | 89.6 |  | 19.7823 | 18.5459 | 3.6898 | 2.6368 | 59.6018 | 0.6213 | 104.8780 | 44.6 |

## E0b calibration

E0b has not run.

## Decision rules

**D1.** Source enters E2 iff: seed pass rate on validation in [0.10, 0.90] (headroom both ways) AND per-source A/A |delta| <= 5 points AND >= 6 clusters with >= 2 members.

**D2.** A cluster is E2-primary iff >= 2 members, >= 1 genuine reference, oracle step validated (deterministic) or manifestation-based (stochastic), and near+far+why+how+all all feasible. Singletons and clusters failing any condition form the secondary pool.

**D3.** Search stays in E2 only if D1 holds AND its A/A band is not wider than the other sources' by more than 2x; otherwise Search is reported separately as descriptive.

**D4.** Replicates in E2: k=3 if projected E2 cost (from cost.csv, 8 arms x N primary clusters x k) <= owner budget entered in spec.yaml before running; else k=2; N is all primary clusters, never topped up.

**D5.** Held-out size: 30/source unless the A/A band on held-out exceeds 5 points, in which case 45/source (re-sampled from the same frozen seed, superset).

**D6.** Judge: if self-consistency < 0.90, the E2 primary outcome uses a 2-of-3 judge vote (cost recorded); if Flash-vs-Pro agreement < 0.80, report both judges in E2.

| rule | observed | decision |
|---|---|---|
| D1 | E0b not run | not evaluable |
| D2 | E0b not run | not evaluable |
| D3 | E0b not run | not evaluable |
| D4 | E0b not run | not evaluable |
| D5 | E0b not run | not evaluable |
| D6 | E0b not run | not evaluable |
