"""E0b: multimodal judge path, secondary verdicts, frozen splits, spec amendments, D1'/D7."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from ahd.core.config import JudgeConfig, load_run_config
from ahd.errors import ConfigError, InfraError
from ahd.experiments.e0 import _check_spec_matches_config, load_spec
from ahd.experiments.report import SourceCalibration, decisions
from ahd.experiments.splits import build_splits, freeze, load_splits
from ahd.llm.cache import redact_images
from ahd.llm.fake import FakeProvider
from ahd.llm.ledger import Ledger, read_ledger
from ahd.llm.types import ChatMessage
from ahd.tasks import scorer as scorer_module
from ahd.tasks.evobench import EvoBenchLoader
from ahd.tasks.judge import AhdJudgeClient, UnsupportedJudgeRequestError
from ahd.tasks.models import Artifacts, TaskSet
from ahd.tasks.scorer import Scorer
from tests.conftest import REPO_ROOT
from tests.evobench_fixtures import FAKE_REVISION

SPEC = REPO_ROOT / "experiments" / "E0" / "spec.yaml"
IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}}


@pytest.fixture
def validation(fake_snapshot: Path) -> TaskSet:
    return EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot).load("validation")


@pytest.fixture
def evaluation(fake_snapshot: Path) -> TaskSet:
    return EvoBenchLoader(revision=FAKE_REVISION, snapshot_dir=fake_snapshot).load("evaluation")


def test_multimodal_message_and_judge_flag() -> None:
    parts = ({"type": "text", "text": "grade"}, IMAGE)
    message = ChatMessage(role="user", content=parts)
    assert message.text() == "grade" and message.has_images()
    text_only = AhdJudgeClient(FakeProvider("ok"), config=JudgeConfig(), api_base="https://x")
    with pytest.raises(UnsupportedJudgeRequestError, match="text content only"):
        text_only.create(messages=[{"role": "user", "content": list(parts)}])
    vision = AhdJudgeClient(
        FakeProvider("ok"),
        config=JudgeConfig(model="deepseek-v4-flash-vision-exp", multimodal=True),
        api_base="https://x",
        arm="judge_vision",
    )
    vision.create(messages=[{"role": "user", "content": list(parts)}])
    request = vision.requests[-1]
    assert request.attribution.arm == "judge_vision" and request.messages[0].has_images()
    key_payload = request.cache_payload()
    redacted = redact_images(key_payload)
    dumped = json.dumps(redacted)
    assert "base64,AAAA" not in dumped and "sha256:" in dumped
    assert json.dumps(key_payload) != dumped  # the key keeps the full image, the stored copy not
    with pytest.raises(UnsupportedJudgeRequestError, match="unsupported content part"):
        vision.create(messages=[{"role": "user", "content": [{"type": "audio", "data": "x"}]}])


def test_secondary_verdict_is_attached_and_never_fails_the_rollout(
    validation: TaskSet, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_score_task(
        task: dict[str, Any], workspace: Path, final_answer: str, judge_client: Any = None
    ) -> dict[str, Any]:
        calls.append(judge_client.config.model)
        if judge_client.config.model.endswith("vision-exp"):
            return {
                "passed": False,
                "score": 0.4,
                "reason": "rubric_file_judge: 4/10 pts",
                "judge_detail": {"image_grading": {"used": True}},
            }
        return {"passed": True, "score": 0.9, "reason": "rubric_file_judge(text-only): 9/10 pts"}

    monkeypatch.setattr(scorer_module, "score_task", fake_score_task)
    ledger = Ledger(tmp_path / "ledger.jsonl", "run-s")
    provider = FakeProvider("ok")
    primary = Scorer(
        judge=AhdJudgeClient(provider, config=JudgeConfig(), api_base="https://x"),
        ledger=ledger,
        arm="seed",
        seed=0,
    )
    secondary = Scorer(
        judge=AhdJudgeClient(
            provider,
            config=JudgeConfig(model="deepseek-v4-flash-vision-exp", multimodal=True),
            api_base="https://x",
            arm="judge_vision",
        ),
        ledger=ledger,
        arm="seed",
        seed=0,
    )
    task = validation.by_id("gdpval-00000000-0000-0000-0000-000000000001")
    ws = tmp_path / "ws"
    (ws / "outputs").mkdir(parents=True)
    (ws / "outputs" / "plan.docx").write_bytes(b"x")
    artifacts = Artifacts(workspace=ws, final_answer="see outputs")
    score = primary.score(task, artifacts)
    verdict = secondary.verdict(task, artifacts)
    combined = score.model_copy(update={"secondary_judge": verdict})
    assert combined.passed and combined.secondary_judge is not None
    assert combined.secondary_judge.passed is False and combined.secondary_judge.value == 0.4
    assert combined.secondary_judge.model == "deepseek-v4-flash-vision-exp"
    assert calls == ["deepseek-v4-pro", "deepseek-v4-flash-vision-exp"]
    # an infra failure of the secondary is recorded, not raised
    monkeypatch.setattr(
        scorer_module,
        "score_task",
        lambda *a, **k: {"passed": False, "score": 0.0, "reason": "rubric_judge_error: boom"},
    )
    failed = secondary.verdict(task, artifacts)
    assert failed.error == "judge_error" and failed.passed is None
    with pytest.raises(InfraError):
        primary.score(task, artifacts)
    rows = read_ledger(ledger.path)
    assert {r.arm for r in rows if r.event == "infra_failure"} == {"seed"}


def test_splits_are_disjoint_deterministic_and_frozen(
    validation: TaskSet, evaluation: TaskSet, tmp_path: Path
) -> None:
    def widen(taskset: TaskSet, copies: int) -> TaskSet:
        tasks = [
            t.model_copy(update={"id": f"{t.id}-{i}"}) for t in taskset.tasks for i in range(copies)
        ]
        return taskset.model_copy(update={"tasks": tuple(tasks)})

    wide_eval = widen(evaluation, 6)  # 6 evaluation tasks per source in the fixture
    sources = ("browsecomp", "gdpval")
    splits = build_splits(
        validation, wide_eval, sources=sources, eval_dev_per_source=2, heldout_per_source=3, seed=0
    )
    for source in sources:
        s = splits.sources[source]
        assert len(s.eval_dev) == 2 and len(s.heldout) == 3
        assert not (set(s.eval_dev) & set(s.heldout)) and not (set(s.validation) & set(s.eval_dev))
        assert set(splits.mining_pool(source)) == set(s.validation) | set(s.eval_dev)
    again = build_splits(
        validation, wide_eval, sources=sources, eval_dev_per_source=2, heldout_per_source=3, seed=0
    )
    assert again == splits
    path = tmp_path / "splits.json"
    _, sha1 = freeze(splits, path)
    _, sha2 = freeze(again, path)
    assert sha1 == sha2 and load_splits(path) == splits
    other = build_splits(
        validation, wide_eval, sources=sources, eval_dev_per_source=2, heldout_per_source=3, seed=1
    )
    with pytest.raises(ConfigError, match="differs"):
        freeze(other, path)
    with pytest.raises(ConfigError, match="cannot sample"):
        build_splits(
            validation,
            wide_eval,
            sources=sources,
            eval_dev_per_source=5,
            heldout_per_source=3,
            seed=0,
        )


def test_amended_spec_loads_and_matches_config() -> None:
    spec = load_spec(SPEC)
    assert spec.hard_cap_usd == 100.0 and spec.owner_budget_usd == 600.0
    assert spec.judges.secondary == {"gdpval": "deepseek-v4-flash-vision-exp"}
    assert spec.workers_for("browsecomp") == 4 and spec.workers_for("gdpval") == 8
    assert spec.scope("hle")["diagnosis"] is False and spec.scope("gdpval")["B1"] == "mining_pool"
    assert {"D1prime", "D7"} <= set(spec.decision_rules)
    _check_spec_matches_config(spec, load_run_config(REPO_ROOT / "configs" / "runs" / "e0.yaml"))


def test_decisions_d1prime_and_d7() -> None:
    spec = load_spec(SPEC)
    calib = {
        "gdpval": SourceCalibration(
            source="gdpval",
            pass_rate=0.5,
            aa_delta_points=2.0,
            aa_ci=(0.0, 5.0),
            heldout_delta_points=1.0,
            clusters_with_two=7,
            primary_clusters=5,
        ),
        "claw_eval": SourceCalibration(
            source="claw_eval",
            pass_rate=0.6,
            aa_delta_points=3.0,
            aa_ci=(0.0, 6.0),
            heldout_delta_points=2.0,
            clusters_with_two=6,
            primary_clusters=4,
        ),
        "hle": SourceCalibration(
            source="hle",
            pass_rate=0.6,
            aa_delta_points=1.0,
            aa_ci=(0.0, 3.0),
            heldout_delta_points=None,
            clusters_with_two=0,
            primary_clusters=0,
        ),
        "browsecomp": SourceCalibration(
            source="browsecomp",
            pass_rate=0.6,
            aa_delta_points=10.0,
            aa_ci=(0.0, 20.0),
            heldout_delta_points=None,
            clusters_with_two=0,
            primary_clusters=0,
        ),
    }
    extras = {
        "judge_self_consistency": 0.95,
        "judge_self_consistency_vision": 0.92,
        "vision_disagreement": 0.15,
        "vision_compared": 100,
    }
    by_rule = {str(r[0]): str(r[2]) for r in decisions(spec, calib, extras, cost_per_rollout=0.05)}
    assert by_rule["D1prime"] == "E2 starts"  # 5 + 4 = 9 primary clusters in included sources
    assert by_rule["D7"].startswith("gdpval E2 primary judge = vision")
    assert by_rule["D6"] == "single judge"
    assert by_rule["D4"] == "k=3"  # 8 x 9 x 3 x 0.05 = 10.8 <= 600
    fewer = dict(calib)
    fewer["claw_eval"] = calib["claw_eval"].model_copy(update={"primary_clusters": 2})
    by_rule2 = {
        str(r[0]): str(r[2])
        for r in decisions(
            spec,
            fewer,
            {"judge_self_consistency_vision": 0.8, "vision_disagreement": 0.2},
            cost_per_rollout=0.05,
        )
    }
    assert by_rule2["D1prime"].startswith("pivot required")
    assert by_rule2["D7"].startswith("gdpval E2 primary judge = text")
