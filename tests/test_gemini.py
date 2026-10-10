import json

import httpx
import pytest

from assister.ai.engine import EngineError
from assister.ai.gemini import API_URL, BAD_KEY, NO_KEY, OVER_LIMIT, GeminiEngine, api_key

SCHEMA = {"type": "object", "properties": {"summary": {"type": "string"}}, "required": ["summary"]}


def gemini_with(handler, *, key="test-key") -> GeminiEngine:
    return GeminiEngine(key=key, client=httpx.Client(transport=httpx.MockTransport(handler)))


def answer(text: str, status: str = "completed") -> httpx.Response:
    steps = [{"type": "thought"}, {"type": "model_output", "content": [{"type": "text", "text": text}]}]
    return httpx.Response(200, json={"id": "v1_x", "object": "interaction", "status": status, "steps": steps})


def events(*items: dict) -> httpx.Response:
    lines = "".join(f"event: {item['event_type']}\ndata: {json.dumps(item, ensure_ascii=False)}\n\n" for item in items)
    return httpx.Response(200, content=lines.encode("utf-8"))


def text_delta(text: str) -> dict:
    return {"event_type": "step.delta", "index": 0, "delta": {"type": "text", "text": text}}


def test_structured_answer_is_asked_for_and_read_the_way_gemini_documents_it():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return answer('{"summary": "코드를 고치고 있다"}')

    messages = [
        {"role": "system", "content": "화면을 보고 한 문장으로 적는다."},
        {"role": "user", "content": "지금 화면", "images": ["aW1n"]},
    ]

    assert gemini_with(handler).complete_json(messages, SCHEMA) == {"summary": "코드를 고치고 있다"}

    request = seen[0]
    assert str(request.url) == API_URL  # 키를 주소에 붙이지 않는다
    assert request.headers["x-goog-api-key"] == "test-key"
    body = json.loads(request.content)
    assert body["model"] == "gemini-3.8-flash"
    assert body["system_instruction"] == "화면을 보고 한 문장으로 적는다."
    assert body["input"] == [
        {
            "type": "user_input",
            "content": [{"type": "text", "text": "지금 화면"}, {"type": "image", "data": "aW1n", "mime_type": "image/jpeg"}],
        }
    ]
    assert body["response_format"] == {"type": "text", "mime_type": "application/json", "schema": SCHEMA}
    assert body["generation_config"]["temperature"] == 0  # 작업을 고를 때는 매번 같은 답이 나와야 한다
    assert body["store"] is False and body["stream"] is False  # 주고받은 내용을 Google 쪽에 남기지 않는다


def test_chat_sends_the_whole_talk_and_streams_the_reply():
    bodies, pieces = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        done = {"event_type": "interaction.completed", "interaction": {"status": "completed"}}
        return events({"event_type": "interaction.created"}, text_delta("안녕"), text_delta("하세요"), done, text_delta("버려질 말"))

    talk = [
        {"role": "user", "content": "내 이름은 상민이야"},
        {"role": "assistant", "content": "반가워요"},
        {"role": "user", "content": "내 이름 기억해?"},
    ]

    assert gemini_with(handler).chat(talk, on_delta=pieces.append) == "안녕하세요"
    assert pieces == ["안녕", "하세요"]
    assert [step["type"] for step in bodies[0]["input"]] == ["user_input", "model_output", "user_input"]
    assert bodies[0]["stream"] is True and "system_instruction" not in bodies[0]


def test_chat_stops_when_told_to():
    engine = gemini_with(lambda request: events(text_delta("하나"), text_delta("둘"), text_delta("셋")))
    pieces = []

    engine.chat([{"role": "user", "content": "세어 봐"}], on_delta=pieces.append, should_stop=lambda: len(pieces) >= 1)

    assert pieces == ["하나"]


def test_missing_key_is_said_plainly_without_calling_google(monkeypatch):
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    calls = []
    engine = GeminiEngine(client=httpx.Client(transport=httpx.MockTransport(lambda request: calls.append(request))))

    for ask in (engine.warm_up, lambda: engine.chat([{"role": "user", "content": "안녕"}]), lambda: engine.complete_json([], SCHEMA)):
        with pytest.raises(EngineError, match="GEMINI_API_KEY"):
            ask()
    assert calls == [] and "GEMINI_API_KEY" in NO_KEY


def test_key_is_read_from_the_environment(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_API_KEY", " from-google ")
    assert api_key() == "from-google"

    monkeypatch.setenv("GEMINI_API_KEY", "from-gemini")
    assert api_key() == "from-gemini"


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(429, json={"error": {"message": "Quota exceeded"}}), OVER_LIMIT),
        (httpx.Response(403, json={"error": {"message": "Permission denied"}}), BAD_KEY),
        (httpx.Response(400, json={"error": {"message": "API key not valid. Please pass a valid API key."}}), BAD_KEY),
        (httpx.Response(500, text="oops"), "Gemini 오류 (500)"),
    ],
)
def test_refusals_are_turned_into_words_the_user_can_act_on(response, message):
    engine = gemini_with(lambda request: response)

    with pytest.raises(EngineError, match=message.split("(")[0].strip()):
        engine.complete_json([{"role": "user", "content": "안녕"}], SCHEMA)
    with pytest.raises(EngineError, match=message.split("(")[0].strip()):
        engine.chat([{"role": "user", "content": "안녕"}])


def test_unfinished_or_unreadable_answers_and_lost_connections_are_reported():
    def offline(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no network")

    with pytest.raises(EngineError, match="끝내지 못했어요"):
        gemini_with(lambda request: answer("{}", status="incomplete")).complete_json([], SCHEMA)
    with pytest.raises(EngineError, match="알 수 없는 답"):
        gemini_with(lambda request: answer("JSON이 아님")).complete_json([], SCHEMA)
    with pytest.raises(EngineError, match="인터넷"):
        gemini_with(offline).chat([{"role": "user", "content": "안녕"}])
    with pytest.raises(EngineError, match="Gemini 오류: 과부하"):
        gemini_with(lambda request: events({"event_type": "error", "error": {"message": "과부하"}})).chat(
            [{"role": "user", "content": "안녕"}]
        )
