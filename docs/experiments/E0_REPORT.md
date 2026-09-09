# E0 calibration report

Spec: `experiments/E0/spec.yaml`; runs: `runs/E0`; policy {'model': 'deepseek-v4-flash', 'temperature': 1.0, 'reasoning_effort': 'max'}; judge {'model': 'deepseek-v4-pro', 'temperature': 0.0, 'cached': True}; mock_today 2026-03-02; replay k=3, candidates<=5, economize=True; T_att=5; workers=4.

Run git sha(s): 4f2276904b1c7589e104b1d0e5a9d2e55f27cb0e, 51fee3e6c8c16a4d3f44d45ab6302f4462353a58, a49339c86990716d00b1fc6a3e143b2a44753b3b, aca7d7ad2305b8e02d9b17f8888b556b7b78e7b9, c1c9a9f18ff7ee223de960f7e6ef1170824fc530, db5807f1f0608184b141c80d37488e1c842d1d49, f9799b335646aef34562154a8fe1a6b838dde8ce; spec sha(s) recorded in manifests: 9dd1247dd70a030f095b17c5d0391246a3d8a91e7b0975735f62b033dcfbd34e, c3877fd8d5fd7f74e0e89d0f67cbb7f7eb950db630ce37c0105f51efead92576, dea6555ee544be1855b0fc2f2f4b1e00fa79e1b0eaced3185c48d898f616cfb0.

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

compared=256, disagreement=0.0625

