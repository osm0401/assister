import json
from datetime import datetime, timedelta

import httpx
import pytest

from assister.ai.chat import FALLBACK_REPLY, MAX_ACTIONS, ChatSession
from assister.ai.engine import EngineError, OllamaEngine
from assister.ai.prompt import build_planner_prompt, build_system_prompt, format_now
from assister.ai.search import Hit, SearchError
from assister.ai.tools import ACTION_FORMAT, Outcome, ToolBox
from assister.memory.store import MemoryStore
from assister.system.shell import CommandResult
from assister.tasks.repository import TaskRepository

NOW = datetime(2026, 10, 6, 15, 0, 0)  # 화요일


# ---------- Ollama 연결 ----------


def ollama_with(handler) -> OllamaEngine:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaEngine("qwen3.5:9b", client=client)


def stream_of(*chunks: dict) -> httpx.Response:
    body = "\n".join(json.dumps(chunk, ensure_ascii=False) for chunk in chunks)
    return httpx.Response(200, content=body.encode("utf-8"))


def text_chunk(text: str, done: bool = False) -> dict:
    return {"message": {"role": "assistant", "content": text}, "done": done}


def test_engine_streams_text_piece_by_piece():
    engine = ollama_with(lambda request: stream_of(text_chunk("안녕"), text_chunk("하세요"), text_chunk("", done=True)))
    pieces = []

    reply = engine.chat([{"role": "user", "content": "안녕"}], on_delta=pieces.append)

    assert pieces == ["안녕", "하세요"]
    assert reply == "안녕하세요"


def test_engine_sends_model_and_options():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return stream_of(text_chunk("네", done=True))

    ollama_with(handler).chat([{"role": "user", "content": "안녕"}])

    assert seen["model"] == "qwen3.5:9b"
    assert seen["stream"] is True
    assert seen["think"] is False
    assert seen["options"]["num_ctx"] == 16384


def test_engine_can_be_stopped_midway():
    engine = ollama_with(lambda request: stream_of(text_chunk("하나"), text_chunk("둘"), text_chunk("셋", done=True)))
    pieces = []

    reply = engine.chat([{"role": "user", "content": "세어 봐"}], on_delta=pieces.append, should_stop=lambda: len(pieces) >= 1)

    assert reply == "하나"


def test_engine_complete_json_asks_for_the_given_shape_and_parses_it():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"actions": [{"tool": "list_todos"}]}'}, "done": True})

    result = ollama_with(handler).complete_json([{"role": "user", "content": "할 일 뭐야"}], ACTION_FORMAT)

    assert result == {"actions": [{"tool": "list_todos"}]}
    assert seen["format"] == ACTION_FORMAT
    assert seen["stream"] is False
    assert seen["options"]["temperature"] == 0


def test_engine_complete_json_rejects_text_that_is_not_json():
    engine = ollama_with(lambda request: httpx.Response(200, json={"message": {"content": "글쎄요"}, "done": True}))

    with pytest.raises(EngineError, match="JSON"):
        engine.complete_json([{"role": "user", "content": "안녕"}], ACTION_FORMAT)


def test_engine_warm_up_loads_the_model_without_asking_anything():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"done": True, "done_reason": "load"})

    ollama_with(handler).warm_up()

    assert seen["model"] == "qwen3.5:9b"
    assert seen["messages"] == []
    assert seen["options"]["num_ctx"] == 16384


def test_engine_warm_up_reports_failure():
    with pytest.raises(EngineError, match="qwen3.5:9b"):
        ollama_with(lambda request: httpx.Response(404, json={"error": "not found"})).warm_up()


def flaky_ollama(*, comes_up: bool, url: str = "http://localhost:11434"):
    """꺼져 있다가, 켜 달라고 하면(comes_up일 때) 켜지는 Ollama."""
    state = {"up": False, "starts": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if not state["up"]:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"done": True, "version": "0.33.2"})

    def start():
        state["starts"] += 1
        state["up"] = comes_up

    engine = OllamaEngine(
        "qwen3.5:9b",
        base_url=url,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        start_server=start,
        sleep=lambda seconds: None,
    )
    return engine, state


def test_warm_up_starts_ollama_when_it_is_not_running():
    engine, state = flaky_ollama(comes_up=True)

    engine.warm_up()

    assert state["starts"] == 1


def test_warm_up_gives_up_when_ollama_does_not_come_up():
    engine, state = flaky_ollama(comes_up=False)

    with pytest.raises(EngineError, match="Ollama"):
        engine.warm_up()

    assert state["starts"] == 1


def test_ollama_on_another_machine_is_never_started_from_here():
    engine, state = flaky_ollama(comes_up=True, url="http://192.168.0.10:11434")

    with pytest.raises(EngineError, match="Ollama"):
        engine.warm_up()

    assert state["starts"] == 0


