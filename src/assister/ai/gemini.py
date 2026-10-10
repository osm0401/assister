"""Google Gemini 연결 (Interactions API). Ollama 엔진과 같은 모양이라 그대로 바꿔 끼울 수 있다.

이 엔진을 쓰면 대화, 기억해 둔 사실, 화면 그림이 Google 서버로 나간다.
API 키는 환경 변수에서만 읽고, 설정 파일이나 기록에는 남기지 않는다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

import httpx

from assister.ai.engine import CLOSED, EngineError

API_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
KEY_NAMES = ("GEMINI_API_KEY", "GOOGLE_API_KEY")
DEFAULT_MODEL = "gemini-3.8-flash"
IMAGE_TYPE = "image/jpeg"  # screen/capture.py가 화면을 JPEG로 넘긴다
REQUEST_TIMEOUT = httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=10.0)
STEP_TYPES = {"user": "user_input", "assistant": "model_output"}
NO_KEY = "Gemini API 키가 없어요. 환경 변수 GEMINI_API_KEY에 키를 넣고 앱을 다시 켜 주세요."
BAD_KEY = "Gemini가 API 키를 받아 주지 않아요. 키가 맞는지 확인해 주세요."
OVER_LIMIT = "Gemini 사용 한도를 넘었어요. 조금 뒤에 다시 해 주세요."
OFFLINE = "Gemini에 연결하지 못했어요. 인터넷 연결을 확인해 주세요."


def api_key() -> str:
    """환경 변수에 넣어 둔 키. 없으면 빈 글."""
    return next((value for name in KEY_NAMES if (value := os.environ.get(name, "").strip())), "")


def _steps(messages: list[dict]) -> list[dict]:
    """Ollama 모양의 대화를 Gemini가 받는 단계 목록으로 바꾼다. 지시문(system)은 따로 보내므로 뺀다."""
    steps = []
    for message in messages:
        if message["role"] == "system":
            continue
        parts: list[dict] = [{"type": "text", "text": message["content"]}] if message.get("content") else []
        parts += [{"type": "image", "data": image, "mime_type": IMAGE_TYPE} for image in message.get("images", ())]
        if parts:
            steps.append({"type": STEP_TYPES.get(message["role"], "user_input"), "content": parts})
    return steps


class GeminiEngine:
    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        *,
        key: str | None = None,
        temperature: float = 0.5,
        # 생각을 길게 할수록 답이 늦다. 주고받는 대화에는 빠른 답이 더 중요하다.
        thinking_level: str = "low",
        client: httpx.Client | None = None,
    ) -> None:
        self.model = model
        self._key = api_key() if key is None else key
        self._temperature = temperature
        self._thinking_level = thinking_level
        self._closed = False
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT)

    def warm_up(self) -> None:
        """미리 올려 둘 것은 없다. 키가 있는지만 본다."""
        if not self._key:
            raise EngineError(NO_KEY)

    def close(self) -> None:
        self._closed = True
        self._client.close()

    def _body(self, messages: list[dict], *, temperature: float, stream: bool) -> dict:
        body = {
            "model": self.model,
            "input": _steps(messages),
            "generation_config": {"temperature": temperature, "thinking_level": self._thinking_level},
            "stream": stream,
            "store": False,  # 주고받은 내용을 Google 쪽에 남겨 두지 않는다
        }
        if system := "\n\n".join(message["content"] for message in messages if message["role"] == "system"):
            body["system_instruction"] = system
        return body

    def _failure(self, error: Exception) -> EngineError:
        if self._closed:
            return EngineError(CLOSED)
        if isinstance(error, httpx.ConnectError):
            return EngineError(OFFLINE)
        if isinstance(error, httpx.TimeoutException):
            return EngineError("Gemini가 너무 오래 답하지 않아요.")
        return EngineError(f"Gemini와 통신하지 못했어요: {error}")

    @staticmethod
    def _http_error(response: httpx.Response) -> EngineError:
        try:
            detail = str(response.json().get("error", {}).get("message", ""))
        except (json.JSONDecodeError, AttributeError):
            detail = response.text[:200]
        if response.status_code in (401, 403) or "API key" in detail:
            return EngineError(BAD_KEY)
        if response.status_code == 429:
            return EngineError(OVER_LIMIT)
        return EngineError(f"Gemini 오류 ({response.status_code}): {detail}")

    def complete_json(self, messages: list[dict], schema: dict) -> object:
        """정해 준 틀(JSON 스키마)에 맞는 답을 받아 풀어서 돌려준다."""
        self.warm_up()
        body = self._body(messages, temperature=0, stream=False)
        body["response_format"] = {"type": "text", "mime_type": "application/json", "schema": schema}
        try:
            response = self._client.post(API_URL, json=body, headers={"x-goog-api-key": self._key})
        except (httpx.HTTPError, RuntimeError) as error:  # RuntimeError: 닫힌 연결로 보내려 했을 때
            raise self._failure(error) from error
        if response.status_code != 200:
            raise self._http_error(response)
        try:
            answer = response.json()
            if answer.get("status", "completed") != "completed":
                raise EngineError(f"Gemini가 답을 끝내지 못했어요 ({answer.get('status')})")
            text = "".join(
                part.get("text", "")
                for step in answer.get("steps", ())
                if step.get("type") == "model_output"
                for part in step.get("content", ())
                if part.get("type") == "text"
            )
            return json.loads(text)
        except (json.JSONDecodeError, AttributeError) as error:
            raise EngineError(f"Gemini가 알 수 없는 답을 보냈어요: {response.text[:80]!r}") from error

    def chat(
        self,
        messages: list[dict],
        *,
        on_delta: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> str:
        """글이 나오는 대로 on_delta로 넘기고, 다 된 답을 돌려준다."""
        self.warm_up()
        body = self._body(messages, temperature=self._temperature, stream=True)
        pieces: list[str] = []
        try:
            with self._client.stream("POST", API_URL, json=body, headers={"x-goog-api-key": self._key}) as response:
                if response.status_code != 200:
                    response.read()
                    raise self._http_error(response)
                for line in response.iter_lines():
                    if should_stop and should_stop():
                        break
                    if not line.startswith("data:"):
                        continue
                    event = json.loads(line[len("data:") :])
                    kind = event.get("event_type")
                    if kind == "error":
                        raise EngineError(f"Gemini 오류: {event.get('error', {}).get('message', '')}")
                    if kind == "interaction.completed":
                        break
                    delta = event.get("delta", {})
                    if kind == "step.delta" and delta.get("type") == "text" and delta.get("text"):
                        pieces.append(delta["text"])
                        if on_delta:
                            on_delta(delta["text"])
        except (httpx.HTTPError, json.JSONDecodeError, RuntimeError) as error:
            raise self._failure(error) from error
        return "".join(pieces)
