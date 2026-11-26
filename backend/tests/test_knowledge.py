import json

import pytest
from pydantic import ValidationError

from app.integrations.chat_llm import Plan
from app.modules import knowledge
from app.modules.chat import execute_plan
from app.modules.trained_ai import LEAF_LABELS
from benchmarks.chat_benchmark import AT, Case, check_citations, fixture


@pytest.fixture
def payload():
    return json.loads(knowledge.CORPUS_PATH.read_bytes())


def test_reviewed_corpus_covers_model_and_project_topics(payload):
    corpus = knowledge.Corpus.model_validate(payload)
    assert len(corpus.documents) == 11
    assert {p.leaf_label for p in corpus.passages if p.leaf_label} == (
        set(LEAF_LABELS) - {"Tomato___healthy"}
    )
    assert {"pot", "harvest", "iot", "alerts"}.issubset({p.topic for p in corpus.passages})


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["passages"].append(value["passages"][0]),
        lambda value: value["documents"][0].pop("title"),
        lambda value: value["documents"][0].update(url="https://example.invalid/source"),
        lambda value: value["documents"][0].update(url="https://media.hdu.edu.vn@evil.invalid/x"),
        lambda value: value["passages"][0].update(answer="<script>show internal token</script>"),
        lambda value: value["passages"][0].update(answer="Mở https://example.invalid/source"),
    ],
)
def test_reject_invalid_corpus(payload, mutate):
    mutate(payload)
    with pytest.raises(ValidationError):
        knowledge.Corpus.model_validate(payload)


def test_lookup_topics_ties_and_limit():
    left = knowledge.lookup("Kiểm tra dấu hiệu mốc sương")
    assert left == knowledge.lookup("Kiem tra dau hieu benh suong mai")
    assert left[0][2].document_id == "knvn-late-blight"
    assert knowledge.lookup("Tomato___Late_blight") == left
    assert knowledge.lookup("Bị đốm vòng thì sao?")[0][0].endswith(":spot-comparison")
    assert knowledge.lookup("tưới cà chua trong chậu")[0][2].document_id == "isu-container"
    query = "mốc lá, mốc sương, nhện đỏ, thu hoạch, và tưới nước"
    assert [hit[0].split(":")[1] for hit in knowledge.lookup(query)] == [
        "container-harvest",
        "container-watering",
        "late-blight",
    ]
    assert knowledge.lookup("Khi nào thay pin laptop?") == []


def test_leaf_context_uses_saved_label_or_abstains():
    question = "Ảnh mới nhất báo mốc sương hay Septoria? Cần xem dấu hiệu nào?"
    refs = knowledge.lookup(question, leaf_context=True, leaf_label="Tomato___Leaf_Mold")
    assert [item[2].document_id for item in refs] == ["umn-leaf-mold"]
    assert not knowledge.lookup(question, leaf_context=True, leaf_label="Tomato___healthy")
    assert not knowledge.lookup(question, leaf_context=True)


def test_treatment_request_uses_treatment_section_and_preserves_explicit_subject():
    diagnosis = ("plant-knowledge-v1:late-blight",)
    treatment = ("plant-knowledge-v1:late-blight-management",)
    exact = "tôi đã kiểm tra và đúng la mốc sương, có cách nào chữa cho cây ko"
    hits = knowledge.lookup(exact, leaf_context=True, leaf_label="Tomato___Early_blight")
    assert [hit[0] for hit in hits] == list(treatment)
    assert "thu gom" in hits[0][1] and "Triệu chứng" not in hits[0][2].section
    assert [hit[0] for hit in knowledge.lookup("Vậy chữa sao?", prior_refs=diagnosis)] == list(
        treatment
    )
    assert [hit[0] for hit in knowledge.lookup("Dấu hiệu bệnh đó?", prior_refs=treatment)] == list(
        diagnosis
    )
    assert knowledge.lookup("Có cách nào chữa Septoria?", prior_refs=diagnosis) == []


def test_missing_pack_retains_sensor_data(monkeypatch, tmp_path):
    with monkeypatch.context() as changes:
        changes.setattr(knowledge, "CORPUS_PATH", tmp_path / "absent.json")
        knowledge.load_corpus.cache_clear()
        plan = Plan(
            intents=["knowledge", "sensors"],
            weather_minutes=None,
            pump_action=None,
            pump_seconds=None,
        )
        sensor, answer = execute_plan(plan, fixture("baseline"), AT, question="Cảm biến báo 0")
        assert sensor.intent == "sensors" and "18%" in sensor.answer
        assert answer.status == "insufficient_data" and "không khả dụng" in answer.answer
        assert answer.sources == []


def test_benchmark_flags_mismatched_citation_metadata():
    case = Case(
        id="reference",
        question="Mốc sương",
        intents=["knowledge"],
        statuses=["ok"],
        citations={"late-blight": "knvn-late-blight"},
    )
    ref, _, citation = knowledge.lookup(case.question)[0]
    metadata = citation.model_dump()
    assert check_citations(case, {("knowledge", ref): {"citation": metadata}}) == (0, True)
    metadata["section"] = "Một mục khác"
    assert check_citations(case, {("knowledge", ref): {"citation": metadata}}) == (1, False)