| run_id | task_id | replicate | text_passed | text_value | vision_model | vision_passed | vision_value | vision_used_images | agree | vision_error |
|---|---|---|---|---|---|---|---|---|---|---|
| e0b-b1-gdpval-p1 | gdpval-0112fc9b-c3b2-4084-8993-5a4abb1f54f1 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-02aa1805-c658-4069-8a6a-02dec146063a | r1 | 1 | 0.7209 | deepseek-v4-flash-vision-exp | 1 | 0.7093 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-0419f1c3-d669-45d0-81cd-f4d5923b06a5 | r1 | 1 | 0.9647 | deepseek-v4-flash-vision-exp | 1 | 0.9647 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-0e386e32-df20-4d1f-b536-7159bc409ad5 | r1 | 0 | 0.1282 | deepseek-v4-flash-vision-exp | 1 | 0.9231 | 0 | 0 |  |
| e0b-b1-gdpval-p1 | gdpval-0ec25916-1b5c-4bfe-93d3-4e103d860f3a | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-0fad6023-767b-42c1-a1b3-027cd4f583cb | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9470 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-1137e2bb-bdf9-4876-b572-f29b7de5e595 | r1 | 1 | 0.9875 | deepseek-v4-flash-vision-exp | 1 | 0.9875 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-15d37511-75c5-4c7f-81f1-16e00c0d95f3 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-1752cb53-5983-46b6-92ee-58ac85a11283 | r1 | 0 | 0.2958 | deepseek-v4-flash-vision-exp | 1 | 0.9437 | 1 | 0 |  |
| e0b-b1-gdpval-p1 | gdpval-1e5a1d7f-12c1-48c6-afd9-82257b3f2409 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-211d0093-2c64-4bd0-828c-0201f18924e7 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-2696757c-1f8a-4959-8f0d-f5597b9e70fc | r1 | 0 | 0.5122 | deepseek-v4-flash-vision-exp | 0 | 0.5366 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-3c19c6d1-672c-467a-8437-6fe21afb8eae | r1 | 1 | 0.9873 | deepseek-v4-flash-vision-exp | 1 | 0.9873 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-3f625cb2-f40e-4ead-8a97-6924356d5989 | r1 | 1 | 0.9474 | deepseek-v4-flash-vision-exp | 1 | 0.8618 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-3f821c2d-ab97-46ec-a0fb-b8f73c2682bc | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9938 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-476db143-163a-4537-9e21-fe46adad703b | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.8438 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-4b894ae3-1f23-4560-b13d-07ed1132074e | r1 | 0 | 0.0000 | deepseek-v4-flash-vision-exp | 0 | 0.0000 | 0 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-5349dd7b-bf0a-4544-9a17-75b7013767e6 | r1 | 0 | 0.4698 | deepseek-v4-flash-vision-exp | 0 | 0.5034 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-552b7dd0-96f4-437c-a749-0691e0e4b381 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-57b2cdf2-ad62-4591-aa91-aad489740320 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-58ac1cc5-5754-4580-8c9c-8c67e1a9d619 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9868 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-5a2d70da-0a42-4a6b-a3ca-763e03f070a5 | r1 | 1 | 0.9778 | deepseek-v4-flash-vision-exp | 1 | 0.9556 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-5d0feb24-e8b6-4ace-b64f-d5cd1a8b563d | r1 | 1 | 0.9844 | deepseek-v4-flash-vision-exp | 1 | 0.9688 | 0 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-6074bba3-7e3a-4b1c-b8c6-a15bb6695c3b | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-61e7b9c6-0051-429f-a341-fda9b6578a84 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-6436ff9e-c5f2-47ba-9aaa-49d89b0594ab | r1 | 1 | 0.9683 | deepseek-v4-flash-vision-exp | 1 | 0.9841 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-68d8d901-dd0b-4a7e-bf9a-1074fddf1a96 | r1 | 1 | 0.9651 | deepseek-v4-flash-vision-exp | 1 | 0.9767 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-7151c60a-d4cb-4fc4-8169-3d4cb446e6b9 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-788d2bc6-82df-4dc7-8467-a0f31405dc14 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-7de33b48-5163-4f50-b5f3-8deea8185e57 | r1 | 0 | 0.0000 | deepseek-v4-flash-vision-exp | 0 | 0.0000 | 0 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-8079e27d-b6f3-4f75-a9b5-db27903c798d | r1 | 0 | 0.4636 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 0 |  |
| e0b-b1-gdpval-p1 | gdpval-8a7b6fca-60cc-4ae3-b649-971753cbf8b9 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-90edba97-74f0-425a-8ff6-8b93182eb7cb | r1 | 1 | 0.8971 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-93b336f3-61f3-4287-86d2-87445e1e0f90 | r1 | 1 | 0.8684 | deepseek-v4-flash-vision-exp | 1 | 0.8684 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-94925f49-36bc-42da-b45b-61078d329300 | r1 | 0 | 0.3537 | deepseek-v4-flash-vision-exp | 0 | 0.3659 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-9efbcd35-186d-49b6-ac24-28ee2bc9a263 | r1 | 1 | 0.8289 | deepseek-v4-flash-vision-exp | 1 | 0.8947 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-a10ec48c-168e-476c-8fe3-23b2a5f616ac | r1 | 1 | 0.8000 | deepseek-v4-flash-vision-exp | 1 | 0.7714 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-a1963a68-1bea-4bb1-b7e0-145c92a57449 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-a73fbc98-90d4-4134-a54f-2b1d0c838791 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9655 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-a99d85fc-eff8-48d2-a7d4-42a75d62f18d | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-b39a5aa7-cd1b-47ad-b249-90afd22f8f21 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-b57efde3-26d6-4742-bbff-2b63c43b4baa | r1 | 1 | 0.8933 | deepseek-v4-flash-vision-exp | 1 | 0.8400 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-b5d2e6f1-62a2-433a-bcdd-95b260cdd860 | r1 | 1 | 0.8000 | deepseek-v4-flash-vision-exp | 1 | 0.7385 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-b78fd844-db76-448e-a783-5e9877cb74c2 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-bb863dd9-31c2-4f64-911a-ce11f457143b | r1 | 1 | 0.6392 | deepseek-v4-flash-vision-exp | 1 | 0.7526 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-c2e8f271-7858-412f-b460-472463ad81d9 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9877 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-c7d83f01-2874-4876-b7fd-52582ec99e1a | r1 | 0 | 0.0000 | deepseek-v4-flash-vision-exp | 0 | 0.0000 | 0 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-d025a41c-c439-4ee1-bc79-dd5c94b27a2d | r1 | 1 | 0.7887 | deepseek-v4-flash-vision-exp | 1 | 0.8028 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-dfb4e0cd-a0b7-454e-b943-0dd586c2764c | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-ec2fccc9-b7f6-4c73-bf51-896fdb433cec | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-ed2bc14c-99ac-4a2a-8467-482a1a5d67f3 | r1 | 1 | 0.9815 | deepseek-v4-flash-vision-exp | 1 | 0.9815 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-f2986c1f-2bbf-4b83-bc93-624a9d617f45 | r1 | 0 | 0.0000 | deepseek-v4-flash-vision-exp | 0 | 0.0000 |  | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-f5d428fd-b38e-41f0-8783-35423dab80f6 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-fccaa4a1-1c39-49ac-b701-55361a19966b | r1 | 1 | 0.9259 | deepseek-v4-flash-vision-exp | 1 | 0.9630 | 1 | 1 |  |
| e0b-b1-gdpval-p1 | gdpval-feb5eefc-39f1-4451-9ef9-bffe011b71dd | r1 | 1 | 0.9904 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-0112fc9b-c3b2-4084-8993-5a4abb1f54f1 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-02aa1805-c658-4069-8a6a-02dec146063a | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9767 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-0419f1c3-d669-45d0-81cd-f4d5923b06a5 | r1 | 1 | 0.9412 | deepseek-v4-flash-vision-exp | 1 | 0.9647 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-0e386e32-df20-4d1f-b536-7159bc409ad5 | r1 | 0 | 0.0000 | deepseek-v4-flash-vision-exp | 0 | 0.0000 | 0 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-0ec25916-1b5c-4bfe-93d3-4e103d860f3a | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-0fad6023-767b-42c1-a1b3-027cd4f583cb | r1 | 1 | 0.8636 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-1137e2bb-bdf9-4876-b572-f29b7de5e595 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-15d37511-75c5-4c7f-81f1-16e00c0d95f3 | r1 | 1 | 0.9725 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-1752cb53-5983-46b6-92ee-58ac85a11283 | r1 | 0 | 0.2817 | deepseek-v4-flash-vision-exp | 1 | 0.6197 | 1 | 0 |  |
| e0b-b1-gdpval-p2 | gdpval-1e5a1d7f-12c1-48c6-afd9-82257b3f2409 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-211d0093-2c64-4bd0-828c-0201f18924e7 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-2696757c-1f8a-4959-8f0d-f5597b9e70fc | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-3c19c6d1-672c-467a-8437-6fe21afb8eae | r1 | 1 | 0.9873 | deepseek-v4-flash-vision-exp | 1 | 0.9873 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-3f625cb2-f40e-4ead-8a97-6924356d5989 | r1 | 1 | 0.9605 | deepseek-v4-flash-vision-exp | 1 | 0.9342 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-3f821c2d-ab97-46ec-a0fb-b8f73c2682bc | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9938 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-476db143-163a-4537-9e21-fe46adad703b | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-4b894ae3-1f23-4560-b13d-07ed1132074e | r1 | 0 | 0.1290 | deepseek-v4-flash-vision-exp | 0 | 0.0000 | 0 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-5349dd7b-bf0a-4544-9a17-75b7013767e6 | r1 | 1 | 0.7919 | deepseek-v4-flash-vision-exp | 1 | 0.7315 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-552b7dd0-96f4-437c-a749-0691e0e4b381 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-57b2cdf2-ad62-4591-aa91-aad489740320 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-58ac1cc5-5754-4580-8c9c-8c67e1a9d619 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-5a2d70da-0a42-4a6b-a3ca-763e03f070a5 | r1 | 1 | 0.9778 | deepseek-v4-flash-vision-exp | 1 | 0.9778 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-5d0feb24-e8b6-4ace-b64f-d5cd1a8b563d | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9688 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-5e2b6aab-f9fb-4dd6-a1a5-874ef1743909 | r1 | 0 | 0.0000 | deepseek-v4-flash-vision-exp | 0 | 0.0000 |  | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-6074bba3-7e3a-4b1c-b8c6-a15bb6695c3b | r1 | 0 | -3.2000 | deepseek-v4-flash-vision-exp | 0 | -4.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-61e7b9c6-0051-429f-a341-fda9b6578a84 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-6436ff9e-c5f2-47ba-9aaa-49d89b0594ab | r1 | 1 | 0.9841 | deepseek-v4-flash-vision-exp | 1 | 0.9841 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-68d8d901-dd0b-4a7e-bf9a-1074fddf1a96 | r1 | 1 | 0.9884 | deepseek-v4-flash-vision-exp | 1 | 0.9884 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-7151c60a-d4cb-4fc4-8169-3d4cb446e6b9 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-788d2bc6-82df-4dc7-8467-a0f31405dc14 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-7de33b48-5163-4f50-b5f3-8deea8185e57 | r1 | 0 | 0.3500 | deepseek-v4-flash-vision-exp | 0 | 0.1500 | 0 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-8079e27d-b6f3-4f75-a9b5-db27903c798d | r1 | 1 | 0.8818 | deepseek-v4-flash-vision-exp | 1 | 0.8455 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-8a7b6fca-60cc-4ae3-b649-971753cbf8b9 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-90edba97-74f0-425a-8ff6-8b93182eb7cb | r1 | 1 | 0.8897 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-93b336f3-61f3-4287-86d2-87445e1e0f90 | r1 | 1 | 0.8684 | deepseek-v4-flash-vision-exp | 1 | 0.8684 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-94925f49-36bc-42da-b45b-61078d329300 | r1 | 1 | 0.6707 | deepseek-v4-flash-vision-exp | 1 | 0.7439 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-9efbcd35-186d-49b6-ac24-28ee2bc9a263 | r1 | 1 | 0.8026 | deepseek-v4-flash-vision-exp | 1 | 0.8947 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-a10ec48c-168e-476c-8fe3-23b2a5f616ac | r1 | 1 | 0.8286 | deepseek-v4-flash-vision-exp | 1 | 0.8857 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-a1963a68-1bea-4bb1-b7e0-145c92a57449 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9848 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-a73fbc98-90d4-4134-a54f-2b1d0c838791 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9310 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-a99d85fc-eff8-48d2-a7d4-42a75d62f18d | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-b39a5aa7-cd1b-47ad-b249-90afd22f8f21 | r1 | 0 | 0.3871 | deepseek-v4-flash-vision-exp | 1 | 0.9194 | 1 | 0 |  |
| e0b-b1-gdpval-p2 | gdpval-b57efde3-26d6-4742-bbff-2b63c43b4baa | r1 | 1 | 0.7467 | deepseek-v4-flash-vision-exp | 1 | 0.7333 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-b5d2e6f1-62a2-433a-bcdd-95b260cdd860 | r1 | 1 | 0.9538 | deepseek-v4-flash-vision-exp | 1 | 0.8308 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-b78fd844-db76-448e-a783-5e9877cb74c2 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-bb863dd9-31c2-4f64-911a-ce11f457143b | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9897 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-c2e8f271-7858-412f-b460-472463ad81d9 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9753 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-c7d83f01-2874-4876-b7fd-52582ec99e1a | r1 | 1 | 0.9623 | deepseek-v4-flash-vision-exp | 0 | 0.0000 | 0 | 0 |  |
| e0b-b1-gdpval-p2 | gdpval-d025a41c-c439-4ee1-bc79-dd5c94b27a2d | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9718 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-dfb4e0cd-a0b7-454e-b943-0dd586c2764c | r1 | 1 | 0.8140 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-ec2fccc9-b7f6-4c73-bf51-896fdb433cec | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-ed2bc14c-99ac-4a2a-8467-482a1a5d67f3 | r1 | 1 | 0.9815 | deepseek-v4-flash-vision-exp | 1 | 0.9722 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-f2986c1f-2bbf-4b83-bc93-624a9d617f45 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-f5d428fd-b38e-41f0-8783-35423dab80f6 | r1 | 1 | 0.9434 | deepseek-v4-flash-vision-exp | 1 | 0.9717 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-fccaa4a1-1c39-49ac-b701-55361a19966b | r1 | 1 | 0.9815 | deepseek-v4-flash-vision-exp | 1 | 0.9815 | 1 | 1 |  |
| e0b-b1-gdpval-p2 | gdpval-feb5eefc-39f1-4451-9ef9-bffe011b71dd | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-01d7e53e-0513-4109-a242-8ccaf442cd21 | r1 | 1 | 0.8333 | deepseek-v4-flash-vision-exp | 1 | 0.7738 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-0353ee0c-18b5-4ad3-88e8-e001d223e1d7 | r1 | 0 | 0.4404 | deepseek-v4-flash-vision-exp | 1 | 0.6606 | 1 | 0 |  |
| e0b-b2-gdpval-p1 | gdpval-045aba2e-4093-42aa-ab7f-159cc538278c | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-05389f78-589a-473c-a4ae-67c61050bfca | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9886 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-11593a50-734d-4449-b5b4-f8986a133fd8 | r1 | 0 | 0.4340 | deepseek-v4-flash-vision-exp | 1 | 0.8868 | 1 | 0 |  |
| e0b-b2-gdpval-p1 | gdpval-116e791e-890c-42b1-ba90-1db02e8bfd45 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-19403010-3e5c-494e-a6d3-13594e99f6af | r1 | 1 | 0.6694 | deepseek-v4-flash-vision-exp | 1 | 0.7258 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-22c0809b-f8db-489e-93b3-b4da225e3e0e | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-2d06bc0a-89c6-4e89-9417-5ffe725c1bc6 | r1 | 1 | 0.9697 | deepseek-v4-flash-vision-exp | 1 | 0.9697 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-401a07f1-d57e-4bb0-889b-22de8c900f0e | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-43dc9778-450b-4b46-b77e-b6d82b202035 | r1 | 0 | 0.4050 | deepseek-v4-flash-vision-exp | 0 | 0.4050 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-4520f882-715a-482d-8e87-1cb3cbdfe975 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-47ef842d-8eac-4b90-bda8-dd934c228c96 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-4b98ccce-9e42-44e9-9115-6fc3e79de288 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-61f546a8-c374-467f-95cc-d0d9b5656eb6 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-76d10872-9ffa-4ede-83ee-e0f1ec5e2b8d | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-7bbfcfe9-132d-4194-82bb-d6f29d001b01 | r1 | 0 | 0.5094 | deepseek-v4-flash-vision-exp | 0 | 0.5094 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-85d95ce5-b20c-41e2-834e-e788ce9622b6 | r1 | 1 | 0.9412 | deepseek-v4-flash-vision-exp | 1 | 0.6941 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-9e39df84-ac57-4c9b-a2e3-12b8abf2c797 | r1 | 0 | 0.5000 | deepseek-v4-flash-vision-exp | 0 | 0.5417 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-aad21e4c-1d43-45fc-899a-97754a1b1b63 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-ab81b076-e5d8-473a-9bdb-7ea7c38f6ebc | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp |  |  |  |  | judge_error |
| e0b-b2-gdpval-p1 | gdpval-b1a79ce1-86b0-41fb-97dc-9206dfd7b044 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9811 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-bb499d9c-0263-4684-9238-75e8e86077b1 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-be830ca0-b352-4658-a5bd-57139d6780ba | r1 | 1 | 0.8312 | deepseek-v4-flash-vision-exp | 1 | 0.8442 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-cecac8f9-8203-4ebd-ad49-54436a8c4171 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-e14e32ba-d310-4d45-9b8a-6d73d0ece1ae | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-ec591973-04d5-48c0-981c-1ab2fcec2dc1 | r1 | 1 | 0.9870 | deepseek-v4-flash-vision-exp | 1 | 0.9286 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-efca245f-c24f-4f75-a9d5-59201330ab7a | r1 | 1 | 0.9902 | deepseek-v4-flash-vision-exp | 1 | 0.9608 | 1 | 1 |  |
| e0b-b2-gdpval-p1 | gdpval-f3351922-dbdd-45da-85c5-e7110696bbe5 | r1 | 1 | 0.9882 | deepseek-v4-flash-vision-exp | 1 | 0.9529 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-01d7e53e-0513-4109-a242-8ccaf442cd21 | r1 | 1 | 0.8690 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-0353ee0c-18b5-4ad3-88e8-e001d223e1d7 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-045aba2e-4093-42aa-ab7f-159cc538278c | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-05389f78-589a-473c-a4ae-67c61050bfca | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-11593a50-734d-4449-b5b4-f8986a133fd8 | r1 | 0 | 0.4906 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 0 |  |
| e0b-b2-gdpval-p2 | gdpval-116e791e-890c-42b1-ba90-1db02e8bfd45 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9844 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-19403010-3e5c-494e-a6d3-13594e99f6af | r1 | 1 | 0.6694 | deepseek-v4-flash-vision-exp | 1 | 0.7339 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-22c0809b-f8db-489e-93b3-b4da225e3e0e | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-2d06bc0a-89c6-4e89-9417-5ffe725c1bc6 | r1 | 1 | 0.9394 | deepseek-v4-flash-vision-exp | 1 | 0.9394 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-401a07f1-d57e-4bb0-889b-22de8c900f0e | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-43dc9778-450b-4b46-b77e-b6d82b202035 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-4520f882-715a-482d-8e87-1cb3cbdfe975 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-47ef842d-8eac-4b90-bda8-dd934c228c96 | r1 | 0 | 0.1010 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 0 |  |
| e0b-b2-gdpval-p2 | gdpval-4b98ccce-9e42-44e9-9115-6fc3e79de288 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9808 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-61f546a8-c374-467f-95cc-d0d9b5656eb6 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-76d10872-9ffa-4ede-83ee-e0f1ec5e2b8d | r1 | 1 | 0.9595 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-7bbfcfe9-132d-4194-82bb-d6f29d001b01 | r1 | 0 | 0.4528 | deepseek-v4-flash-vision-exp | 0 | 0.4717 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-85d95ce5-b20c-41e2-834e-e788ce9622b6 | r1 | 1 | 0.9176 | deepseek-v4-flash-vision-exp | 1 | 0.6588 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-9e39df84-ac57-4c9b-a2e3-12b8abf2c797 | r1 | 1 | 0.7083 | deepseek-v4-flash-vision-exp | 1 | 0.7083 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-aad21e4c-1d43-45fc-899a-97754a1b1b63 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-ab81b076-e5d8-473a-9bdb-7ea7c38f6ebc | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9615 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-b1a79ce1-86b0-41fb-97dc-9206dfd7b044 | r1 | 1 | 0.8302 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-bb499d9c-0263-4684-9238-75e8e86077b1 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-be830ca0-b352-4658-a5bd-57139d6780ba | r1 | 1 | 0.9221 | deepseek-v4-flash-vision-exp | 1 | 0.7403 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-cecac8f9-8203-4ebd-ad49-54436a8c4171 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9600 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-e14e32ba-d310-4d45-9b8a-6d73d0ece1ae | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9310 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-ec591973-04d5-48c0-981c-1ab2fcec2dc1 | r1 | 1 | 0.7532 | deepseek-v4-flash-vision-exp | 1 | 0.7597 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-efca245f-c24f-4f75-a9d5-59201330ab7a | r1 | 1 | 0.9902 | deepseek-v4-flash-vision-exp | 1 | 0.9804 | 1 | 1 |  |
| e0b-b2-gdpval-p2 | gdpval-f3351922-dbdd-45da-85c5-e7110696bbe5 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9412 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-01d7e53e-0513-4109-a242-8ccaf442cd21 | r1 | 1 | 0.9524 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-0353ee0c-18b5-4ad3-88e8-e001d223e1d7 | r1 | 0 | 0.4679 | deepseek-v4-flash-vision-exp | 1 | 0.9908 | 1 | 0 |  |
| e0b-b2-gdpval-p3 | gdpval-045aba2e-4093-42aa-ab7f-159cc538278c | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9726 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-05389f78-589a-473c-a4ae-67c61050bfca | r1 | 1 | 0.9773 | deepseek-v4-flash-vision-exp | 1 | 0.9773 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-11593a50-734d-4449-b5b4-f8986a133fd8 | r1 | 0 | 0.5283 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 0 |  |
| e0b-b2-gdpval-p3 | gdpval-116e791e-890c-42b1-ba90-1db02e8bfd45 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9531 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-19403010-3e5c-494e-a6d3-13594e99f6af | r1 | 1 | 0.6371 | deepseek-v4-flash-vision-exp | 1 | 0.7097 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-22c0809b-f8db-489e-93b3-b4da225e3e0e | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-2d06bc0a-89c6-4e89-9417-5ffe725c1bc6 | r1 | 1 | 0.9697 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-401a07f1-d57e-4bb0-889b-22de8c900f0e | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-43dc9778-450b-4b46-b77e-b6d82b202035 | r1 | 0 | 0.3223 | deepseek-v4-flash-vision-exp | 0 | 0.3719 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-4520f882-715a-482d-8e87-1cb3cbdfe975 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-47ef842d-8eac-4b90-bda8-dd934c228c96 | r1 | 0 | 0.3737 | deepseek-v4-flash-vision-exp | 1 | 0.9293 | 1 | 0 |  |
| e0b-b2-gdpval-p3 | gdpval-4b98ccce-9e42-44e9-9115-6fc3e79de288 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-61f546a8-c374-467f-95cc-d0d9b5656eb6 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-76d10872-9ffa-4ede-83ee-e0f1ec5e2b8d | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-7bbfcfe9-132d-4194-82bb-d6f29d001b01 | r1 | 0 | 0.5849 | deepseek-v4-flash-vision-exp | 0 | 0.5660 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-85d95ce5-b20c-41e2-834e-e788ce9622b6 | r1 | 1 | 0.9412 | deepseek-v4-flash-vision-exp | 1 | 0.8588 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-9e39df84-ac57-4c9b-a2e3-12b8abf2c797 | r1 | 0 | 0.5000 | deepseek-v4-flash-vision-exp | 0 | 0.4583 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-aad21e4c-1d43-45fc-899a-97754a1b1b63 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-ab81b076-e5d8-473a-9bdb-7ea7c38f6ebc | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9038 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-b1a79ce1-86b0-41fb-97dc-9206dfd7b044 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9811 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-bb499d9c-0263-4684-9238-75e8e86077b1 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-be830ca0-b352-4658-a5bd-57139d6780ba | r1 | 1 | 0.6883 | deepseek-v4-flash-vision-exp | 1 | 0.7662 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-cecac8f9-8203-4ebd-ad49-54436a8c4171 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-e14e32ba-d310-4d45-9b8a-6d73d0ece1ae | r1 | 1 | 0.6897 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-ec591973-04d5-48c0-981c-1ab2fcec2dc1 | r1 | 1 | 0.9091 | deepseek-v4-flash-vision-exp | 1 | 0.7208 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-efca245f-c24f-4f75-a9d5-59201330ab7a | r1 | 1 | 0.9412 | deepseek-v4-flash-vision-exp | 1 | 0.9412 | 1 | 1 |  |
| e0b-b2-gdpval-p3 | gdpval-f3351922-dbdd-45da-85c5-e7110696bbe5 | r1 | 1 | 0.9882 | deepseek-v4-flash-vision-exp | 1 | 0.9882 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-01d7e53e-0513-4109-a242-8ccaf442cd21 | r1 | 1 | 0.9286 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-0353ee0c-18b5-4ad3-88e8-e001d223e1d7 | r1 | 1 | 0.7982 | deepseek-v4-flash-vision-exp | 1 | 0.9908 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-045aba2e-4093-42aa-ab7f-159cc538278c | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9863 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-05389f78-589a-473c-a4ae-67c61050bfca | r1 | 1 | 0.7045 | deepseek-v4-flash-vision-exp | 1 | 0.7614 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-11593a50-734d-4449-b5b4-f8986a133fd8 | r1 | 0 | 0.5283 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 0 |  |
| e0b-b2-gdpval-p4 | gdpval-116e791e-890c-42b1-ba90-1db02e8bfd45 | r1 | 1 | 0.9219 | deepseek-v4-flash-vision-exp | 1 | 0.9688 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-19403010-3e5c-494e-a6d3-13594e99f6af | r1 | 1 | 0.6371 | deepseek-v4-flash-vision-exp | 1 | 0.7258 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-22c0809b-f8db-489e-93b3-b4da225e3e0e | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-2d06bc0a-89c6-4e89-9417-5ffe725c1bc6 | r1 | 1 | 0.9697 | deepseek-v4-flash-vision-exp | 1 | 0.9697 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-401a07f1-d57e-4bb0-889b-22de8c900f0e | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-43dc9778-450b-4b46-b77e-b6d82b202035 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-4520f882-715a-482d-8e87-1cb3cbdfe975 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-47ef842d-8eac-4b90-bda8-dd934c228c96 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-4b98ccce-9e42-44e9-9115-6fc3e79de288 | r1 | 0 | 0.4808 | deepseek-v4-flash-vision-exp | 1 | 0.9904 | 1 | 0 |  |
| e0b-b2-gdpval-p4 | gdpval-61f546a8-c374-467f-95cc-d0d9b5656eb6 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-76d10872-9ffa-4ede-83ee-e0f1ec5e2b8d | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-7bbfcfe9-132d-4194-82bb-d6f29d001b01 | r1 | 0 | 0.5472 | deepseek-v4-flash-vision-exp | 0 | 0.5283 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-85d95ce5-b20c-41e2-834e-e788ce9622b6 | r1 | 1 | 0.9412 | deepseek-v4-flash-vision-exp | 1 | 0.9412 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-9e39df84-ac57-4c9b-a2e3-12b8abf2c797 | r1 | 1 | 0.9583 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-aad21e4c-1d43-45fc-899a-97754a1b1b63 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9764 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-ab81b076-e5d8-473a-9bdb-7ea7c38f6ebc | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-b1a79ce1-86b0-41fb-97dc-9206dfd7b044 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9811 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-bb499d9c-0263-4684-9238-75e8e86077b1 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9775 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-be830ca0-b352-4658-a5bd-57139d6780ba | r1 | 1 | 0.7143 | deepseek-v4-flash-vision-exp | 1 | 0.7403 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-cecac8f9-8203-4ebd-ad49-54436a8c4171 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-e14e32ba-d310-4d45-9b8a-6d73d0ece1ae | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-ec591973-04d5-48c0-981c-1ab2fcec2dc1 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.8377 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-efca245f-c24f-4f75-a9d5-59201330ab7a | r1 | 1 | 0.9804 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p4 | gdpval-f3351922-dbdd-45da-85c5-e7110696bbe5 | r1 | 1 | 0.9882 | deepseek-v4-flash-vision-exp | 1 | 0.9529 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-01d7e53e-0513-4109-a242-8ccaf442cd21 | r1 | 1 | 0.7857 | deepseek-v4-flash-vision-exp | 1 | 0.7619 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-0353ee0c-18b5-4ad3-88e8-e001d223e1d7 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-045aba2e-4093-42aa-ab7f-159cc538278c | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9589 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-05389f78-589a-473c-a4ae-67c61050bfca | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9886 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-11593a50-734d-4449-b5b4-f8986a133fd8 | r1 | 0 | 0.4528 | deepseek-v4-flash-vision-exp | 1 | 0.8868 | 1 | 0 |  |
| e0b-b2-gdpval-p5 | gdpval-116e791e-890c-42b1-ba90-1db02e8bfd45 | r1 | 1 | 0.7656 | deepseek-v4-flash-vision-exp | 1 | 0.7500 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-19403010-3e5c-494e-a6d3-13594e99f6af | r1 | 1 | 0.6210 | deepseek-v4-flash-vision-exp | 1 | 0.7500 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-22c0809b-f8db-489e-93b3-b4da225e3e0e | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-2d06bc0a-89c6-4e89-9417-5ffe725c1bc6 | r1 | 1 | 0.9697 | deepseek-v4-flash-vision-exp | 1 | 0.9697 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-401a07f1-d57e-4bb0-889b-22de8c900f0e | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-43dc9778-450b-4b46-b77e-b6d82b202035 | r1 | 0 | 0.1901 | deepseek-v4-flash-vision-exp | 0 | 0.2975 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-4520f882-715a-482d-8e87-1cb3cbdfe975 | r1 | 0 | 0.5486 | deepseek-v4-flash-vision-exp | 0 | 0.5257 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-47ef842d-8eac-4b90-bda8-dd934c228c96 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9596 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-4b98ccce-9e42-44e9-9115-6fc3e79de288 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-5f6c57dd-feb6-4e70-b152-4969d92d1608 | r1 | 1 | 0.9594 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-61f546a8-c374-467f-95cc-d0d9b5656eb6 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-76d10872-9ffa-4ede-83ee-e0f1ec5e2b8d | r1 | 1 | 0.9527 | deepseek-v4-flash-vision-exp | 1 | 0.9662 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-7bbfcfe9-132d-4194-82bb-d6f29d001b01 | r1 | 0 | 0.5849 | deepseek-v4-flash-vision-exp | 0 | 0.5660 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-85d95ce5-b20c-41e2-834e-e788ce9622b6 | r1 | 1 | 0.9412 | deepseek-v4-flash-vision-exp | 1 | 0.9412 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-9e39df84-ac57-4c9b-a2e3-12b8abf2c797 | r1 | 0 | 0.5000 | deepseek-v4-flash-vision-exp | 0 | 0.5000 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-aad21e4c-1d43-45fc-899a-97754a1b1b63 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-ab81b076-e5d8-473a-9bdb-7ea7c38f6ebc | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9423 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-b1a79ce1-86b0-41fb-97dc-9206dfd7b044 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9434 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-bb499d9c-0263-4684-9238-75e8e86077b1 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9775 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-be830ca0-b352-4658-a5bd-57139d6780ba | r1 | 1 | 0.6883 | deepseek-v4-flash-vision-exp | 1 | 0.7532 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-cecac8f9-8203-4ebd-ad49-54436a8c4171 | r1 | 1 | 0.8800 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-e14e32ba-d310-4d45-9b8a-6d73d0ece1ae | r1 | 1 | 0.7586 | deepseek-v4-flash-vision-exp | 1 | 0.9310 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-ec591973-04d5-48c0-981c-1ab2fcec2dc1 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.8506 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-efca245f-c24f-4f75-a9d5-59201330ab7a | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 1.0000 | 1 | 1 |  |
| e0b-b2-gdpval-p5 | gdpval-f3351922-dbdd-45da-85c5-e7110696bbe5 | r1 | 1 | 1.0000 | deepseek-v4-flash-vision-exp | 1 | 0.9647 | 1 | 1 |  |

