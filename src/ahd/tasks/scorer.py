"""Scorer adapter over Evo-Bench's ``score_task`` with ahd error semantics.

No reference source: written fresh for ahd (see docs/reuse/M1.md). ``score_task`` itself is
imported from the submodule (Apache-2.0), never copied.

Evo-Bench's scorer never raises for a missing resource; it returns ``passed=False, score=0``
with a reason string. :data:`REASON_RULES` enumerates every reason string it can emit
(``evobench/evaluation/scorer.py`` at e1dc938; ``web_hackle_violation`` is applied by the
runner, ``hackle_guard.py``) and assigns each to a family:

* ``infra``  -> :class:`ahd.errors.InfraError` is raised and an ``infra_failure`` row written;
* ``task``   -> a ``Score`` with ``task_failure`` set and a ``task_failure`` row written;
* ``judged`` -> a ``Score`` carrying the verdict.

An unknown reason string is itself an ``InfraError``; nothing is silently scored 0.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import re
import threading
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evobench.evaluation.scorer import score_task

from ahd.core.hashing import DEFAULT_IGNORED_PARTS, JsonValue, sha256_dir, to_json_value
from ahd.errors import ConfigError, InfraError, TaskFailure
from ahd.llm.ledger import Ledger
from ahd.tasks.claw import (
    CLAW_JUDGE_MAX_RETRIES,
    PartialScore,
    load_checks,
    partial_score,
    read_dispatches,
)
from ahd.tasks.judge import AhdJudgeClient, patched_claw_judge
from ahd.tasks.models import Artifacts, Score, SecondaryVerdict, Task

logger = logging.getLogger(__name__)

type ReasonFamily = Literal["judged", "infra", "task"]


@dataclass(frozen=True, slots=True)
class ReasonRule:
    pattern: str
    family: ReasonFamily
    kind: str
    example: str
    note: str


REASON_RULES: tuple[ReasonRule, ...] = (
    # --- infra: judge failed, resource missing, grader crashed ---
    ReasonRule(
        r"^judge_error: ",
        "infra",
        "judge_error",
        "judge_error: unparseable judge output: 'x'",
        "BrowseComp judge failed after 3 attempts (scorer.py:274)",
    ),
    ReasonRule(
        r"^hle_judge_error: ",
        "infra",
        "judge_error",
        "hle_judge_error: RateLimitError: 429",
        "HLE judge failed after 3 attempts (scorer.py:395)",
    ),
    ReasonRule(
        r"^rubric_judge_error: ",
        "infra",
        "judge_error",
        "rubric_judge_error: no attempt made",
        "GDPval judge failed after 3 attempts incl. text-only fallback (scorer.py:659)",
    ),
    ReasonRule(
        r"^rubric_file_judge: rubric has no positive points$",
        "infra",
        "bad_rubric",
        "rubric_file_judge: rubric has no positive points",
        "released rubric defect, not a verdict (scorer.py:591)",
    ),
    ReasonRule(
        r"^claw_grader: no trajectory path on task$",
        "infra",
        "missing_trajectory",
        "claw_grader: no trajectory path on task",
        "caller did not attach _trajectory_path (scorer.py:1101)",
    ),
    ReasonRule(
        r"^claw_grader: assembled trace missing at ",
        "infra",
        "missing_trace",
        "claw_grader: assembled trace missing at /x/claw_trace.json",
        "rollout dir lacks the assembled Claw trace (scorer.py:1104)",
    ),
    ReasonRule(
        r"^claw_grader_error: ",
        "infra",
        "grader_error",
        "claw_grader_error: ImportError: No module named claw_eval",
        "exception inside Claw grading: checkout, import, judge (scorer.py:1139)",
    ),
    ReasonRule(
        r"^apex_grader_error: ",
        "infra",
        "apex_unavailable",
        "apex_grader_error: no trajectory path on task",
        "APEX grading needs the E2B sandbox (scorer.py:1162-1187)",
    ),
    ReasonRule(
        r"^pairwise_judge_error: ",
        "infra",
        "judge_error",
        "pairwise_judge_error: x",
        "unused scorer type (scorer.py:1056)",
    ),
    # --- task: the agent's conduct, applied by the runner ---
    ReasonRule(
        r"^web_hackle_violation: ",
        "task",
        "hackle_violation",
        "web_hackle_violation: huggingface_dataset_or_space",
        "benchmark-leak URL in tool output (hackle_guard.py:160)",
    ),
    # --- judged verdicts ---
    ReasonRule(
        r"^llm_as_judge(: |$)",
        "judged",
        "llm_as_judge",
        "llm_as_judge: matches the gold answer",
        "BrowseComp verdict (scorer.py:267)",
    ),
    ReasonRule(
        r"^hle_judge: (correct|incorrect)",
        "judged",
        "hle_judge",
        "hle_judge: incorrect — wrong value",
        "HLE verdict (scorer.py:379)",
    ),
    ReasonRule(
        r"^rubric_file_judge(\(text-only\))?: -?[0-9.]+/[0-9]+ pts",
        "judged",
        "rubric_file_judge",
        "rubric_file_judge(text-only): 3.0/10 pts, 1 file(s), thr=0.6",
        "GDPval rubric verdict (scorer.py:643)",
    ),
    ReasonRule(
        r"^claw_grader: C=",
        "judged",
        "claw_grader",
        "claw_grader: C=0.50 R=1.00 ...",
        "Claw-Eval verdict (scorer.py:1130)",
    ),
    ReasonRule(
        r"^apex_grader: \d+/\d+ criteria passed",
        "judged",
        "apex_grader",
        "apex_grader: 3/6 criteria passed",
        "APEX verdict (scorer.py:1195); APEX tasks are excluded",
    ),
    ReasonRule(
        r"^pairwise_vs_seed: ",
        "judged",
        "pairwise_vs_seed",
        "pairwise_vs_seed: both sides empty (parity)",
        "unused scorer type (scorer.py:1022-1071)",
    ),
    ReasonRule(
        r"^final_answer_exact$",
        "judged",
        "final_answer_exact",
        "final_answer_exact",
        "unused scorer type (scorer.py:99)",
    ),
    ReasonRule(
        r"^file_contains$",
        "judged",
        "file_contains",
        "file_contains",
        "unused scorer type (scorer.py:107)",
    ),
    ReasonRule(
        r"^file_equals$",
        "judged",
        "file_equals",
        "file_equals",
        "unused scorer type (scorer.py:115)",
    ),
)


def classify_reason(reason: str) -> ReasonRule:
    for rule in REASON_RULES:
        if re.match(rule.pattern, reason):
            return rule
    raise InfraError(
        f"Evo-Bench scorer returned a reason string ahd does not know: {reason[:200]!r}",
        kind="unknown_reason",
    )


def artifact_sha256(artifacts: Artifacts) -> str:
    """Hash of everything the judge will look at: final answer, ``outputs/``, trajectory."""
    digest = hashlib.sha256()
    digest.update(b"final_answer\0")
    digest.update(artifacts.final_answer.encode("utf-8"))
    digest.update(b"\0outputs\0")
    outputs = artifacts.workspace / "outputs"
    if outputs.is_dir():
        digest.update(sha256_dir(outputs, ignored_parts=DEFAULT_IGNORED_PARTS).encode("ascii"))
    digest.update(b"\0trajectory\0")
    if artifacts.trajectory_path is not None and artifacts.trajectory_path.is_file():
        digest.update(artifacts.trajectory_path.read_bytes())
    return digest.hexdigest()


CLAW_REPO_ENV = "EVOBENCH_CLAW_REPO"
_env_lock = threading.Lock()
_env_depth = 0
_env_previous: str | None = None


@contextlib.contextmanager
def claw_repo_env(claw_repo: Path | None) -> Iterator[None]:
    """Point Evo-Bench's host-side Claw grader at the checkout for the duration of a call.

    ``evobench.evaluation.scorer._claw_grader`` resolves ``task_dir`` through
    ``claw_runtime.claw_repo_root()``, which reads ``EVOBENCH_CLAW_REPO`` from the *process*
    environment (default ``/opt/claw-eval``). The variable is set on first entry and restored
    when the last concurrent scoring leaves (refcounted; M2.1 runs scorings in threads).
    """
    global _env_depth, _env_previous
    if claw_repo is None:
        yield
        return
    with _env_lock:
        if _env_depth == 0:
            _env_previous = os.environ.get(CLAW_REPO_ENV)
            os.environ[CLAW_REPO_ENV] = str(claw_repo)
        _env_depth += 1
    try:
        yield
    finally:
        with _env_lock:
            _env_depth -= 1
            if _env_depth == 0:
                if _env_previous is None:
                    os.environ.pop(CLAW_REPO_ENV, None)
                else:
                    os.environ[CLAW_REPO_ENV] = _env_previous


class Scorer:
    """``score(task, artifacts) -> Score``; judge calls are ledgered and artifact-cached."""

    def __init__(
        self,
        *,
        judge: AhdJudgeClient,
        ledger: Ledger,
        arm: str,
        seed: int,
        claw_repo: Path | None = None,
    ) -> None:
        self._judge = judge
        self._ledger = ledger
        self._arm = arm
        self._seed = seed
        self._claw_repo = claw_repo

    def _precheck(self, task: Task, artifacts: Artifacts) -> tuple[str, str] | None:
        """Structural failures decided without a judge call: (kind, reason)."""
        if task.source_benchmark in ("browsecomp", "hle") and not artifacts.final_answer.strip():
            return "empty_answer", "empty_answer: the rollout produced no final answer"
        if task.source_benchmark == "gdpval":
            outputs = artifacts.workspace / "outputs"
            if not outputs.is_dir() or not any(p.is_file() for p in outputs.rglob("*")):
                return "no_deliverable", "no_deliverable: nothing was written to outputs/"
        return None

    def _task_failure(
        self, task: Task, kind: str, reason: str, *, artifact: str, meta: dict[str, JsonValue]
    ) -> Score:
        exc = TaskFailure(reason, kind=kind)
        self._ledger.record_task_failure_event(
            arm=self._arm, unit_id=task.id, seed=self._seed, model=self._judge.config.model, exc=exc
        )
        logger.info("task failure", extra={"task_id": task.id, "kind": kind})
        return Score(
            passed=False,
            value=0.0,
            reason=reason,
            scorer=task.evaluator.type,
            judge_meta=meta,
            task_failure=kind,
            artifact_sha256=artifact,
        )

    def _judge_exhausted(
        self,
        task: Task,
        artifacts: Artifacts,
        judge: AhdJudgeClient,
        *,
        artifact: str,
        raw_reason: str,
    ) -> Score:
        last_reply = judge.responses[-1].strip().replace("\n", " ")[:160] if judge.responses else ""
        rollout_dir = (
            artifacts.trajectory_path.parent if artifacts.trajectory_path is not None else None
        )
        partial: PartialScore | None = None
        if rollout_dir is not None and self._claw_repo is not None:
            partial = partial_score(
                load_checks(task, claw_repo=self._claw_repo), read_dispatches(rollout_dir)
            )
        reason = (
            f"empty_answer: Claw judge exhausted {judge.max_retry_index} retries on an empty or "
            f"unparseable assistant reply (last judge reply: {last_reply!r}); {raw_reason}"
        )
        meta_raw: dict[str, Any] = {
            "scorer_kind": "claw_grader",
            "judge_exhausted": True,
            "partial": partial.model_dump() if partial is not None else None,
        }
        meta = to_json_value({k: v for k, v in meta_raw.items() if v is not None})
        assert isinstance(meta, dict)
        score = self._task_failure(task, "empty_answer", reason, artifact=artifact, meta=meta)
        return score.model_copy(update={"value": partial.value if partial is not None else 0.0})

    def verdict(self, task: Task, artifacts: Artifacts) -> SecondaryVerdict:
        """Score as a secondary judge: infra errors become part of the verdict."""
        model = self._judge.config.model
        try:
            score = self.score(task, artifacts)
        except InfraError as exc:
            return SecondaryVerdict(
                model=model, passed=None, value=None, reason=str(exc), error=exc.kind
            )
        return SecondaryVerdict(
            model=model,
            passed=score.passed,
            value=score.value,
            reason=score.reason,
            judge_meta=score.judge_meta,
        )

    def score(self, task: Task, artifacts: Artifacts) -> Score:
        if task.excluded:
            raise ConfigError(f"task {task.id} is excluded: {task.exclusion_reason}")
        if not artifacts.workspace.is_dir():
            raise InfraError(f"workspace missing: {artifacts.workspace}", kind="missing_file")
        artifact = artifact_sha256(artifacts)
        pre = self._precheck(task, artifacts)
        if pre is not None:
            return self._task_failure(task, pre[0], pre[1], artifact=artifact, meta={})

        evo_task = task.to_evobench_dict()
        if artifacts.trajectory_path is not None:
            evo_task["_trajectory_path"] = str(artifacts.trajectory_path)
        judge = self._judge.bind(unit_id=task.id, cache_scope=f"artifact:{artifact}")
        if task.source_benchmark == "claw_eval":
            with claw_repo_env(self._claw_repo), patched_claw_judge(judge):
                raw = _call_score_task(evo_task, artifacts, judge)
        else:
            raw = _call_score_task(evo_task, artifacts, judge)

        reason = str(raw.get("reason", ""))
        if (
            task.source_benchmark == "claw_eval"
            and reason.startswith("claw_grader_error: ")
            and judge.max_retry_index >= CLAW_JUDGE_MAX_RETRIES
        ):
            # claw-eval's judge re-sent the same prompt until its retries ran out: the judge
            # could not grade the assistant's reply (empty or unparseable). That is the agent's
            # failure, not the grader's (owner decision, M3.1). The value is the deterministic
            # part of the Claw score; judged components count zero.
            return self._judge_exhausted(
                task, artifacts, judge, artifact=artifact, raw_reason=reason
            )
        rule = classify_reason(reason)
        meta_raw = {
            "scorer_kind": rule.kind,
            "judge_detail": raw.get("judge_detail"),
            "judge_usage": raw.get("judge_usage"),
            "policy_violation": raw.get("policy_violation"),
        }
        meta = to_json_value({k: v for k, v in meta_raw.items() if v is not None})
        if not isinstance(meta, dict):  # pragma: no cover - dict in, dict out
            raise TypeError("judge_meta must be an object")
        if rule.family == "infra":
            exc = InfraError(reason, kind=rule.kind)
            self._ledger.record_infra_failure_event(
                arm=self._arm, unit_id=task.id, seed=self._seed, model=judge.config.model, exc=exc
            )
            logger.error("scoring infra failure", extra={"task_id": task.id, "kind": rule.kind})
            raise exc
        if rule.family == "task":
            return self._task_failure(task, rule.kind, reason, artifact=artifact, meta=meta)
        return Score(
            passed=bool(raw.get("passed", False)),
            value=float(raw.get("score", 0.0)),
            reason=reason,
            scorer=rule.kind,
            judge_meta=meta,
            task_failure=None,
            artifact_sha256=artifact,
        )


def _call_score_task(
    evo_task: dict[str, Any], artifacts: Artifacts, judge: AhdJudgeClient
) -> dict[str, Any]:
    result: dict[str, Any] = score_task(
        evo_task,
        Path(artifacts.workspace),
        artifacts.final_answer,
        judge_client=judge,
    )
    if not isinstance(result, dict) or "reason" not in result:
        raise InfraError("Evo-Bench scorer returned a malformed result", kind="scorer_protocol")
    return result