def test_one_dropped_connection_is_retried_quietly():
    """연결이 한 번 끊기는 일은 드물게 있다(이 PC에서 WinError 10038로 한 번 봤다). 한 번은 다시 해 본다."""
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ReadError("[WinError 10038] 소켓 이외의 개체에 작업을 시도했습니다")
        return httpx.Response(200, json={"message": {"content": '{"actions": []}'}, "done": True})

    assert ollama_with(handler).complete_json([{"role": "user", "content": "안녕"}], ACTION_FORMAT) == {"actions": []}
    assert len(attempts) == 2


def test_connection_that_keeps_dropping_is_reported():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("끊김")

    with pytest.raises(EngineError, match="통신"):
        ollama_with(handler).complete_json([{"role": "user", "content": "안녕"}], ACTION_FORMAT)


def test_requests_after_closing_end_as_an_ordinary_engine_error():
    """앱을 끄는 동안 다른 스레드가 요청을 보내도, 처리하지 못하는 오류로 스레드가 죽지 않게 한다."""
    engine = ollama_with(lambda request: stream_of(text_chunk("네", done=True)))

    engine.close()

    for call in (
        lambda: engine.chat([{"role": "user", "content": "안녕"}]),
        lambda: engine.complete_json([{"role": "user", "content": "안녕"}], ACTION_FORMAT),
        engine.warm_up,
    ):
        with pytest.raises(EngineError, match="닫"):
            call()


def test_engine_explains_when_ollama_is_not_running():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(EngineError, match="Ollama"):
        ollama_with(handler).chat([{"role": "user", "content": "안녕"}])
    with pytest.raises(EngineError, match="Ollama"):
        ollama_with(handler).complete_json([{"role": "user", "content": "안녕"}], ACTION_FORMAT)


def test_engine_explains_when_model_is_missing():
    engine = ollama_with(lambda request: httpx.Response(404, json={"error": "model 'qwen3.5:9b' not found"}))

    with pytest.raises(EngineError, match="qwen3.5:9b"):
        engine.chat([{"role": "user", "content": "안녕"}])


def test_engine_reports_error_sent_inside_the_stream():
    engine = ollama_with(lambda request: stream_of({"error": "CUDA error: unknown error"}))

    with pytest.raises(EngineError, match="CUDA"):
        engine.chat([{"role": "user", "content": "안녕"}])


# ---------- 지시문 ----------


def test_time_is_written_the_way_people_say_it():
    assert format_now(NOW) == "2026년 10월 6일 화요일 오후 3:00"
    assert format_now(datetime(2026, 10, 11, 0, 5)) == "2026년 10월 11일 일요일 오전 12:05"


def test_system_prompt_carries_name_time_and_facts():
    prompt = build_system_prompt("모모", persona="", facts=["이름은 상민", "매운 음식을 좋아함"], now=NOW)

    assert "모모" in prompt
    assert "2026년 10월 6일 화요일 오후 3:00" in prompt
    assert "- 이름은 상민" in prompt and "- 매운 음식을 좋아함" in prompt
    assert "말투 메모" not in prompt


def test_system_prompt_adds_persona_only_when_set():
    prompt = build_system_prompt("모모", persona="반말로, 장난기 있게", facts=[], now=NOW)

    assert "반말로, 장난기 있게" in prompt
    assert "기억하는 것" not in prompt


def test_planner_prompt_lists_every_tool_the_time_current_state_and_recent_talk():
    prompt = build_planner_prompt(
        ToolBox.schemas,
        recent=[("user", "할 일 뭐 있어?"), ("assistant", "1. 우유 사기")],
        state="[지금 남은 할 일]\n1. 우유 사기",
        now=NOW,
    )

    for schema in ToolBox.schemas:
        assert schema["function"]["name"] in prompt
    assert "2026년 10월 6일 화요일 오후 3:00" in prompt
    assert "[지금 남은 할 일]\n1. 우유 사기" in prompt
    assert "사용자: 할 일 뭐 있어?" in prompt and "캐릭터: 1. 우유 사기" in prompt
    # 작은 모델은 '다음 주 화요일'을 날짜로 바꾸는 계산을 자주 틀려서, 달력을 보여 주고 찾게 한다.
    assert "2026-10-06 화요일(오늘)" in prompt and "2026-10-13 화요일" in prompt


# ---------- 도구 ----------


@pytest.fixture
def stores(tmp_path):
    tasks = TaskRepository(tmp_path / "tasks.db")
    memory = MemoryStore(tmp_path / "memory.db")
    yield tasks, memory
    tasks.close()
    memory.close()


@pytest.fixture
def toolbox(stores):
    return ToolBox(*stores)