### E0b: baseline

| source | run_id | tasks | rollouts | rollout_pass_rate | pass_hat_k_rate |
|---|---|---|---|---|---|
| browsecomp | e0b-b1-browsecomp-p1 | 10 | 10 | 0.7000 | 0.7000 |
| browsecomp | e0b-b1-browsecomp-p2 | 10 | 10 | 0.6667 | 0.6000 |
| hle | e0b-b1-hle-p1 | 32 | 32 | 0.5312 | 0.5312 |
| hle | e0b-b1-hle-p2 | 32 | 32 | 0.4375 | 0.4375 |
| gdpval | e0b-b1-gdpval-p1 | 56 | 56 | 0.8182 | 0.8036 |
| gdpval | e0b-b1-gdpval-p2 | 56 | 56 | 0.8750 | 0.8750 |
| claw_eval | e0b-b1-claw_eval-p1 | 56 | 168 | 0.8155 | 0.7143 |
| claw_eval | e0b-b1-claw_eval-p2 | 56 | 168 | 0.8274 | 0.7500 |

### E0b: A/A bands

| source | split | tasks | agreement | delta_points | ci95_low | ci95_high |
|---|---|---|---|---|---|---|
| browsecomp | validation | 10 | 0.7000 | 10.00 | 0.00 | 40.00 |
| hle | validation | 32 | 0.7188 | 9.38 | 0.00 | 28.12 |
| gdpval | validation | 56 | 0.8571 | 7.14 | 0.00 | 16.07 |
| gdpval | heldout | 30 | 0.8667 | 6.67 | 0.00 | 20.00 |
| claw_eval | validation | 56 | 0.8929 | 3.57 | 0.00 | 12.50 |
| claw_eval | heldout | 30 | 0.9000 | 3.33 | 0.00 | 13.33 |

