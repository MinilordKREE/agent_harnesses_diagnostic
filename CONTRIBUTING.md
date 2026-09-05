# Contributing

Read `docs/CONVENTIONS.md` first. It is short and every rule in it is checked in review.

## Pull request checklist

- [ ] `make check` passes locally (ruff, ruff format, mypy --strict, offline tests).
- [ ] New or changed behaviour has a unit test that runs offline.
- [ ] No `print` in `src/` outside `ahd/cli.py`; no secret value in any log, trace, ledger,
      manifest or test fixture.
- [ ] Every file adapted from a reference repo has the provenance header
      (repo, sha, original path, license, changes) and is listed in `THIRD_PARTY_NOTICES.md`
      and the module's `docs/reuse/M<n>.md`. Fresh files say "No reference source".
- [ ] Nothing copied from `rethinking-harness-evolution` (no license file); the same file in
      `agentic-harness-engineering` is cited instead.
- [ ] Any change to a record shape (trace envelope, ledger row, manifest, config, cache key)
      bumps the corresponding schema version and updates `docs/CONVENTIONS.md`.
- [ ] Any change to `configs/pricing.yaml` bumps `pricing_version` and updates `as_of`.
- [ ] Infra failures raise `InfraError`; task outcomes raise `TaskFailure`; neither is caught
      and turned into a default value.
- [ ] `uv.lock` is updated (`make lock`) if `pyproject.toml` dependencies changed.
- [ ] No task-loading, harness-execution or experiment logic in M0-scoped modules.

## Commit hygiene

- Commit messages describe the behaviour change, not the file list.
- Never commit `.env`, `runs/`, or `.cache/`. Pre-commit refuses `.env` explicitly.