def test_alarm_tool_with_minutes(toolbox, stores):
    outcome = toolbox.execute("add_alarm", {"minutes_from_now": 10, "message": "빨래 꺼내기"}, NOW)

    assert outcome == Outcome("오후 3:10에 '빨래 꺼내기' 알려 줄게요.")
    assert [(alarm.message, alarm.due_at) for alarm in stores[0].list_pending_alarms()] == [
        ("빨래 꺼내기", NOW + timedelta(minutes=10))
    ]


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ({"time": "18:30", "message": "저녁"}, datetime(2026, 10, 6, 18, 30)),
        ({"time": "09:00", "message": "회의"}, datetime(2026, 10, 7, 9, 0)),  # 이미 지난 시각은 내일
        ({"time": "09:00", "day_offset": 2, "message": "회의"}, datetime(2026, 10, 8, 9, 0)),
        ({"minutes_from_now": "25", "message": "쉬기"}, NOW + timedelta(minutes=25)),  # 숫자를 글자로 줘도 받는다
        # 정해진 틀로 답하게 하면 안 쓰는 칸을 빈 값으로 채워 오곤 한다.
        ({"minutes_from_now": 30, "time": "", "day_offset": 0, "message": "스트레칭"}, NOW + timedelta(minutes=30)),
        ({"minutes_from_now": 0, "time": "18:30", "message": "저녁"}, datetime(2026, 10, 6, 18, 30)),
    ],
)
def test_alarm_tool_time_forms(toolbox, stores, arguments, expected):
    toolbox.execute("add_alarm", arguments, NOW)

    assert [alarm.due_at for alarm in stores[0].list_pending_alarms()] == [expected]


@pytest.mark.parametrize(
    "arguments",
    [{"message": "언제?"}, {"minutes_from_now": "십분", "message": "x"}, {"time": "25:99", "message": "x"}, {"minutes_from_now": -5}],
)
def test_alarm_tool_rejects_unusable_times_without_crashing(toolbox, stores, arguments):
    outcome = toolbox.execute("add_alarm", arguments, NOW)

    assert not outcome.ok and not outcome.shown
    assert stores[0].list_pending_alarms() == []


def test_todo_tools(toolbox):
    assert toolbox.execute("add_todo", {"text": "우유 사기"}, NOW) == Outcome("할 일에 '우유 사기' 적어 뒀어요.")
    toolbox.execute("add_todo", {"text": "메일 답장"}, NOW)

    assert toolbox.execute("list_todos", {}, NOW).text == "남은 할 일이에요. 1. 우유 사기, 2. 메일 답장"
    assert toolbox.execute("complete_todo", {"number": 1}, NOW) == Outcome("'우유 사기' 끝낸 걸로 표시했어요.")
    assert toolbox.execute("list_todos", {}, NOW).text == "남은 할 일이에요. 1. 메일 답장"
    assert not toolbox.execute("complete_todo", {"number": 7}, NOW).ok


def test_list_tools_say_so_when_empty(toolbox):
    assert toolbox.execute("list_todos", {}, NOW) == Outcome("남은 할 일이 없어요.")
    assert toolbox.execute("list_alarms", {}, NOW) == Outcome("울릴 알람이 없어요.")


def test_list_alarms_tool(toolbox):
    toolbox.execute("add_alarm", {"minutes_from_now": 30, "message": "스트레칭"}, NOW)
    toolbox.execute("add_alarm", {"time": "18:30", "message": "저녁 약속"}, NOW)

    assert toolbox.execute("list_alarms", {}, NOW).text == "울릴 알람이에요. 1. 오후 3:30 스트레칭, 2. 오후 6:30 저녁 약속"


def test_alarm_tool_takes_a_calendar_date(toolbox, stores):
    outcome = toolbox.execute("add_alarm", {"date": "2026-10-12", "time": "15:00", "message": "병원"}, NOW)

    assert outcome == Outcome("10월 12일 오후 3:00에 '병원' 알려 줄게요.")
    assert [alarm.due_at for alarm in stores[0].list_pending_alarms()] == [datetime(2026, 10, 12, 15, 0)]


@pytest.mark.parametrize(
    "arguments",
    [
        {"date": "2026-10-01", "time": "15:00", "message": "지난 날"},
        {"date": "10월 12일", "time": "15:00", "message": "틀린 형식"},
        {"date": "2026-10-12", "message": "시각 없음"},
    ],
)
def test_alarm_tool_rejects_unusable_dates(toolbox, stores, arguments):
    assert not toolbox.execute("add_alarm", arguments, NOW).ok
    assert stores[0].list_pending_alarms() == []


def test_daily_alarm_says_so_and_is_listed_as_daily(toolbox, stores):
    outcome = toolbox.execute("add_alarm", {"time": "09:00", "repeat": "daily", "message": "약 먹기"}, NOW)

    assert outcome == Outcome("매일 오전 9:00에 '약 먹기' 알려 줄게요.")
    assert stores[0].list_pending_alarms()[0].repeat == "daily"
    assert toolbox.execute("list_alarms", {}, NOW).text == "울릴 알람이에요. 1. 매일 오전 9:00 약 먹기"


def test_daily_alarm_ignores_a_date_and_starts_at_the_next_such_time(toolbox, stores):
    """'매일 아침 9시'에 AI가 오늘 날짜를 붙여 와도, 지난 때라고 거절하지 않고 내일 9시부터 울린다."""
    toolbox.execute("add_alarm", {"time": "09:00", "date": "2026-10-06", "repeat": "daily", "message": "약"}, NOW)

    assert [alarm.due_at for alarm in stores[0].list_pending_alarms()] == [datetime(2026, 10, 7, 9, 0)]