### E0b: failure types

| source | failure_type | count |
|---|---|---|
| browsecomp | deterministic | 0 |
| browsecomp | stochastic | 0 |
| browsecomp | unrepairable | 0 |
| browsecomp | unreplayable | 0 |
| hle | deterministic | 0 |
| hle | stochastic | 0 |
| hle | unrepairable | 0 |
| hle | unreplayable | 0 |
| gdpval | deterministic | 1 |
| gdpval | stochastic | 3 |
| gdpval | unrepairable | 4 |
| gdpval | unreplayable | 2 |
| claw_eval | deterministic | 24 |
| claw_eval | stochastic | 5 |
| claw_eval | unrepairable | 8 |
| claw_eval | unreplayable | 0 |

### E0b: references

| source | failed_tasks | references | genuine | shortcut | undetermined |
|---|---|---|---|---|---|
| browsecomp | 4 | 0 | 0 | 0 | 0 |
| hle | 21 | 0 | 0 | 0 | 0 |
| gdpval | 13 | 13 | 11 | 2 | 0 |
| claw_eval | 18 | 23 | 23 | 0 | 0 |

### E0b: replay

| source | failures_replayed | oracle_validated | deterministic | stochastic | unrepairable | unreplayable | mean_candidates | full_arms_failures | control_pass_fraction_mean |
|---|---|---|---|---|---|---|---|---|---|
| browsecomp | 0 | 0 | 0 | 0 | 0 | 0 |  | 0 |  |
| hle | 0 | 0 | 0 | 0 | 0 | 0 |  | 0 |  |
| gdpval | 11 | 4 | 1 | 3 | 4 | 2 | 5.00 | 10 | 0.4306 |
| claw_eval | 40 | 29 | 24 | 5 | 8 | 0 | 3.77 | 12 | 0.2171 |

