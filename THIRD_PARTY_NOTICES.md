# Third-party notices

Reference repositories surveyed for `ahd` (agent harnesses diagnostic). Clones live
outside this repo at `~/work/refs/<name>`; the commit sha recorded here is the one
surveyed and is the sha that any header comment in reused/adapted files must cite.

The "Files reused" column is filled in per module (see `docs/reuse/M*.md`) as code is
borrowed; the only copied tree is the M3 replay instrument (Evo-Bench seed harness, Apache-2.0).

| Repo | URL | Default branch | Commit sha (surveyed 2026-09-04) | License | Files reused |
|---|---|---|---|---|---|
| meta-harness | https://github.com/stanford-iris-lab/meta-harness | main | `44b9942127847f7421db70d8c7e48407f09a3c70` | MIT (Copyright (c) 2026 Yoonho Lee) |`src/ahd/llm/cache.py` (key derivation, file-per-key layout) |
| agentic-harness-engineering | https://github.com/china-qijizhifeng/agentic-harness-engineering | main | `8b2a55d97590363fe50c3cc6b5e833b020a4bb4c` | MIT (Copyright (c) 2026 Jiahang Lin) |none (masking rule in `src/ahd/settings.py` follows `evolve.py:48-54` as a pattern) |
| AutoSaddler | https://github.com/microsoft/AutoSaddler | main | `30e20ce004486c58e7ee97c66182a8d0d41ec90e` | MIT (Copyright (c) 2026) |`src/ahd/core/hashing.py`, `src/ahd/core/io.py`, `src/ahd/core/trace.py` |
| Evo-Bench (also vendored as git submodule `third_party/evo-bench`, pinned to the same sha, added 2026-09-04 for M1) | https://github.com/RUCAIBox/Evo-Bench | main | `e1dc9386a193cab1ee8630824c085e5e26d0c730` | Apache-2.0 (see also its `NOTICE` and `third_party/`) |`src/ahd/core/hashing.py` (dir hash), `src/ahd/core/io.py` (jsonl), `src/ahd/llm/retry.py` (status set); `src/ahd/diagnosis/instrument/` (adapted copy of `policy_harness_seed/`, M3 replay instrument, changes listed in its README and in `agent/loop.py`) |
| AgentRx | https://github.com/microsoft/AgentRx | main | `f228165bfec60a801fd5fedd9d8ffe0f9de0c69d` | MIT (Copyright (c) Microsoft Corporation) |none |
| Rethinking the Evaluation of Harness Evolution (arXiv 2607.12227) | https://github.com/rethinking-harness-evolution/code | main | `62df2b9624ff32ca61b8accce7fb4a0fd8cbc8a8` | **No LICENSE file in repo; reuse permitted by the authors (name: TODO-owner, channel: TODO-owner, date: TODO-owner); MIT requested.** `pyproject.toml` declares `license = {text = "MIT"}`. Status: `permitted-pending-license` until a LICENSE file or an email confirming MIT is filed under `docs/reuse/permissions/`. Files identical to agentic-harness-engineering are cited to that repo instead. | none in M0 |
| VeRO (arXiv 2602.22480) | https://github.com/scaleapi/vero | main | `0b0e86764d836c456aee5b8dff80d765fdbba77c` | MIT (Copyright (c) 2026 Scale AI) |`src/ahd/core/config.py` (StrictModel), `src/ahd/llm/cache.py`, `src/ahd/llm/retry.py` |
| AgentDebug (arXiv 2509.25370) | https://github.com/ulab-uiuc/AgentDebug | main | `7740fe3a5c4822b2143cbde78ecfffeace0bb166` | MIT (Copyright (c) 2026 U Lab @UIUC) |none |

| Claw-Eval (via Evo-Bench `scripts/setup_claw_eval.sh`; not vendored, not redistributed) | https://github.com/claw-eval/claw-eval | pinned archive | `d3f02d4938ab0832377d90535013def2b1a2fdc0` (+ Evo-Bench `third_party/claw-eval/retry.patch`) | **No LICENSE file at the pinned commit; its README labels the project MIT** (per Evo-Bench's note). Executed locally from the gitignored `external/claw-eval` checkout; no code copied. | none |

## Obligations

- **MIT**: retain the copyright notice and permission notice in any reused file (the
  per-file header comment required by `docs/CONVENTIONS.md` satisfies this) and keep
  this notices file in distributions.
- **Apache-2.0** (Evo-Bench): retain copyright/attribution notices, reproduce the
  contents of its `NOTICE` file for any reused portion, and mark modified files as
  changed. Evo-Bench vendors code under `third_party/` with its own licenses; reuse
  from there must be attributed to the upstream project, not to Evo-Bench.
- **permitted-pending-license** (rethinking-harness-evolution): no license file, only a
  `license = MIT` string in `pyproject.toml`. The authors have agreed verbally to reuse;
  the owner records who, through which channel and when in the table above, and has asked
  for a LICENSE file or an email confirming MIT, to be stored under `docs/reuse/permissions/`.
  Until then any adapted file from this repo carries the header
  `License: no LICENSE file in repo; reuse permitted by the authors, MIT requested` and is
  listed here with status `permitted-pending-license`. Files it shares with
  agentic-harness-engineering are taken from that repo (MIT) and attributed there.
- **Vendored third parties inside the reference repos**: agentic-harness-engineering
  and rethinking-harness-evolution carry Nex-AGI (Apache-2.0) and Google LLC
  (Apache-2.0) headers under `agents/evolve_agent/`; Evo-Bench vendors Archipelago
  grading (Apache-2.0) under `third_party/`. Any reuse from those paths must credit
  the upstream holder named in the file header, not the reference repo.
