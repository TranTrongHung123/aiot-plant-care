from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import chat as api
from app.core.schemas import DEVICE_ID
from app.core.settings import settings
from app.db.models import Base, ChatMessage, ChatToolCall, Command, Device, Job, PlantImage
from app.integrations import chat_llm
from app.integrations.chat_llm import HTTPProvider, Plan, ProviderError
from app.modules import knowledge
from benchmarks.chat_benchmark import Case, evaluate


@dataclass
class TestProvider:
    __test__ = False
    name: str
    plan: Plan
    model: str = "fixture-model"
    configured: bool = True
    error: ProviderError | None = None
    calls: int = 0
    messages: list | None = None

    async def generate(self, messages):
        self.calls += 1
        self.messages = messages
        if self.error:
            raise self.error
        return self.plan, 400, 28

    async def healthy(self):
        return self.configured


def plan(intent="sensors", *, weather_minutes=None, action=None, seconds=None):
    return Plan(
        intents=[intent],
        weather_minutes=weather_minutes,
        pump_action=action,
        pump_seconds=seconds,
    )


@pytest.mark.asyncio
async def test_router_supports_explicit_modes_and_auto_fallback(monkeypatch):
    monkeypatch.setattr(settings, "chat_auto_preference", "cloud")
    cloud = TestProvider("cloud", plan())
    local = TestProvider("local", plan())
    registry = {"local": local, "cloud": cloud}
    _, chosen = await chat_llm.route("local", "Cho xem độ ẩm", [], registry)
    assert chosen.actual_provider == "local" and not chosen.fallback_used
    assert cloud.calls == 0
    _, chosen = await chat_llm.route("cloud", "Cho xem độ ẩm", [], registry)
    assert chosen.actual_provider == "cloud" and not chosen.fallback_used
    cloud.error = ProviderError(503, "rate limit", fallback=True)
    _, chosen = await chat_llm.route("auto", "Cho xem độ ẩm", [], registry)
    assert chosen.requested_mode == "auto" and chosen.actual_provider == "local"
    assert chosen.fallback_used and chosen.input_tokens == 400
    with pytest.raises(ProviderError, match="rate limit"):
        await chat_llm.route("cloud", "Cho xem độ ẩm", [], registry)


@pytest.mark.asyncio
async def test_capability_mismatch_does_not_fallback(monkeypatch):
    monkeypatch.setattr(settings, "chat_auto_preference", "cloud")
    cloud = TestProvider("cloud", plan(), error=ProviderError(502, "schema"))
    local = TestProvider("local", plan())
    with pytest.raises(ProviderError, match="schema"):
        await chat_llm.route("auto", "Cho xem độ ẩm", [], {"local": local, "cloud": cloud})
    assert local.calls == 0


@pytest.mark.asyncio
async def test_http_provider_uses_structured_schema_and_no_tool_execution(monkeypatch):
    import json

    dummy = "test-only-cloud-key"
    monkeypatch.setattr(settings, "chat_cloud_api_key", SecretStr(dummy))
    calls = []
    messages = [{"role": "user", "content": "Cho xem độ ẩm"}]
    generate = plan().model_dump_json()

    def respond(request):
        calls.append((request.url.path, json.loads(request.content), request.headers))
        if request.url.path == "/v1/chat/completions":
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": generate}}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 12},
                },
            )
        return httpx.Response(
            200,
            json={
                "message": {"content": generate},
                "prompt_eval_count": 10,
                "eval_count": 12,
            },
        )

    client = httpx.AsyncClient
    monkeypatch.setattr(
        chat_llm.httpx,
        "AsyncClient",
        lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs),
    )
    for provider in ("cloud", "local"):
        actual, tokens_in, tokens_out = await HTTPProvider(provider, "test").generate(messages)
        assert actual == plan() and (tokens_in, tokens_out) == (10, 12)
    cloud, local = calls
    assert cloud[0] == "/v1/chat/completions" and local[0] == "/api/chat"
    assert cloud[1]["response_format"]["json_schema"]["strict"] is True
    assert cloud[1]["store"] is False and cloud[2]["Authorization"] == f"Bearer {dummy}"
    assert local[1]["format"]["additionalProperties"] is False
    assert "Authorization" not in local[2]
    assert all("tools" not in body for _, body, _ in calls)