### E0b: clusters

| source | n_clusters | sizes | singleton_fraction | deterministic_fraction | clusters_with_two |
|---|---|---|---|---|---|
| gdpval | 3 | [2, 1, 1] | 0.6667 | 0.3333 | 1 |
| claw_eval | 15 | [5, 4, 3, 3, 3, 2, 1, 1, 1, 1, 1, 1, 1, 1, 1] | 0.6000 | 0.8000 | 6 |

### E0b: leakage

| source | run_id | n | top1 | top3 | chance_top1 |
|---|---|---|---|---|---|
| gdpval | e0b-b1-gdpval-p1 | 2 | 0.5000 | 0.5000 | 0.0556 |
| gdpval | e0b-b1-gdpval-p2 | 1 | 0.0000 | 0.0000 | 0.0556 |
| claw_eval | e0b-b1-claw_eval-p1 | 11 | 0.4545 | 0.5455 | 0.0556 |
| claw_eval | e0b-b1-claw_eval-p2 | 8 | 0.1250 | 0.6250 | 0.0556 |

### E0b: judge calibration

| judge | artifacts | rejudged | self_consistency | released_labels |
|---|---|---|---|---|
| text (deepseek-v4-pro) | 69 | 69 | 0.9855 | none: the Evo-Bench snapshot releases expected answers and rubrics, no per-trajectory judge labels |
| vision (gdpval secondary) | 44 | 41 | 1.0000 | none: the Evo-Bench snapshot releases expected answers and rubrics, no per-trajectory judge labels |