def test_unknown_repeat_is_rejected(toolbox):
    assert not toolbox.execute("add_alarm", {"time": "09:00", "repeat": "hourly", "message": "x"}, NOW).ok


def test_cancel_alarm_tool_removes_the_numbered_alarm(toolbox, stores):
    toolbox.execute("add_alarm", {"minutes_from_now": 30, "message": "스트레칭"}, NOW)
    toolbox.execute("add_alarm", {"time": "18:30", "message": "저녁 약속"}, NOW)

    outcome = toolbox.execute("cancel_alarm", {"number": 1}, NOW)

    assert outcome == Outcome("오후 3:30 '스트레칭' 알람을 취소했어요.")
    assert [alarm.message for alarm in stores[0].list_pending_alarms()] == ["저녁 약속"]
    assert not toolbox.execute("cancel_alarm", {"number": 5}, NOW).ok


def test_review_day_gives_the_ai_a_timeline_of_what_was_on_screen(toolbox, stores):
    memory = stores[1]
    yesterday = NOW - timedelta(days=1)
    for minute in range(0, 90, 10):  # 어제 오후 3시부터 10분마다
        memory.add_screen_note(f"코드를 고치는 중 {minute}", "Code.exe", yesterday + timedelta(minutes=minute))
    memory.add_screen_note("오늘 본 화면", "chrome.exe", NOW)

    outcome = toolbox.execute("review_day", {"day_offset": -1}, NOW)

    assert outcome.ok and not outcome.shown  # AI가 읽고 답하게 한다
    # 30분에 하나씩만 추려서 보여 준다.
    assert outcome.text.count("\n- ") == 3
    assert "오후 3:00 코드를 고치는 중 0" in outcome.text and "오후 4:00 코드를 고치는 중 60" in outcome.text
    assert "오늘 본 화면" not in outcome.text


def test_review_day_says_so_when_nothing_was_recorded_or_the_day_is_out_of_range(toolbox):
    assert "기록이 없" in toolbox.execute("review_day", {"day_offset": -1}, NOW).text
    assert not toolbox.execute("review_day", {"day_offset": 3}, NOW).ok
    assert not toolbox.execute("review_day", {"day_offset": -30}, NOW).ok


def test_control_pc_tool_hands_the_goal_to_the_pc_operator(stores):
    goals = []
    toolbox = ToolBox(*stores, start_control=lambda goal: goals.append(goal) or True)

    outcome = toolbox.execute("control_pc", {"goal": "메모장 열어서 안녕이라고 써 줘"}, NOW)

    assert goals == ["메모장 열어서 안녕이라고 써 줘"]
    assert outcome.ok and outcome.shown and "Esc" in outcome.text


def test_control_pc_tool_says_so_when_busy_or_unavailable(stores):
    busy = ToolBox(*stores, start_control=lambda goal: False)
    assert "이미" in busy.execute("control_pc", {"goal": "계산기 열어 줘"}, NOW).text

    assert not ToolBox(*stores).execute("control_pc", {"goal": "계산기 열어 줘"}, NOW).ok


def test_web_search_tool_hands_results_to_the_ai_quietly(stores):
    asked = []

    def search(query):
        asked.append(query)
        return [Hit("기상청 날씨누리", "https://www.weather.go.kr/w/index.do", "서울 오늘 맑음, 낮 최고 21도")]

    outcome = ToolBox(*stores, search=search).execute("web_search", {"query": " 서울 오늘 날씨 "}, NOW)

    assert asked == ["서울 오늘 날씨"]
    # 검색 결과는 사용자에게 그대로 띄우지 않고, AI가 읽고 답하게 한다.
    assert outcome.ok and not outcome.shown
    assert "낮 최고 21도" in outcome.text and "www.weather.go.kr" in outcome.text
    assert "/w/index.do" not in outcome.text  # 긴 주소는 목소리로 읽기 나쁘니 사이트 이름만 적는다


def test_web_search_tool_says_so_when_it_cannot_search(stores):
    def refused(query):
        raise SearchError("검색 서비스가 지금은 검색을 받아 주지 않습니다")

    outcome = ToolBox(*stores, search=refused).execute("web_search", {"query": "날씨"}, NOW)

    assert not outcome.ok and "받아 주지" in outcome.text
    assert "결과가 없" in ToolBox(*stores, search=lambda query: []).execute("web_search", {"query": "날씨"}, NOW).text
    assert not ToolBox(*stores).execute("web_search", {"query": "날씨"}, NOW).ok


LISTING = CommandResult("메모.txt", 0)


def shell_toolbox(stores, *, answers=(), result=LISTING):
    ran, asked = [], []
    replies = iter(answers)
    toolbox = ToolBox(
        *stores,
        shell=lambda command: ran.append(command) or result,
        confirm=lambda text: asked.append(text) or next(replies),
    )
    return toolbox, ran, asked