@pytest.fixture
def chat_api(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(api, "Session", session)
    monkeypatch.setattr(api, "_recent_sends", type(api._recent_sends)())
    monkeypatch.setattr(settings, "chat_enabled", True)
    monkeypatch.setattr(settings, "app_token", "chat-test-app-token")
    monkeypatch.setattr(settings, "chat_rate_limit_per_minute", 2)
    remote = TestProvider("cloud", plan("pump", action="on", seconds=10))
    local = TestProvider("local", plan("other"))
    monkeypatch.setattr(chat_llm, "providers", lambda: {"cloud": remote, "local": local})
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client, session, remote
    engine.dispose()


def test_chat_api_auth_rate_limit_redaction_and_replay(chat_api):
    client, session, provider = chat_api
    base = "/api/v1/chat"
    headers = {"Authorization": f"Bearer {settings.app_token}"}
    assert client.get(base + "/health").status_code == 401
    assert client.get(base + "/providers", params={"token": settings.app_token}).status_code == 401
    assert client.get(base + "/health", headers=headers).json()["providers"]["cloud"]
    response = client.post(base + "/conversations", json={"default_mode": "cloud"}, headers=headers)
    assert response.status_code == 201
    path = f"{base}/conversations/{response.json()['id']}/messages"
    secret = settings.app_token
    body = {"message": f"Tưới ngay 10 giây. {secret}", "request_id": str(uuid4())}
    response = client.post(path, json=body, headers=headers)
    assert response.status_code == 200
    turn = response.json()
    assert turn["status"] == "read_only" and "10 giây" in turn["answer"]
    assert secret not in response.text
    assert client.post(path, json=body, headers=headers).json()["message_id"] == turn["message_id"]
    assert provider.calls == 1
    conflict = client.post(path, json={**body, "message": "câu khác"}, headers=headers)
    assert conflict.status_code == 409
    invalid = client.post(path, json={"message": " " * 2, "key": secret}, headers=headers)
    assert invalid.status_code == 422 and secret not in invalid.text
    assert client.post(path, json={"message": "tiếp tục"}, headers=headers).status_code == 200
    assert client.post(path, json={"message": "lượt thứ ba"}, headers=headers).status_code == 429
    items = client.get(path, params={"limit": 1}, headers=headers).json()
    assert [message["role"] for message in items["items"]] == ["user", "assistant"]
    assert items["next_before_turn"] == 2
    with session() as db:
        assert len(list(db.scalars(select(ChatMessage)))) == 4
        assert len(list(db.scalars(select(ChatToolCall)))) == 2
        assert db.scalar(select(Command)) is None


def test_benchmark_catches_fabricated_weather_horizon():
    question = Case(
        id="horizon",
        question="3 giờ tới có mưa không?",
        intents=["weather"],
        weather_minutes=180,
        statuses=["insufficient_data"],
        forbid=["17.0%"],
    )
    assert evaluate(question, question.plan())["passed"]
    assert not evaluate(question, plan("weather", weather_minutes=60))["passed"]


def test_knowledge_citations_are_persisted_without_sending_corpus_to_provider(
    chat_api, monkeypatch
):
    client, session, provider = chat_api
    headers = {"Authorization": f"Bearer {settings.app_token}"}
    base = "/api/v1/chat/conversations"
    thread = client.post(base, json={"default_mode": "cloud"}, headers=headers).json()
    path = f"{base}/{thread['id']}/messages"
    provider.plan = plan("other")
    old = client.post(path, json={"message": "Xin chào"}, headers=headers).json()
    assert old["sources"] == []

    provider.plan = plan("knowledge")
    payload = {"message": "Mốc sương phải kiểm tra gì?", "request_id": str(uuid4())}
    assert client.post(path, json=payload).status_code == 401
    response = client.post(path, json=payload, headers=headers)
    assert response.status_code == 200
    answer = response.json()
    assert "mốc trắng" in answer["answer"]
    assert "mốc trắng" not in str(provider.messages)
    sources = answer["sources"]
    assert sources[0]["citation"]["section"].endswith("trang 24")
    assert sources[0]["citation"]["version"] == "plant-knowledge-v1"
    monkeypatch.setattr(
        knowledge, "load_corpus", lambda: pytest.fail("History reopened the corpus")
    )
    items = client.get(path, headers=headers).json()["items"]
    assert items[1]["details"]["sources"] == []
    assert items[-1]["details"]["sources"] == sources
    replay = client.post(path, json=payload, headers=headers).json()
    assert replay["message_id"] == answer["message_id"] and replay["sources"] == sources
    assert provider.calls == 2
    with session() as db:
        tool = db.scalar(select(ChatToolCall).where(ChatToolCall.tool == "knowledge"))
        assert tool.sanitized_result["sources"] == sources
        assert db.scalar(select(Command)) is None


@pytest.mark.asyncio
async def test_router_preserves_factual_image_reads_and_explicit_follow_on_pump():
    cloud = TestProvider(
        "cloud",
        Plan(
            intents=["leaf", "knowledge"], weather_minutes=None, pump_action=None, pump_seconds=None
        ),
    )
    registry = {"cloud": cloud}
    actual, _ = await chat_llm.route("cloud", "Ảnh lá kia gợi ý bệnh gì?", [], registry)
    assert actual.intents == ["leaf"]
    actual, _ = await chat_llm.route(
        "cloud", "Ảnh lá đó có dấu hiệu gì cần đối chiếu?", [], registry
    )
    assert actual.intents == ["leaf", "knowledge"]
    actual, _ = await chat_llm.route("cloud", "Tra độ ẩm rồi tắt bơm ngay.", [], registry)
    assert actual == plan("pump", action="off")
    actual, _ = await chat_llm.route("cloud", "Tra tài liệu và bật bơm ngay 4 giây.", [], registry)
    assert actual == plan("pump", action="on", seconds=4)
    cloud.plan = plan("irrigation")
    actual, _ = await chat_llm.route("cloud", "Có nên tưới và tắt bơm ngay?", [], registry)
    assert actual == plan("irrigation")


def test_follow_up_keeps_the_same_image_and_compares_both_diseases(chat_api, monkeypatch):
    client, session, provider = chat_api
    monkeypatch.setattr(settings, "chat_rate_limit_per_minute", 10)
    headers = {"Authorization": f"Bearer {settings.app_token}"}
    base = "/api/v1/chat/conversations"

    def new_thread():
        thread = client.post(base, json={"default_mode": "cloud"}, headers=headers).json()
        return f"{base}/{thread['id']}/messages"

    def ask(path, message):
        response = client.post(path, json={"message": message}, headers=headers)
        assert response.status_code == 200
        return response.json()

    def make_image(class_name, label):
        with session.begin() as db:
            if db.get(Device, DEVICE_ID) is None:
                db.add(Device(id=DEVICE_ID))
                db.flush()
            image = PlantImage(
                id=str(uuid4()),
                device_id=DEVICE_ID,
                purpose="leaf",
                source="simulation",
                object_key="fixture-leaf",
                content_type="image/jpeg",
                width=400,
                height=400,
            )
            db.add(image)
            db.flush()
            db.add(
                Job(
                    image_id=image.id,
                    task="leaf",
                    status="completed",
                    provider="trained",
                    model_version="fixture-model",
                    result={
                        "is_mock": False,
                        "class_name": class_name,
                        "label": label,
                        "confidence": 0.978,
                    },
                )
            )
            return image.id

    path = new_thread()
    old = make_image("Tomato___Late_blight", "Mốc sương")
    provider.plan = plan("leaf")
    result = ask(path, "Ảnh lá mới nhất gợi ý bệnh gì?")
    assert result["sources"][0]["ref"] == old and "Mốc sương" in result["answer"]
    new = make_image("Tomato___Early_blight", "Đốm lá sớm")

    provider.plan = Plan(
        intents=["leaf", "knowledge"], weather_minutes=None, pump_action=None, pump_seconds=None
    )
    follow_up = ask(path, "Lá đó nên nhìn mặt nào?")
    assert "mốc trắng" in follow_up["answer"]
    assert "MobileNetV3" not in follow_up["answer"]
    assert [source["ref"] for source in follow_up["sources"]] == [
        old,
        "plant-knowledge-v1:late-blight",
    ]
    assert "mốc trắng" not in str(provider.messages)

    provider.plan = Plan(
        intents=["leaf", "knowledge"], weather_minutes=None, pump_action=None, pump_seconds=None
    )
    comparison = ask(path, "Mốc sương và đốm lá sớm khác nhau thế nào? Nên chụp cận ở đâu?")
    citations = {source["citation"]["document_id"] for source in comparison["sources"]}
    assert citations == {"knvn-late-blight", "umn-spots"}
    assert "Mốc sương —" in comparison["answer"] and "Đốm lá sớm —" in comparison["answer"]
    assert "chụp cả hai mặt lá" in comparison["answer"]

    provider.plan = plan("leaf")
    latest = ask(path, "Còn ảnh lá mới nhất thì sao?")
    assert latest["sources"][0]["ref"] == new and "Đốm lá sớm" in latest["answer"]

    provider.plan = plan("knowledge")
    unrelated = ask(new_thread(), "Lá đó nên nhìn mặt nào?")
    assert unrelated["status"] == "insufficient_data" and unrelated["sources"] == []


def test_treatment_follow_up_does_not_repeat_the_image_diagnosis(chat_api):
    client, _, provider = chat_api
    headers = {"Authorization": f"Bearer {settings.app_token}"}
    base = "/api/v1/chat/conversations"
    thread = client.post(base, json={"default_mode": "cloud"}, headers=headers).json()
    path = f"{base}/{thread['id']}/messages"
    provider.plan = plan("knowledge")
    initial = client.post(path, json={"message": "Mốc sương nhận biết thế nào?"}, headers=headers)
    assert initial.status_code == 200 and "mốc trắng" in initial.json()["answer"]

    provider.plan = Plan(
        intents=["leaf", "knowledge"], weather_minutes=None, pump_action=None, pump_seconds=None
    )
    message = "tôi đã kiểm tra và đúng la mốc sương, có cách nào chữa cho cây ko"
    response = client.post(path, json={"message": message}, headers=headers)
    assert response.status_code == 200
    turn = response.json()
    assert turn["agents_used"] == ["knowledge"] and "hạn chế lây lan" in turn["answer"]
    assert "MobileNetV3" not in turn["answer"] and "mốc trắng" not in turn["answer"]
    assert turn["sources"][0]["ref"] == "plant-knowledge-v1:late-blight-management"