## E0d calibration addendum (M3.2)

E0c (reasoning_effort low) status: **not_run** (max condition only; E0c follows E0d). E0d spend cap 40.0 USD; stage order ['B', 'C', 'D', 'A'].

### E0d-A: seed noise band (held-out passes of the seed harness)

| source | passes | mean | sigma_seed | pairs | |d| mean | p90 | p95 |
|---|---|---|---|---|---|---|---|
| browsecomp | 0 |  |  |  |  |  |  |
| hle | 0 |  |  |  |  |  |  |
| gdpval | 5 | 82.67 | 4.35 | 10 | 5.33 | 10.00 | 10.00 |
| claw_eval | 8 | 67.08 | 3.75 | 28 | 4.17 | 10.00 | 10.00 |

### E0d-B: replay escalation (marginal fixed-k verdicts re-opened)

| source | run | failures | candidates | type changed | oracle changed | transitions | usd |
|---|---|---|---|---|---|---|---|
| gdpval | e0b-b1-gdpval-p1 | 5 | 5 | 3 | 3 | {"negative->negative": 1, "positive->negative": 1, "positive->positive": 1, "positive->unresolved": 1, "unresolved->negative": 1} | 4.6576 |
| gdpval | e0b-b1-gdpval-p2 | 1 | 1 | 0 | 0 | {"unresolved->negative": 1} | 0.7255 |
| claw_eval | e0b-b1-claw_eval-p1 | 12 | 19 | 3 | 5 | {"negative->negative": 5, "positive->negative": 2, "positive->positive": 5, "positive->unresolved": 1, "unresolved->negative": 3, "unresolved->positive": 1, "unresolved->unresolved": 2} | 2.5185 |
| claw_eval | e0b-b1-claw_eval-p2 | 10 | 15 | 3 | 5 | {"negative->negative": 2, "negative->positive": 1, "positive->negative": 1, "positive->positive": 5, "positive->unresolved": 2, "unresolved->negative": 1, "unresolved->unresolved": 3} | 5.4677 |

### E0d-B: failure types before and after M3.2

| source | failure_type | before | after |
|---|---|---|---|
| gdpval | deterministic | 3 | 1 |
| gdpval | stochastic | 2 | 3 |
| gdpval | unrepairable | 4 | 4 |
| gdpval | unreplayable | 2 | 2 |
| gdpval | unresolved | 0 | 1 |
| gdpval | candidates_confirmed_n>=5 |  | 6 |
| gdpval | candidates_unconfirmed_fixed_k |  | 23 |
| claw_eval | deterministic | 26 | 24 |
| claw_eval | stochastic | 7 | 5 |
| claw_eval | unrepairable | 7 | 8 |
| claw_eval | unreplayable | 0 | 0 |
| claw_eval | unresolved | 0 | 3 |
| claw_eval | candidates_confirmed_n>=5 |  | 34 |
| claw_eval | candidates_unconfirmed_fixed_k |  | 107 |

### E0d-C: COH-WRONG plausibility parity (per source, pooled over passes)

| source | rounds | final seed | n | mean diff | ref> | coh> | ties | p | status |
|---|---|---|---|---|---|---|---|---|---|
| gdpval | 1 | 0 | 1 | 0.0000 | 0 | 0 | 1 |  | ok |
| claw_eval | 1 | 0 | 7 | 0.1429 | 2 | 1 | 4 | 1.000 | ok |

### E0d-C: COH-WRONG assignments and scores

| source | run | cluster | decoy | step | basis | cause | sev | gen | ref | coh | error |
|---|---|---|---|---|---|---|---|---|---|---|---|
| gdpval | e0b-b1-gdpval-p1 | c411077d9 | wiring | 4 | validated_negative | contract_violation | high | 0 | 1 | 1 |  |
| claw_eval | e0b-b1-claw_eval-p1 | cd68528c8 | budget | 1 | validated_negative | budget_exhaustion | high | 0 | 1 | 1 |  |
| claw_eval | e0b-b1-claw_eval-p1 | c734034d9 | tool_shell | 3 | validated_negative | over_exploration | high | 0 | 1 | 1 |  |
| claw_eval | e0b-b1-claw_eval-p1 | c27348614 | tool_shell | 6 | validated_negative | over_exploration | medium | 0 | 1 | 1 |  |
| claw_eval | e0b-b1-claw_eval-p2 | cbafea507 | error_handling | 2 | validated_negative | error_recovery | high | 0 | 4 | 1 |  |
| claw_eval | e0b-b1-claw_eval-p2 | ce078740c | tool_shell | 1 | validated_negative | insufficient_evidence | high | 0 | 2 | 1 |  |
| claw_eval | e0b-b1-claw_eval-p2 | c34bd8091 | entry | 2 | validated_negative | wrong_target | high | 0 | 2 | 5 |  |
| claw_eval | e0b-b1-claw_eval-p2 | c16ae51b7 | system_prompt | 5 | validated_negative | contract_violation | high | 0 | 1 | 1 |  |

### E0d-D: privileged-information probe (recovery from the rendered diagnosis alone)