def test_run_command_tool_runs_harmless_commands_without_asking(stores):
    toolbox, ran, asked = shell_toolbox(stores)

    outcome = toolbox.execute("run_command", {"command": "Get-ChildItem $Desktop"}, NOW)

    assert ran == ["Get-ChildItem $Desktop"] and asked == []
    # 출력은 사용자에게 그대로 띄우지 않고, AI가 읽고 답하게 한다.
    assert outcome.ok and not outcome.shown and "메모.txt" in outcome.text


def test_run_command_tool_asks_before_a_risky_looking_command(stores):
    toolbox, ran, asked = shell_toolbox(stores, answers=[False, True])

    refused = toolbox.execute("run_command", {"command": "Remove-Item a.txt"}, NOW)

    assert ran == [] and refused.shown and "실행하지 않" in refused.text
    assert "휴지통" in asked[0] and "Remove-Item a.txt" in asked[0]  # 까닭과 명령을 그대로 보여 준다

    toolbox.execute("run_command", {"command": "Remove-Item a.txt"}, NOW)

    assert ran == ["Remove-Item a.txt"]


def test_run_command_tool_never_runs_a_risky_command_it_cannot_ask_about(stores):
    ran = []
    toolbox = ToolBox(*stores, shell=lambda command: ran.append(command) or CommandResult("", 0))

    toolbox.execute("run_command", {"command": "Remove-Item a.txt"}, NOW)

    assert ran == []


def test_run_command_tool_reports_failures(stores):
    failed, _, _ = shell_toolbox(stores, result=CommandResult("오류: 경로를 찾을 수 없습니다.", 1))
    cut_off, _, _ = shell_toolbox(stores, result=CommandResult("", None))

    outcome = failed.execute("run_command", {"command": "Get-ChildItem C:\\none"}, NOW)

    assert not outcome.ok and "경로를 찾을 수 없습니다" in outcome.text
    assert not cut_off.execute("run_command", {"command": "Get-ChildItem"}, NOW).ok
    assert not ToolBox(*stores).execute("run_command", {"command": "Get-ChildItem"}, NOW).ok


def test_state_summary_numbers_todos_and_alarms_for_the_planner(toolbox):
    assert toolbox.state_summary(NOW) == "[지금 남은 할 일]\n없음\n\n[지금 울릴 알람]\n없음"

    toolbox.execute("add_todo", {"text": "책 반납"}, NOW)
    toolbox.execute("add_alarm", {"minutes_from_now": 30, "message": "스트레칭"}, NOW)

    assert toolbox.state_summary(NOW) == "[지금 남은 할 일]\n1. 책 반납\n\n[지금 울릴 알람]\n1. 오후 3:30 스트레칭"


def test_remembering_what_is_already_known_is_not_news(toolbox):
    toolbox.execute("remember", {"fact": "이름은 상민"}, NOW)

    assert toolbox.execute("remember", {"fact": "이름은 상민"}, NOW) == Outcome("이미 기억하고 있음: 이름은 상민", shown=False)


def test_remember_tool_saves_a_fact_quietly(toolbox, stores):
    outcome = toolbox.execute("remember", {"fact": "이름은 상민"}, NOW)

    assert [fact.text for fact in stores[1].list_facts()] == ["이름은 상민"]
    # 기억했다는 말은 따로 띄우지 않고, AI가 대화 속에서 자연스럽게 받는다.
    assert outcome.ok and not outcome.shown


def test_unknown_tool_is_reported_not_raised(toolbox):
    assert not toolbox.execute("format_disk", {}, NOW).ok


def test_every_schema_has_a_handler_and_is_offered_to_the_planner(toolbox):
    names = [schema["function"]["name"] for schema in toolbox.schemas]

    assert ACTION_FORMAT["properties"]["actions"]["items"]["properties"]["tool"]["enum"] == names
    for name in names:
        assert "모르는 작업" not in toolbox.execute(name, {}, NOW).text, name


# ---------- 대화 한 차례 ----------


class FakeEngine:
    """작업 고르기(plans)와 답하기(replies)를 순서대로 흉내 낸다."""

    def __init__(self, *, plans=(), replies=("네!",)):
        self.plans = list(plans)
        self.replies = list(replies)
        self.plan_calls: list[list[dict]] = []
        self.chat_calls: list[list[dict]] = []

    def complete_json(self, messages, schema):
        self.plan_calls.append([dict(message) for message in messages])
        plan = self.plans.pop(0) if self.plans else {"actions": []}
        if isinstance(plan, Exception):
            raise plan
        return plan

    def chat(self, messages, *, on_delta=None, should_stop=None):
        self.chat_calls.append([dict(message) for message in messages])
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if on_delta and reply:
            on_delta(reply)
        return reply


def session_with(engine, stores, **options) -> ChatSession:
    tasks, memory = stores
    return ChatSession(engine, memory, ToolBox(tasks, memory), character_name="모모", clock=lambda: NOW, **options)


def plan(*actions: dict) -> dict:
    return {"actions": list(actions)}


