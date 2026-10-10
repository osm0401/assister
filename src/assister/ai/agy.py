"""Antigravity CLI(agy)를 AI 엔진으로 쓴다. API 키 없이 Antigravity 로그인만으로 Gemini를 부른다.

agy는 코딩 에이전트다. 한 번 부를 때마다 자기 지시문을 통째로 싣고 가서 답이 7초쯤 걸리고 토큰도 많이 든다.
그림은 받지 못하므로 화면 보기와 PC 조작에는 쓸 수 없다(그 둘은 내 PC의 Ollama가 맡는다).

- 빈 임시 폴더에서 실행한다. 프로젝트 폴더에서 돌리면 에이전트가 그 안의 파일을 읽는다.
- 도구를 허락 없이 쓰게 하는 옵션은 붙이지 않는다. 기본 모드에서는 명령 실행이 거부되는 것을 확인했다.
- 대화와 기억해 둔 사실이 Google 서버로 나가고, 부를 때마다 Antigravity에 대화가 하나씩 쌓인다.
"""

from __future__ import annotations

import json
import locale
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path

from assister.ai.engine import CLOSED, EngineError

DEFAULT_MODEL = "gemini-3.8-flash-low"
TIMEOUT_SEC = 120
POLL_SEC = 0.3
MAX_PROMPT_CHARS = 30000  # Windows는 명령 한 줄이 3만 2천 자를 넘지 못한다
NOT_INSTALLED = "agy를 찾을 수 없어요. Antigravity CLI가 깔려 있는지 확인해 주세요."
NO_IMAGES = "agy는 그림을 읽지 못해요."
TOO_LONG = "말이 너무 길어서 agy에 보낼 수 없어요."
SPEAKERS = {"user": "사용자", "assistant": "캐릭터"}
REPLY_ONLY = "위에 맞춰 답하는 말만 쓴다. 도구는 쓰지 않고, 파일을 만들거나 명령을 실행하겠다고 하지 않는다."
JSON_ONLY = (
    "아래 JSON 스키마에 맞는 JSON 하나만 출력한다. 설명, 인사, 코드 블록 표시는 붙이지 않는다. "
    "도구는 쓰지 않는다. 사용자의 부탁을 직접 해 주려 하지 말고 JSON만 쓴다.\n"
)


def as_prompt(messages: list[dict]) -> str:
    """Ollama 모양의 대화를 글 하나로 잇는다. agy는 글 하나만 받는다."""
    if any(message.get("images") for message in messages):
        raise EngineError(NO_IMAGES)
    parts = [message["content"] for message in messages if message["role"] == "system"]
    talk = [message for message in messages if message["role"] != "system" and message.get("content")]
    if talk[:-1]:
        lines = [f"{SPEAKERS.get(message['role'], message['role'])}: {message['content']}" for message in talk[:-1]]
        parts.append("[지금까지의 대화]\n" + "\n".join(lines))
    if talk:
        parts.append(f"[사용자가 방금 한 말]\n{talk[-1]['content']}")
    return "\n\n".join(parts)


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode(locale.getpreferredencoding(False), errors="replace")


def _tidy(response: str) -> str:
    """같은 줄이 잇달아 되풀이되면 한 번만 남긴다. agy가 여러 턴을 돌며 같은 답을 거듭 적을 때가 있다."""
    kept: list[str] = []
    for line in response.strip().splitlines():
        if not (kept and line.strip() and line.strip() == kept[-1].strip()):
            kept.append(line)
    return "\n".join(kept).strip()


def first_object(text: str, schema: dict) -> dict:
    """글 속에서 틀에 맞는 첫 JSON을 찾아, 틀에 없는 칸을 버리고 돌려준다."""
    required = schema.get("required", ())
    decoder = json.JSONDecoder()
    for start in (index for index, char in enumerate(text) if char == "{"):
        try:
            value, _ = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and all(key in value for key in required):
            known = schema.get("properties")
            return {key: item for key, item in value.items() if key in known} if known else value
    raise EngineError(f"agy가 틀에 맞는 답을 주지 않았어요: {text[:80]!r}")


