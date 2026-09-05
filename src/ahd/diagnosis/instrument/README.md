# Instrumented harness (measurement instrument, never an arm)

Adapted from: RUCAIBox/Evo-Bench @ e1dc9386a193cab1ee8630824c085e5e26d0c730
Original path: policy_harness_seed/ (verbatim copy)
License: Apache-2.0, Copyright 2026 Evo-Bench Authors -- see THIRD_PARTY_NOTICES.md
Changes: `agent/loop.py` accepts `task["_ahd_replay"]` (prefix replay for M3 replay
validation, see the header of that file); `harness.py` passes `command_timeout_seconds`
through unchanged. Everything else is byte-identical to the seed.

This tree is a measurement instrument used only by `ahd.diagnosis.replay`. It is hashed
and validated like a snapshot and both hashes are recorded in every `ReplayResult`; it is
never a snapshot in any experimental arm and never a proposer target.