def test_reply_is_returned_streamed_and_remembered(stores):
    engine = FakeEngine(replies=["안녕하세요!"])
    pieces = []

    text = session_with(engine, stores).reply("안녕", on_delta=pieces.append)

    assert text == "안녕하세요!"
    assert pieces == ["안녕하세요!"]
    assert [(m.role, m.content) for m in stores[1].recent_messages(10)] == [("user", "안녕"), ("assistant", "안녕하세요!")]
    sent = engine.chat_calls[0]
    assert sent[0]["role"] == "system" and "모모" in sent[0]["content"]
    assert sent[-1] == {"role": "user", "content": "안녕"}


def test_search_results_reach_the_answering_ai(stores):
    tasks, memory = stores
    engine = FakeEngine(plans=[plan({"tool": "web_search", "query": "서울 날씨"})], replies=["오늘 서울은 맑대요."])
    toolbox = ToolBox(tasks, memory, search=lambda query: [Hit("기상청", "https://www.weather.go.kr/", "서울 맑음")])
    session = ChatSession(engine, memory, toolbox, character_name="모모", clock=lambda: NOW)

    assert session.reply("오늘 서울 날씨 어때?") == "오늘 서울은 맑대요."
    assert "서울 맑음" in engine.chat_calls[0][0]["content"]


def test_earlier_turns_are_sent_as_history(stores):
    engine = FakeEngine(replies=["상민 님이죠!", "네!"])
    session = session_with(engine, stores)

    session.reply("내 이름은 상민이야")
    session.reply("내 이름 기억해?")

    assert [(m["role"], m["content"]) for m in engine.chat_calls[1][1:]] == [
        ("user", "내 이름은 상민이야"),
        ("assistant", "상민 님이죠!"),
        ("user", "내 이름 기억해?"),
    ]


def test_related_old_talk_is_recalled_beyond_the_history_window(stores):
    memory = stores[1]
    memory.add_message("user", "주말에 제주도 여행 가기로 했어", NOW - timedelta(days=30))
    memory.add_message("assistant", "제주도 좋죠!", NOW - timedelta(days=30))
    for index in range(4):
        memory.add_message("user" if index % 2 == 0 else "assistant", f"잡담 {index}", NOW - timedelta(days=1))
    engine = FakeEngine(replies=["제주도였죠."])

    session_with(engine, stores, history_limit=4).reply("여행 어디로 가기로 했더라")

    system = engine.chat_calls[0][0]["content"]
    assert "주말에 제주도 여행 가기로 했어" in system
    assert "9월 6일" in system
    assert all("제주도 여행 가기로" not in m["content"] for m in engine.chat_calls[0][1:])


def test_confirmation_of_an_action_comes_from_the_program_not_the_ai(stores):
    """작은 모델은 시각을 다시 말하다가 틀리곤 한다. 확인 문장은 실제 값으로 직접 만든다."""
    engine = FakeEngine(
        plans=[plan({"tool": "add_alarm", "minutes_from_now": 10, "message": "빨래 꺼내기"})],
        replies=["10분 뒤인 오후 3시 11분에 알려 드릴게요."],  # 이런 답은 아예 받지 않는다
    )
    used, pieces = [], []

    text = session_with(engine, stores).reply("10분 뒤에 빨래 꺼내라고 해 줘", on_tool=used.append, on_delta=pieces.append)

    assert text == "오후 3:10에 '빨래 꺼내기' 알려 줄게요."
    assert pieces == [text]
    assert engine.chat_calls == []
    assert used == ["add_alarm"]
    assert [alarm.message for alarm in stores[0].list_pending_alarms()] == ["빨래 꺼내기"]
    # 작업이 오간 과정은 기억에 남기지 않고, 사람이 본 말만 남긴다.
    assert [(m.role, m.content) for m in stores[1].recent_messages(10)] == [
        ("user", "10분 뒤에 빨래 꺼내라고 해 줘"),
        ("assistant", text),
    ]


@pytest.mark.parametrize(
    ("said", "action", "expected"),
    [
        # 시각을 말했는데 AI가 '몇 분 뒤'까지 제멋대로 계산해 붙인 경우: 말한 시각을 따른다.
        ("7시에 깨워 줘", {"minutes_from_now": 261, "time": "19:00"}, datetime(2026, 10, 6, 19, 0)),
        ("오후 6시 반에 저녁 약속 있어", {"minutes_from_now": 90, "time": "18:30"}, datetime(2026, 10, 6, 18, 30)),
        # '몇 분 뒤'라고 말했는데 AI가 시각까지 계산해 붙인 경우: 말한 분을 따른다.
        ("30분 있다가 스트레칭", {"minutes_from_now": 30, "time": "15:45"}, NOW + timedelta(minutes=30)),
        ("두 시간 뒤에 약 먹기", {"minutes_from_now": 120, "time": "16:00"}, NOW + timedelta(minutes=120)),
    ],
)
def test_when_ai_fills_both_time_forms_the_one_the_user_said_wins(stores, said, action, expected):
    engine = FakeEngine(plans=[plan({"tool": "add_alarm", "message": "알람", **action})])

    session_with(engine, stores).reply(said)

    assert [alarm.due_at for alarm in stores[0].list_pending_alarms()] == [expected]