class AgyEngine:
    sees_images = False  # 화면 보기와 PC 조작은 다른 엔진이 맡아야 한다

    def __init__(self, model: str = DEFAULT_MODEL, *, program: str | None = None, timeout_sec: int = TIMEOUT_SEC) -> None:
        self.model = model
        self._program = program or shutil.which("agy")
        self._timeout_sec = timeout_sec
        self._closed = False
        self._process: subprocess.Popen | None = None
        self._folder = Path(tempfile.mkdtemp(prefix="assister-agy-"))

    def warm_up(self) -> None:
        """미리 올려 둘 것은 없다. agy가 깔려 있는지만 본다."""
        if self._program is None:
            raise EngineError(NOT_INSTALLED)

    def close(self) -> None:
        self._closed = True
        if self._process is not None and self._process.poll() is None:
            self._kill(self._process)
        shutil.rmtree(self._folder, ignore_errors=True)

    @staticmethod
    def _kill(process: subprocess.Popen) -> None:
        # 딸린 프로그램까지 끊는다. 남아 있으면 출력 통로를 물고 있어 읽기가 끝나지 않는다.
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(process.pid)], capture_output=True, check=False)  # noqa: S603, S607

    def _ask(self, prompt: str, should_stop: Callable[[], bool] | None = None) -> str:
        self.warm_up()
        if len(prompt) > MAX_PROMPT_CHARS:
            raise EngineError(TOO_LONG)
        command = [
            self._program, "-p", prompt, "--model", self.model,
            "--output-format", "json", "--print-timeout", f"{self._timeout_sec}s",
        ]  # fmt: skip
        try:
            process = subprocess.Popen(  # noqa: S603 (PATH에서 찾은 agy만 실행한다)
                command,
                cwd=self._folder,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except OSError as error:
            raise EngineError(CLOSED if self._closed else f"agy를 실행하지 못했어요: {error}") from error
        self._process = process
        waited = 0.0
        while True:
            try:
                out, err = process.communicate(timeout=POLL_SEC)
                break
            except subprocess.TimeoutExpired:
                waited += POLL_SEC
                stopped = bool(should_stop and should_stop())
                if not (self._closed or stopped or waited > self._timeout_sec + 10):
                    continue
                self._kill(process)
                process.communicate()
                if self._closed:
                    raise EngineError(CLOSED) from None
                if stopped:
                    return ""
                raise EngineError("agy가 너무 오래 답하지 않아요.") from None
        try:
            answer = json.loads(_decode(out))
        except json.JSONDecodeError as error:
            detail = (_decode(err).strip() or _decode(out).strip())[-200:]
            raise EngineError(f"agy가 알 수 없는 답을 보냈어요: {detail}") from error
        if not isinstance(answer, dict) or answer.get("status") != "SUCCESS":
            status = answer.get("status") if isinstance(answer, dict) else answer
            raise EngineError(f"agy가 답하지 못했어요 ({status})")
        return _tidy(str(answer.get("response", "")))

    def chat(
        self,
        messages: list[dict],
        *,
        on_delta: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> str:
        """답을 받아 돌려준다. 글이 조각조각 흘러나오지는 않고, 다 된 답이 한 번에 온다."""
        text = self._ask(f"{as_prompt(messages)}\n\n{REPLY_ONLY}", should_stop)
        if text and on_delta:
            on_delta(text)
        return text

    def complete_json(self, messages: list[dict], schema: dict) -> object:
        """정해 준 틀(JSON 스키마)에 맞는 답을 받아 풀어서 돌려준다.

        agy의 --json-schema는 쓰지 않는다. 그 길은 '끝내기' 도구를 거치는데 도구가 거부돼 네 턴을 돌았고
        (13~29초), 답도 다른 글과 섞여 나왔다. 틀을 글로 일러 주면 한 턴에 끝난다.
        """
        prompt = f"{as_prompt(messages)}\n\n{JSON_ONLY}{json.dumps(schema, ensure_ascii=False)}"
        return first_object(self._ask(prompt), schema)