| source | arm | n | top1 | top3 | origin top1 | tool P | tool R | category | chance1 | chance3 |
|---|---|---|---|---|---|---|---|---|---|---|
| gdpval | coherent_wrong | 1 | 0.0000 | 0.0000 | 0.0000 | 1.0000 | 1.0000 | 0.0000 | 0.0179 | 0.0536 |
| gdpval | reference | 3 | 0.0000 | 0.3333 | 0.0000 | 1.0000 | 1.0000 | 0.3333 | 0.0179 | 0.0536 |
| gdpval | shuffled | 2 | 0.0000 | 0.5000 | 0.0000 | 1.0000 | 1.0000 | 0.5000 | 0.0179 | 0.0536 |
| gdpval | system | 3 | 0.3333 | 0.3333 | 0.3333 | 1.0000 | 1.0000 | 0.3333 | 0.0179 | 0.0536 |
| claw_eval | coherent_wrong | 7 | 0.4286 | 0.8571 | 0.4286 | 0.6571 | 0.7857 | 0.7143 | 0.0179 | 0.0536 |
| claw_eval | reference | 19 | 0.5263 | 0.8421 | 0.5263 | 0.7518 | 0.7395 | 0.6842 | 0.0179 | 0.0536 |
| claw_eval | shuffled | 19 | 0.1053 | 0.3684 | 0.4211 | 0.3878 | 0.3640 | 0.3158 | 0.0179 | 0.0536 |
| claw_eval | system | 19 | 0.5263 | 0.8421 | 0.5263 | 0.6711 | 0.6544 | 0.7368 | 0.0179 | 0.0536 |

### E0d-E: component ambiguity (attribution records of the reference arm)

| source | failures | rule | llm | candidate-set sizes | clusters | component_unique |
|---|---|---|---|---|---|---|
| gdpval | 4 | 0.0000 | 1.0000 | {"3": 3, "4": 1} | 3 | 0 |
| claw_eval | 29 | 0.0345 | 0.9655 | {"1": 1, "2": 3, "3": 19, "4": 6} | 15 | 1 |

### M3.2: decoy exclusion audit (old rule vs full candidate set)

Old-rule decoys that sat inside the candidate set: 4 of 44 (cluster, tier) draws.

| source | clusters | lose near | lose far |
|---|---|---|---|
| gdpval | 3 | 0 | 0 |
| claw_eval | 19 | 0 | 0 |

### E0d-F: minimum detectable effect (cluster-level sign-flip test, alpha 0.05, power 0.8)

Cost model: 11 arms x N x k x (proposal call + passes x held-out pass); inputs from the E0b ledgers.

| source | N | k | passes | sigma | spread | MDE | cost | <=3 |
|---|---|---|---|---|---|---|---|---|
| claw_eval | 7 | 3 | 1 | 3.75 | 5.00 | 7.50 | 451.05 | 0 |
| claw_eval | 7 | 3 | 2 | 3.75 | 5.00 | 7.00 | 900.93 | 0 |
| claw_eval | 7 | 5 | 1 | 3.75 | 5.00 | 7.00 | 751.74 | 0 |
| claw_eval | 7 | 5 | 2 | 3.75 | 5.00 | 7.25 | 1501.56 | 0 |
| claw_eval | 8 | 3 | 1 | 3.75 | 5.00 | 6.75 | 515.48 | 0 |
| claw_eval | 8 | 3 | 2 | 3.75 | 5.00 | 6.50 | 1029.64 | 0 |
| claw_eval | 8 | 5 | 1 | 3.75 | 5.00 | 6.50 | 859.14 | 0 |
| claw_eval | 8 | 5 | 2 | 3.75 | 5.00 | 6.25 | 1716.06 | 0 |
| claw_eval | 10 | 3 | 1 | 3.75 | 5.00 | 5.75 | 644.35 | 0 |
| claw_eval | 10 | 3 | 2 | 3.75 | 5.00 | 5.50 | 1287.05 | 0 |
| claw_eval | 10 | 5 | 1 | 3.75 | 5.00 | 5.50 | 1073.92 | 0 |
| claw_eval | 10 | 5 | 2 | 3.75 | 5.00 | 5.25 | 2145.08 | 0 |
| claw_eval | 14 | 3 | 1 | 3.75 | 5.00 | 4.75 | 902.09 | 0 |
| claw_eval | 14 | 3 | 2 | 3.75 | 5.00 | 4.25 | 1801.87 | 0 |
| claw_eval | 14 | 5 | 1 | 3.75 | 5.00 | 4.75 | 1503.49 | 0 |
| claw_eval | 14 | 5 | 2 | 3.75 | 5.00 | 4.25 | 3003.11 | 0 |
| gdpval | 7 | 3 | 1 | 4.35 | 5.00 | 7.50 | 597.70 | 0 |
| gdpval | 7 | 3 | 2 | 4.35 | 5.00 | 7.25 | 1193.56 | 0 |
| gdpval | 7 | 5 | 1 | 4.35 | 5.00 | 7.25 | 996.17 | 0 |
| gdpval | 7 | 5 | 2 | 4.35 | 5.00 | 7.25 | 1989.27 | 0 |
| gdpval | 8 | 3 | 1 | 4.35 | 5.00 | 7.00 | 683.09 | 0 |
| gdpval | 8 | 3 | 2 | 4.35 | 5.00 | 6.50 | 1364.07 | 0 |
| gdpval | 8 | 5 | 1 | 4.35 | 5.00 | 6.50 | 1138.48 | 0 |
| gdpval | 8 | 5 | 2 | 4.35 | 5.00 | 6.25 | 2273.45 | 0 |
| gdpval | 10 | 3 | 1 | 4.35 | 5.00 | 6.00 | 853.86 | 0 |
| gdpval | 10 | 3 | 2 | 4.35 | 5.00 | 5.50 | 1705.09 | 0 |
| gdpval | 10 | 5 | 1 | 4.35 | 5.00 | 5.50 | 1423.10 | 0 |
| gdpval | 10 | 5 | 2 | 4.35 | 5.00 | 5.25 | 2841.81 | 0 |
| gdpval | 14 | 3 | 1 | 4.35 | 5.00 | 4.75 | 1195.40 | 0 |
| gdpval | 14 | 3 | 2 | 4.35 | 5.00 | 4.50 | 2387.12 | 0 |
| gdpval | 14 | 5 | 1 | 4.35 | 5.00 | 4.75 | 1992.34 | 0 |
| gdpval | 14 | 5 | 2 | 4.35 | 5.00 | 4.25 | 3978.54 | 0 |

### E0d-G: measurability funnel (per source; feasibility counts per-run clusters)

| source | rollouts | failed_rollouts | failed_tasks | genuine_references | replayable | validated_positive | validated_negative | unresolved | unreplayable | clusters | clusters_ge2 | feasible_where_near | feasible_where_far | feasible_why | feasible_how | feasible_shuffled | feasible_coherent_wrong |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| browsecomp | 20 | 6 | 4 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| hle | 64 | 33 | 21 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| gdpval | 112 | 17 | 13 | 11 | 11 | 1 | 7 | 1 | 2 | 3 | 1 | 3 | 3 | 2 | 2 | 2 | 3 |
| claw_eval | 336 | 60 | 18 | 23 | 40 | 24 | 13 | 3 | 0 | 15 | 6 | 19 | 19 | 19 | 19 | 19 | 19 |

## Incidents and operator interventions

