"""COH-WRONG generation and parity (M3.2) and the privileged-information probe."""

from __future__ import annotations

import json

import pytest

from ahd.diagnosis import coherent
from ahd.diagnosis.cluster import cluster
from ahd.diagnosis.llm import DiagnosisLLM
from ahd.diagnosis.probe import one_line, probe_one
from ahd.diagnosis.schema import load_causes
from ahd.harness.components import ComponentManifest
from ahd.llm.fake import FakeProvider
from ahd.llm.types import ChatRequest
from tests.conftest import REPO_ROOT
from tests.diag_fixtures import diagnosis, finish, sh, trajectory

VOCAB = load_causes(REPO_ROOT / "configs" / "prompts" / "diagnosis" / "causes.yaml")


@pytest.fixture(scope="module")
def manifest() -> ComponentManifest:
    return ComponentManifest.load(REPO_ROOT / "configs" / "harness" / "seed_components.yaml")


def test_sign_test_and_parity_rule() -> None:
    assert coherent.sign_test_p(0, 0) is None
    assert coherent.sign_test_p(5, 0) == pytest.approx(2 / 32)
    assert coherent.sign_test_p(3, 2) == pytest.approx(1.0)

    def score(cid: str, ref: int, coh: int) -> coherent.PlausibilityScore:
        return coherent.PlausibilityScore(
            cluster_id=cid,
            generation_seed=0,
            reference_score=ref,
            coherent_score=coh,
            reference_position="A",
        )

    even = coherent.parity([score("a", 4, 4), score("b", 3, 3), score("c", 5, 4), score("d", 3, 4)])
    assert even.ok and even.mean_difference == 0 and even.ties == 2 and even.sign_test_p == 1.0
    skewed = coherent.parity([score(str(i), 5, 3) for i in range(6)])
    assert not skewed.ok and skewed.mean_difference == 2.0
    assert skewed.sign_test_p is not None and skewed.sign_test_p < 0.10
    assert len(skewed.reasons) == 2
    empty = coherent.parity([score("x", 4, 2).model_copy(update={"error": "bad json"})])
    assert not empty.ok and empty.n == 0 and empty.reasons == ("no scored pairs",)


def test_generate_and_judge_with_a_fake_model(manifest: ComponentManifest) -> None:
    cs = cluster([diagnosis(task_id="t1", component="system_prompt", step=3)])
    c = cs.clusters[0]
    failed = trajectory([[sh("ls")], [sh("cat a")], [finish("wrong")]])
    members: list[coherent.Member] = [("t1/r1/1", "find the town", failed)]
    prompts = coherent.load_prompts()

    def reply(request: ChatRequest) -> str:
        text = request.messages[-1].text()
        if "Diagnosis A:" in text:
            return json.dumps({"A": 4, "B": 3})
        assert "`tool_router`" in text and "step 2" in text and "variant 1" in text
        assert "Reference" not in text
        return json.dumps(
            {
                "severity": "high",
                "cause_label": "argument_error",
                "mechanism": "The tool router dropped the argument.",
                "fix_hint": "Validate arguments in the router.",
            }
        )

    provider = FakeProvider(reply)
    llm = DiagnosisLLM(provider)
    record = coherent.generate(
        c,
        members=members,
        component="tool_router",
        step=2,
        step_basis="validated_negative",
        manifest=manifest,
        llm=llm,
        prompt_template=prompts["forced_where"],
        vocabulary=VOCAB,
        seed=0,
        generation_seed=1,
    )
    assert record.component == "tool_router" and record.step == 2
    assert record.why.cause_label == "argument_error" and record.severity == "high"
    assert record.model == "deepseek-v4-pro" and record.request_sha256
    # another variant seed is a different request (the listing order and the variant line)
    coherent.generate(
        c,
        members=members,
        component="tool_router",
        step=2,
        step_basis="validated_negative",
        manifest=manifest,
        llm=llm,
        prompt_template=prompts["forced_where"].replace("variant {variant}", "variant 1"),
        vocabulary=VOCAB,
        seed=0,
        generation_seed=2,
    )
    first, second = (
        provider.requests[0].messages[-1].text(),
        provider.requests[1].messages[-1].text(),
    )
    assert first != second
    score = coherent.judge_plausibility(
        c,
        members=members,
        rendered_reference="DIAGNOSIS ref",
        rendered_coherent="DIAGNOSIS coh",
        llm=llm,
        prompt_template=prompts["plausibility"],
        seed=0,
        generation_seed=1,
    )
    assert score.error is None
    if score.reference_position == "A":
        assert (score.reference_score, score.coherent_score) == (4, 3)
    else:
        assert (score.reference_score, score.coherent_score) == (3, 4)
    judge_text = provider.requests[-1].messages[-1].text()
    assert "DIAGNOSIS ref" in judge_text and "DIAGNOSIS coh" in judge_text
    bad = coherent.judge_plausibility(
        c,
        members=members,
        rendered_reference="r",
        rendered_coherent="c",
        llm=DiagnosisLLM(FakeProvider('{"A": 9, "B": 1}')),
        prompt_template=prompts["plausibility"],
        seed=0,
        generation_seed=0,
    )
    assert bad.error is not None and bad.reference_score is None


def test_probe_scoring_and_malformed_output() -> None:
    reply = json.dumps(
        {
            "task_top3": ["t2", "t1", "t3"],
            "required_tools": ["todo_list_tasks", "gmail_send"],
            "answer_category": "Productivity",
        }
    )
    llm = DiagnosisLLM(FakeProvider(reply))
    record = probe_one(
        "DIAGNOSIS\nWHERE: component `tool_router`, step 2",
        cluster_id="c1",
        arm="reference",
        assigned_task="t1",
        origin_task="t2",
        pool=[("t1", "tidy the todo list"), ("t2", "email a contact"), ("t3", "book a room")],
        tools=["todo_list_tasks", "todo_update_task", "gmail_send"],
        categories=["productivity", "communication"],
        required_tools=["todo_list_tasks", "todo_update_task"],
        true_category="productivity",
        llm=llm,
        prompt_template=__import__("ahd.diagnosis.probe", fromlist=["load_prompt"]).load_prompt(),
        seed=0,
    )
    assert not record.hit_top1 and record.hit_top3
    assert record.origin_hit_top1 and record.origin_hit_top3
    assert record.tool_precision == 0.5 and record.tool_recall == 0.5
    assert record.category_hit is True and record.error is None
    broken = probe_one(
        "x",
        cluster_id="c1",
        arm="system",
        assigned_task="t1",
        origin_task="t1",
        pool=[("t1", "a")],
        tools=[],
        categories=[],
        required_tools=[],
        true_category=None,
        llm=DiagnosisLLM(FakeProvider("not json")),
        prompt_template="{diagnosis}{tasks}{tools}{categories}",
        seed=0,
    )
    assert broken.error is not None and broken.task_top3 == () and broken.category_hit is None
    assert one_line("  a   very\nlong  prompt " * 20, limit=20).endswith("…")
