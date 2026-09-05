# Reuse permissions

This directory stores written permission for code whose repository ships no license file.

## rethinking-harness-evolution (arXiv 2607.12227)

Repo: https://github.com/rethinking-harness-evolution/code, sha
`62df2b9624ff32ca61b8accce7fb4a0fd8cbc8a8`. The repository has no LICENSE file; its
`pyproject.toml` declares `license = {text = "MIT"}`.

Status: **permitted-pending-license**. The authors have agreed verbally that the code may be
reused (details of that conversation are recorded by the owner in `THIRD_PARTY_NOTICES.md`).
Because verbal consent does not bind downstream users of a public repository, the owner has
asked the authors to either add a LICENSE file upstream or confirm MIT by email.

When that email arrives, save it here as `rethinking-harness-evolution-<YYYY-MM-DD>.eml`
(or `.txt`), update the row in `THIRD_PARTY_NOTICES.md` to cite it, and change the status
above to `permitted`.

Until then:

- Files identical to those in agentic-harness-engineering (MIT) are cited to that repo and
  are not affected.
- Files unique to rethinking-harness-evolution (parallel sampling, sequential refinement,
  budget accounting; needed from M5) may be adapted, must carry the header
  `License: no LICENSE file in repo; reuse permitted by the authors, MIT requested`, and are
  listed in `THIRD_PARTY_NOTICES.md` with the status `permitted-pending-license`.

No M0 file is derived from this repository.
