import json
import subprocess

import pytest

from assister.ai import agy
from assister.ai.agy import NO_IMAGES, NOT_INSTALLED, TOO_LONG, AgyEngine, as_prompt, first_object
from assister.ai.engine import EngineError

SCHEMA = {
    "type": "object",
    "properties": {"actions": {"type": "array", "items": {"type": "object"}}},
    "required": ["actions"],
}


class FakeProcess:
    """agy 대신 정해 둔 출력을 내놓는다. waits만큼은 아직 안 끝난 척한다."""

    def __init__(self, output: str, *, waits: int = 0, error: str = ""):
        self._output, self._error, self._waits = output.encode("utf-8"), error.encode("utf-8"), waits
        self.pid = 4321
        self.killed = False

    def communicate(self, timeout=None):
        if self._waits > 0 and not self.killed:
            self._waits -= 1
            raise subprocess.TimeoutExpired("agy", timeout)
        return self._output, self._error

    def poll(self):
        return None


def answered(response: str, status: str = "SUCCESS") -> str:
    return json.dumps({"conversation_id": "x", "status": status, "response": response, "num_turns": 1}, ensure_ascii=False)


@pytest.fixture
def launched(monkeypatch):
    """agy를 실제로 띄우지 않는다. 띄우려던 명령과 폴더를 적어 둔다."""
    calls = []
    processes = []

    def use(*outputs, **options):
        queue = [FakeProcess(output, **options) for output in outputs]
        processes.extend(queue)

        def popen(command, **kwargs):
            calls.append((command, kwargs))
            return queue.pop(0)

        monkeypatch.setattr(agy.subprocess, "Popen", popen)
        monkeypatch.setattr(AgyEngine, "_kill", staticmethod(lambda process: setattr(process, "killed", True)))
        return AgyEngine(program="agy.exe")

    use.calls, use.processes = calls, processes
    return use


def test_the_talk_is_joined_into_one_text_for_a_tool_that_takes_only_one():
    messages = [
        {"role": "system", "content": "너는 모모야."},
        {"role": "user", "content": "내 이름은 상민이야"},
        {"role": "assistant", "content": "반가워요"},
        {"role": "user", "content": "내 이름 기억해?"},
    ]

    assert as_prompt(messages) == (
        "너는 모모야.\n\n[지금까지의 대화]\n사용자: 내 이름은 상민이야\n캐릭터: 반가워요\n\n[사용자가 방금 한 말]\n내 이름 기억해?"
    )
    with pytest.raises(EngineError, match=NO_IMAGES):
        as_prompt([{"role": "user", "content": "이 화면 뭐야", "images": ["aW1n"]}])


def test_chat_runs_agy_in_an_empty_folder_without_letting_it_use_tools_freely(launched):
    engine = launched(answered("안녕, 상민아!\n안녕, 상민아!\n"))
    pieces = []

    reply = engine.chat([{"role": "user", "content": "안녕"}], on_delta=pieces.append)

    assert reply == "안녕, 상민아!"  # 여러 턴을 돌며 거듭 적은 같은 줄은 한 번만 남긴다
    assert pieces == ["안녕, 상민아!"]
    command, options = launched.calls[0]
    assert command[:2] == ["agy.exe", "-p"] and "[사용자가 방금 한 말]\n안녕" in command[2]
    assert command[3:] == ["--model", "gemini-3.8-flash-low", "--output-format", "json", "--print-timeout", "120s"]
    assert "--dangerously-skip-permissions" not in command
    assert list(options["cwd"].iterdir()) == []  # 에이전트가 읽을 파일이 없는 빈 폴더
    engine.close()
    assert not options["cwd"].exists()


def test_structured_answer_is_picked_out_of_whatever_agy_wrote_around_it(launched):
    wordy = '네, 알겠습니다.\n```json\n{"actions": [{"tool": "add_alarm", "minutes_from_now": 10}], "toolAction": "Finishing"}\n```\n끝.'
    engine = launched(answered(wordy))

    plan = engine.complete_json([{"role": "user", "content": "10분 뒤에 알려 줘"}], SCHEMA)

    assert plan == {"actions": [{"tool": "add_alarm", "minutes_from_now": 10}]}  # 틀에 없는 칸은 버린다
    prompt = launched.calls[0][0][2]
    assert json.dumps(SCHEMA, ensure_ascii=False) in prompt and "--json-schema" not in launched.calls[0][0]


def test_first_object_skips_json_that_does_not_fit_the_form():
    text = '{"note": "생각 중"} 그리고 {"actions": []}'

    assert first_object(text, SCHEMA) == {"actions": []}
    with pytest.raises(EngineError, match="틀에 맞는 답"):
        first_object("JSON이 없는 글", SCHEMA)


def test_failures_are_turned_into_words_the_user_can_act_on(launched):
    with pytest.raises(EngineError, match="답하지 못했어요"):
        launched(answered("", status="ERROR")).chat([{"role": "user", "content": "안녕"}])
    with pytest.raises(EngineError, match="로그인이 필요합니다"):
        launched("이건 JSON이 아님", error="로그인이 필요합니다").chat([{"role": "user", "content": "안녕"}])
    with pytest.raises(EngineError, match=TOO_LONG):
        launched().chat([{"role": "user", "content": "가" * 40_000}])
    with pytest.raises(EngineError, match=NOT_INSTALLED.split(".")[0]):
        engine = launched()
        engine._program = None
        engine.warm_up()


def test_a_slow_answer_can_be_cut_off(launched):
    engine = launched(answered("늦은 답"), waits=50)

    assert engine.chat([{"role": "user", "content": "안녕"}], should_stop=lambda: True) == ""
    assert launched.processes[0].killed