| ts | kind | detail |
|---|---|---|
| 2026-09-07T02:27 | code_fix | full-arms subset keys had the wrong format; the first gdpval-p1 full-arms pass matched nothing and was redone (commit fd43063) |
| 2026-09-07T02:49 | pause | owner-requested checkpoint; runner stopped cleanly, resumed 02:55 with no loss |
| 2026-09-07T05:58 | code_change | replay candidates run in parallel under one semaphore bounded by workers (commit db5807f); runner restarted, one in-flight rollout lost |
| 2026-09-07T07:59 | reorder | B7 judge calibration run in a separate process ahead of the remaining replays so the hard cap could not starve it |
| 2026-09-07T18:31 | reuse | gdpval-p2 full-arms subset: 75 finished substitute rollouts copied from the economize pass so only control arms ran (same measurement, no second draw of the substitute arm) |
| 2026-09-07T22:09 | data_loss | reference run e0b-b1-claw_eval-p1-ref deleted by a replayed policy command (`rm -rf <reference run dir>` after a substituted action wrote there with an absolute path); claw-p1 alignment and 186 replay rollouts discarded and redone against a fresh reference; fix: replayed commands and restored context now remap recorded/reference workspace paths to the replay workspace, reference runs are locked read-only after genuineness |
| 2026-09-07T22:20 | validity_caveat | before the remap fix, replayed actions carried absolute paths of the recorded run's workspace (gdpval-p1: 396 of 927 actions; gdpval-p2: 552 of 942; claw-p1: 39 of 330; pilot browsecomp: 30 of 186); prefix state and some continuation writes therefore landed outside the replay workspace, biasing replay outcomes of those runs; a re-run under the fixed instrument is the owner's decision |
| 2026-09-08T02:31 | code_fix | report: the pass glob e0b-b1-<source>-p* also matched the -ref reference runs, so the A/A band paired pass 1 with its reference run and the D1 pass rate averaged reference retries in (caught on a dry run before the final report; no run data affected). Feasibility of clusters merged across passes is now matched by (cause, component) instead of the member-hash id. |
| 2026-09-08T03:27 | disconnect | editor session disconnected between 02:31 and 03:26 UTC and took the detached runner with it during the claw-p2 replay; 302 replay rollouts kept (done.json markers), 4 in-flight rollouts of one task redone on resume; runner restarted from the same commit |
| 2026-09-08T03:54 | deviation | B7 judge calibration ran twice with different candidate pools: the separate --stages B7 run pooled artifacts through the same p* glob and drew 3 of its 50 from e0b-b1-gdpval-p1-ref; the resumed runner pooled from the seed passes only and re-judged its own draw of 50 (cache bypassed). judge_calibration.json keeps both draws, so the report's self-consistency uses more than the spec's n=50 artifacts; the reference-run artifacts are seed-policy rollouts on the same tasks |
| 2026-09-08T10:56 | start | E0d launched at commit 78c7b9d (tag m3.2; the addendum spec amendment landed in the same commit because the two-commit split failed on a pre-commit mypy run). Order B C D A, cap 40 USD on E0d ledgers only; E0c not run. |
| 2026-09-08T13:00 | restart | E0d runner restarted at commit 4f22769 to escalate marginal failures and candidates concurrently (the first run serialised them: 3 GDPval failures in 2 h); finished escalation rows and rollouts with done.json are reused, 2 in-flight GDPval rollouts lost |

## Decision rules

**D1.** Source enters E2 iff: seed pass rate on the mining pool (validation U eval_dev) in [0.10, 0.90] (headroom both ways) AND per-source A/A |delta| <= 5 points AND >= 6 clusters with >= 2 members. (Amended: computed on the mining pool, not validation alone.)

**D1prime.** E2 starts only if total primary clusters across included sources >= 8; otherwise pivot options are exactly: (i) policy reasoning_effort low (deviation stated), (ii) build M2b web freeze and re-admit browsecomp. No other option may be added.

**D2.** A cluster is E2-primary iff >= 2 members, >= 1 genuine reference, oracle step validated (deterministic) or manifestation-based (stochastic), and near+far+why+how+all all feasible. Singletons and clusters failing any condition form the secondary pool.

**D3.** Search stays in E2 only if D1 holds AND its A/A band is not wider than the other sources' by more than 2x; otherwise Search is reported separately as descriptive.

**D4.** Replicates in E2: k=3 if projected E2 cost (from cost.csv, 8 arms x N primary clusters x k) <= owner budget entered in spec.yaml before running; else k=2; N is all primary clusters, never topped up.

**D5.** Held-out size: 30/source unless the A/A band on held-out exceeds 5 points, in which case 45/source (re-sampled from the same frozen seed, superset).

**D6.** Judge: if self-consistency < 0.90, the E2 primary outcome uses a 2-of-3 judge vote (cost recorded). (The Flash text re-judge is replaced by P1; cross-judge agreement is the gdpval text-vs-vision comparison under D7.)

**D7.** The E2 primary judge for gdpval is the vision judge iff its self-consistency >= 0.90 AND text-vs-vision disagreement >= 10% of artifacts (i.e. the text judge is missing rubric evidence); otherwise the text judge stays primary and the vision verdict is reported as secondary. Whichever is chosen is frozen in the E2 spec and applies to every gdpval arm; Claw keeps V4 Pro. Judge model per source is recorded in every manifest.

**D8.** delta_meaningful = 3 points. E2 uses the cheapest (N, k, passes) configuration with MDE <= 3 if one exists within owner_budget_usd; otherwise the best available configuration, and the paper states the exclusion bound (the smallest effect the design would have detected) rather than a null.

| rule | observed | decision |
|---|---|---|
| D1:browsecomp | pass_rate=0.6833 aa_delta=10.00 clusters_with_two=0 | excluded |
| D1:hle | pass_rate=0.4844 aa_delta=9.38 clusters_with_two=0 | excluded |
| D1:gdpval | pass_rate=0.8466 aa_delta=7.14 clusters_with_two=1 | excluded |
| D1:claw_eval | pass_rate=0.8214 aa_delta=3.57 clusters_with_two=6 | enters E2 |
| D2:browsecomp | primary_clusters=0 | 0 primary; rest secondary |
| D2:hle | primary_clusters=0 | 0 primary; rest secondary |
| D2:gdpval | primary_clusters=1 | 1 primary; rest secondary |
| D2:claw_eval | primary_clusters=6 | 6 primary; rest secondary |
| D3:browsecomp | aa_delta=10.00 widest_other=7.14 | descriptive only |
| D3:hle | aa_delta=9.38 widest_other=7.14 | descriptive only |
| D1prime | primary_clusters_in_included_sources=6 (min 8); included=['claw_eval'] | pivot required: (i) policy reasoning_effort low (deviation stated) or (ii) build M2b web freeze and re-admit browsecomp |
| D4 | projected_usd(k=3)=10.79 budget=600.0 | k=3 |
| D5:browsecomp | no held-out A/A | not evaluable |
| D5:hle | no held-out A/A | not evaluable |
| D5:gdpval | heldout_delta=6.67 | 45/source |
| D5:claw_eval | heldout_delta=3.33 | 30/source |
| D6 | text_self_consistency=0.9855 (Flash re-judge replaced by P1) | single judge |
| D7 | vision_self_consistency=1.0000 text_vs_vision_disagreement=0.0625 compared=256 | gdpval E2 primary judge = text (deepseek-v4-pro); vision reported as secondary |
| D8:claw_eval | N=8 k=3 passes=1 MDE=6.75 points cost=515.48 USD (delta_meaningful=3.0) | no configuration within owner_budget_usd reaches delta_meaningful: best available within the budget; the paper states the exclusion bound 6.75 points |
| D8:gdpval | N=7 k=3 passes=1 MDE=7.50 points cost=597.70 USD (delta_meaningful=3.0) | no configuration within owner_budget_usd reaches delta_meaningful: best available within the budget; the paper states the exclusion bound 7.50 points |
| COH-WRONG:claw_eval | parity ok | arm admitted |
| COH-WRONG:gdpval | parity ok | arm admitted |
