"""AI 엔진 연결. 지금은 내 PC의 Ollama만 쓰고, 나중에 클라우드 API를 같은 모양으로 붙일 수 있다."""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from collections.abc import Callable
from typing import Protocol
from urllib.parse import urlsplit

import httpx

DEFAULT_URL = "http://localhost:11434"
DEFAULT_NUM_CTX = 16384
REQUEST_TIMEOUT = httpx.Timeout(connect=5.0, read=300.0, write=30.0, pool=5.0)
NOT_RUNNING = "Ollama에 연결할 수 없습니다. Ollama가 켜져 있는지 확인해 주세요."
CLOSED = "앱을 닫는 중이라 AI 엔진과의 연결을 끊었습니다."


LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
START_POLL_SEC = 0.5
START_TRIES = 40  # 켜지기까지 최대 20초 기다린다


class EngineError(Exception):
    """AI 엔진이 답을 주지 못했을 때. 메시지는 사용자에게 그대로 보여 줄 수 있는 말이다."""


def start_ollama() -> None:
    """내 PC에 설치된 Ollama를 켠다. 설치돼 있지 않으면 아무것도 하지 않는다.

    'ollama list'를 부르면 Ollama가 꺼져 있을 때 스스로 앱을 띄운다. 그렇게 뜬 앱은
    사용자가 Ollama에 정해 둔 설정(모델 폴더 등)을 그대로 쓴다.
    """
    program = shutil.which("ollama")
    if program is None:
        return
    # 출력을 버려야 한다. 파이프로 받으면 새로 뜬 Ollama 앱이 그 파이프를 물고 있어 끝나지 않는다.
    subprocess.Popen(  # noqa: S603 (PATH에서 찾은 ollama만 실행한다)
        [program, "list"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


class ChatEngine(Protocol):
    def warm_up(self) -> None: ...

    def chat(
        self,
        messages: list[dict],
        *,
        on_delta: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> str: ...

    def complete_json(self, messages: list[dict], schema: dict) -> object: ...


class OllamaEngine:
    def __init__(
        self,
        model: str,
        *,
        base_url: str = DEFAULT_URL,
        num_ctx: int = DEFAULT_NUM_CTX,
        keep_alive: str = "30m",
        # 높이면 말이 다채로워지지만 엉뚱한 글자가 섞이는 일도 늘어난다.
        temperature: float = 0.5,
        client: httpx.Client | None = None,
        start_server: Callable[[], None] | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """start_server를 주면, 내 PC의 Ollama가 꺼져 있을 때 warm_up이 그것을 불러 켠다."""
        self.model = model
        self._closed = False
        self._local = urlsplit(base_url).hostname in LOCAL_HOSTS
        self._start_server = start_server
        self._sleep = sleep
        self._url = f"{base_url.rstrip('/')}/api/chat"
        self._num_ctx = num_ctx
        self._temperature = temperature
        self._keep_alive = keep_alive
        self._client = client or httpx.Client(timeout=REQUEST_TIMEOUT)

    def close(self) -> None:
        """연결을 닫는다. 그때 다른 스레드에서 하던 요청은 EngineError로 끝난다."""
        self._closed = True
        self._client.close()

    def _failure(self, error: Exception) -> EngineError:
        """통신 오류를 사용자에게 보여 줄 수 있는 말로 바꾼다."""
        if self._closed:
            return EngineError(CLOSED)
        if isinstance(error, httpx.ConnectError):
            return EngineError(NOT_RUNNING)
        if isinstance(error, httpx.TimeoutException):
            return EngineError("AI 엔진이 너무 오래 답하지 않습니다.")
        return EngineError(f"AI 엔진과 통신하지 못했습니다: {error}")

    def _payload(self, messages: list[dict], *, stream: bool, temperature: float) -> dict:
        return {
            "model": self.model,
            "messages": messages,
            "stream": stream,
            # 생각 과정을 길게 쓰는 기능은 끈다. 말풍선 대화에는 빠른 답이 더 중요하다.
            "think": False,
            "keep_alive": self._keep_alive,
            # num_ctx가 요청마다 다르면 Ollama가 모델을 다시 올리므로 늘 같은 값을 보낸다.
            "options": {"num_ctx": self._num_ctx, "temperature": temperature},
        }

    def _post(self, payload: dict) -> dict:
        try:
            try:
                response = self._client.post(self._url, json=payload)
            except (httpx.ReadError, httpx.WriteError, httpx.RemoteProtocolError):
                if self._closed:
                    raise
                # 연결이 도중에 끊긴 것. 새 연결로 한 번만 다시 해 본다.
                response = self._client.post(self._url, json=payload)
        except (httpx.HTTPError, RuntimeError) as error:  # RuntimeError: 닫힌 연결로 보내려 했을 때
            raise self._failure(error) from error
        if response.status_code != 200:
            raise self._http_error(response)
        try:
            return response.json()
        except json.JSONDecodeError as error:
            raise EngineError(f"AI 엔진이 알 수 없는 답을 보냈습니다: {error}") from error

    def warm_up(self) -> None:
        """모델을 미리 메모리에 올린다. 디스크에서 처음 읽을 때는 1분 넘게 걸릴 수 있다.

        내 PC의 Ollama가 꺼져 있으면 켜고 나서 다시 해 본다.
        """
        payload = self._payload([], stream=False, temperature=self._temperature)
        try:
            self._post(payload)
        except EngineError as error:
            if str(error) != NOT_RUNNING or not self._local or self._start_server is None:
                raise
            self._start_server()
            for _ in range(START_TRIES):
                self._sleep(START_POLL_SEC)
                try:
                    self._post(payload)
                    return
                except EngineError as retry_error:
                    if str(retry_error) != NOT_RUNNING:
                        raise
            raise

    def complete_json(self, messages: list[dict], schema: dict) -> object:
        """정해 준 틀(JSON 스키마)에 맞는 답을 받아 풀어서 돌려준다."""
        payload = {**self._payload(messages, stream=False, temperature=0), "format": schema}
        content = self._post(payload).get("message", {}).get("content", "")
        try:
            return json.loads(content)
        except json.JSONDecodeError as error:
            raise EngineError(f"AI 엔진이 JSON이 아닌 답을 보냈습니다: {content[:80]!r}") from error

    def chat(
        self,
        messages: list[dict],
        *,
        on_delta: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> str:
        """글이 나오는 대로 on_delta로 넘기고, 다 된 답을 돌려준다."""
        payload = self._payload(messages, stream=True, temperature=self._temperature)
        pieces: list[str] = []
        try:
            with self._client.stream("POST", self._url, json=payload) as response:
                if response.status_code != 200:
                    response.read()
                    raise self._http_error(response)
                for line in response.iter_lines():
                    if not line:
                        continue
                    chunk = json.loads(line)
                    if "error" in chunk:
                        raise EngineError(f"AI 엔진 오류: {chunk['error']}")
                    if piece := chunk.get("message", {}).get("content", ""):
                        pieces.append(piece)
                        if on_delta:
                            on_delta(piece)
                    if chunk.get("done") or (should_stop and should_stop()):
                        break
        except (httpx.HTTPError, json.JSONDecodeError, RuntimeError) as error:
            raise self._failure(error) from error
        return "".join(pieces)

    def _http_error(self, response: httpx.Response) -> EngineError:
        try:
            detail = response.json().get("error", "")
        except (json.JSONDecodeError, AttributeError):
            detail = response.text[:200]
        if response.status_code == 404:
            return EngineError(f"모델 {self.model}이(가) 없습니다. 'ollama pull {self.model}'로 받아 주세요.")
        return EngineError(f"AI 엔진 오류 ({response.status_code}): {detail}")