@pytest.mark.parametrize(
    ("said", "action", "expected"),
    [
        # AI가 '오후 6시 반'을 '90분 뒤'로 잘못 계산해 왔다.
        ("오후 6시 반에 저녁 약속 있어", {"minutes_from_now": 90}, datetime(2026, 10, 6, 18, 30)),
        # AI가 달력에서 날짜를 잘못 골랐다.
        ("모레 오전 10시 반에 병원", {"time": "10:30", "date": "2026-10-09"}, datetime(2026, 10, 8, 10, 30)),
        ("내일 아침 9시에 회의", {"time": "09:00"}, datetime(2026, 10, 7, 9, 0)),
        # AI가 오후를 빠뜨렸다.
        ("저녁 8시에 운동 가라고 알려 줘", {"time": "08:00"}, datetime(2026, 10, 6, 20, 0)),
        ("새벽 2시에 깨워 줘", {"time": "14:00"}, datetime(2026, 10, 7, 2, 0)),
        # 오전·오후를 말하지 않았으면 AI의 판단을 그대로 둔다.
        ("7시에 깨워 줘", {"time": "07:00", "day_offset": 1}, datetime(2026, 10, 7, 7, 0)),
        # '매일'에는 날을 가리키는 말을 적용하지 않는다.
        ("오늘부터 매일 아침 9시에 약", {"time": "09:00", "repeat": "daily"}, datetime(2026, 10, 7, 9, 0)),
    ],
)
def test_time_and_day_the_user_said_override_what_the_ai_worked_out(stores, said, action, expected):
    engine = FakeEngine(plans=[plan({"tool": "add_alarm", "message": "알람", **action})])

    session_with(engine, stores).reply(said)

    assert [alarm.due_at for alarm in stores[0].list_pending_alarms()] == [expected]


def test_answer_knows_what_was_recently_on_screen(stores):
    stores[1].add_screen_note("VS Code에서 파이썬 코드를 고치는 중", "Code.exe", NOW - timedelta(minutes=2))
    engine = FakeEngine(replies=["코드 고치고 계셨죠."])

    session_with(engine, stores).reply("나 지금 뭐 하고 있었지?")

    system = engine.chat_calls[0][0]["content"]
    assert "오후 2:58 VS Code에서 파이썬 코드를 고치는 중" in system


def test_answer_is_given_extra_context_such_as_closeness_and_mood(stores):
    engine = FakeEngine(replies=["졸려요…"])

    session_with(engine, stores, extra_context=lambda: "[지금 기분] 졸림").reply("안녕")

    assert "[지금 기분] 졸림" in engine.chat_calls[0][0]["content"]


def test_alarm_is_not_cancelled_unless_the_user_said_to_cancel(stores):
    """'1번 다 했어'에 AI가 할 일 1번을 끝내면서 알람 1번까지 지우려 한 적이 있다."""
    stores[0].add_todo("책 반납", NOW)
    stores[0].add_alarm("약 먹기", NOW + timedelta(hours=2))
    both = plan({"tool": "cancel_alarm", "number": 1}, {"tool": "complete_todo", "number": 1})

    text = session_with(FakeEngine(plans=[both]), stores).reply("1번 다 했어")

    assert text == "'책 반납' 끝낸 걸로 표시했어요."
    assert [alarm.message for alarm in stores[0].list_pending_alarms()] == ["약 먹기"]


@pytest.mark.parametrize("said", ["그 알람 취소해 줘", "약 먹기 알람 지워 줘", "1번 알람 삭제", "알람 꺼 줘", "그 알람 없애 줘"])
def test_alarm_is_cancelled_when_asked(stores, said):
    stores[0].add_alarm("약 먹기", NOW + timedelta(hours=2))

    session_with(FakeEngine(plans=[plan({"tool": "cancel_alarm", "number": 1})]), stores).reply(said)

    assert stores[0].list_pending_alarms() == []


def test_past_days_are_summarised_once_and_the_summary_is_given_to_later_answers(stores):
    memory = stores[1]
    yesterday = NOW - timedelta(days=1)
    memory.add_message("user", "다음 달에 제주도 여행 가기로 했어", yesterday)
    memory.add_message("assistant", "좋겠어요!", yesterday)
    engine = FakeEngine(replies=["사용자는 다음 달 제주도 여행을 계획했다.", "제주도 가신다고 했죠."])
    session = session_with(engine, stores, history_limit=0)

    assert session.digest() == 1
    assert session.digest() == 0  # 이미 요약한 날은 다시 하지 않는다
    asked = engine.chat_calls[0][-1]["content"]
    assert "사용자: 다음 달에 제주도 여행 가기로 했어" in asked and "10월 5일" in asked

    session.reply("나 어디 가기로 했었지?")

    assert "10월 5일: 사용자는 다음 달 제주도 여행을 계획했다." in engine.chat_calls[1][0]["content"]


def test_a_day_that_cannot_be_summarised_is_left_for_next_time(stores):
    stores[1].add_message("user", "어제 한 말", NOW - timedelta(days=1))
    session = session_with(FakeEngine(replies=[EngineError("Ollama에 연결할 수 없습니다."), "어제는 인사를 나눴다."]), stores)

    with pytest.raises(EngineError):
        session.digest()

    assert session.digest() == 1


def test_several_confirmations_are_joined(stores):
    actions = plan({"tool": "add_todo", "text": "장보기"}, {"tool": "add_todo", "text": "세탁소 가기"})

    text = session_with(FakeEngine(plans=[actions]), stores).reply("할 일 두 개 적어 줘. 장보기랑 세탁소 가기")

    assert text == "할 일에 '장보기' 적어 뒀어요. 할 일에 '세탁소 가기' 적어 뒀어요."


def test_planner_is_shown_current_todos_and_alarms(stores):
    stores[0].add_todo("책 반납", NOW)
    engine = FakeEngine()

    session_with(engine, stores).reply("책 반납 다 했어")

    assert "[지금 남은 할 일]\n1. 책 반납" in engine.plan_calls[0][0]["content"]


def test_failed_action_is_left_for_the_ai_to_explain(stores):
    engine = FakeEngine(plans=[plan({"tool": "complete_todo", "number": 3})], replies=["3번 할 일은 없어요."])
    pieces = []

    text = session_with(engine, stores).reply("3번 다 했어", on_delta=pieces.append)

    assert text == "3번 할 일은 없어요."
    assert pieces == ["3번 할 일은 없어요."]
    assert "3번 할 일이 없습니다" in engine.chat_calls[0][0]["content"]


def test_answer_is_told_plainly_when_nothing_was_done(stores):
    engine = FakeEngine(replies=["저도 고양이 좋아해요."])

    session_with(engine, stores).reply("고양이 귀엽지")

    system = engine.chat_calls[0][0]["content"]
    assert "작업을 하지 않았다" in system


def test_fact_remembered_in_this_turn_is_told_to_the_answer_as_news(stores):
    """이미 알던 사실처럼 보여 주면 AI가 '이미 알고 있었어요'라고 답한다. 방금 알게 된 것으로 알려 준다."""
    stores[1].add_fact("떡볶이를 좋아함", NOW)
    engine = FakeEngine(plans=[plan({"tool": "remember", "fact": "이름은 상민"})], replies=["반가워요, 상민 님!"])

    text = session_with(engine, stores).reply("내 이름은 상민이야")

    assert text == "반가워요, 상민 님!"
    system = engine.chat_calls[0][0]["content"]
    assert "- 떡볶이를 좋아함" in system
    assert "- 이름은 상민" not in system
    assert "방금 알려 줘서 새로 기억해 둔 사실: 이름은 상민" in system


def test_planner_sees_what_was_just_said_and_the_talk_before_it(stores):
    engine = FakeEngine(replies=["1. 우유 사기", "잘했어요!"])
    session = session_with(engine, stores)
    session.reply("할 일 뭐 있어?")

    session.reply("1번 다 했어")

    planner_messages = engine.plan_calls[1]
    assert planner_messages[-1] == {"role": "user", "content": "1번 다 했어"}
    assert "사용자: 할 일 뭐 있어?" in planner_messages[0]["content"]
    assert "캐릭터: 1. 우유 사기" in planner_messages[0]["content"]


@pytest.mark.parametrize(
    "bad_plan",
    [{}, {"actions": "add_todo"}, {"actions": ["add_todo"]}, {"actions": [{"text": "도구 이름 없음"}]}, []],
)
def test_malformed_plan_is_treated_as_nothing_to_do(stores, bad_plan):
    engine = FakeEngine(plans=[bad_plan], replies=["네."])

    assert session_with(engine, stores).reply("음") == "네."
    assert stores[0].list_todos() == []


def test_runaway_plan_is_capped(stores):
    many = plan(*[{"tool": "add_todo", "text": f"할 일 {number}"} for number in range(30)])
    engine = FakeEngine(plans=[many])

    session_with(engine, stores).reply("전부 적어 줘")

    assert len(stores[0].list_todos()) == MAX_ACTIONS


def test_empty_answer_becomes_fallback(stores):
    assert session_with(FakeEngine(replies=["  "]), stores).reply("음") == FALLBACK_REPLY


@pytest.mark.parametrize("failing", ["plan", "reply"])
def test_engine_failure_keeps_what_the_user_said(stores, failing):
    error = EngineError("Ollama에 연결할 수 없습니다")
    engine = FakeEngine(plans=[error]) if failing == "plan" else FakeEngine(replies=[error])

    with pytest.raises(EngineError):
        session_with(engine, stores).reply("안녕")

    assert [(m.role, m.content) for m in stores[1].recent_messages(10)] == [("user", "안녕")]
