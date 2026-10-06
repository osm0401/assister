import threading

import pytest

from assister.ai.engine import EngineError
from assister.control import executor
from assister.control.actions import (
    ACTION_FORMAT,
    Action,
    ActionError,
    describe,
    monitor_in_use,
    parse_action,
    risk_reason,
    to_screen,
)
from assister.control.agent import ControlAgent
from assister.control.executor import normalized, virtual_key
from assister.screen.capture import Snapshot
from assister.screen.privacy import Window

MONITORS = [(0, 0, 1920, 1200), (-1920, 491, 0, 1571)]
NOTEPAD = Window("제목 없음 - 메모장", "notepad.exe", (0, 0, 800, 600))
KAKAO = Window("민지", "KakaoTalk.exe", (0, 0, 400, 600))

# ---------- 동작 읽기 ----------


def test_click_is_read_with_its_target():
    action = parse_action({"action": "click", "why": "저장 버튼을 누를게요.", "monitor": 1, "x": 500, "y": 250, "target": "저장"})

    assert action == Action("click", "저장 버튼을 누를게요.", monitor=1, x=500, y=250, target="저장")


def test_unused_fields_filled_with_blanks_are_ignored():
    raw = {"action": "type", "why": "글을 쓸게요.", "text": "안녕", "monitor": 0, "x": 0, "y": 0, "keys": "", "risky": False}

    assert parse_action(raw) == Action("type", "글을 쓸게요.", text="안녕")


@pytest.mark.parametrize(
    "raw",
    [
        "click",
        {"action": "fly", "why": "x"},
        {"action": "click", "why": "x", "x": 1500, "y": 10},  # 화면 밖
        {"action": "click", "why": "x"},  # 좌표 없음
        {"action": "type", "why": "x", "text": ""},
        {"action": "key", "why": "x", "keys": "ctrl+nonsense"},
        {"action": "key", "why": "x", "keys": "shift+delete"},  # 휴지통을 거치지 않는 삭제는 하지 않는다
        {"action": "key", "why": "x", "keys": "esc"},  # Esc는 사용자가 멈출 때 쓰는 키다
        {"action": "open", "why": "x", "text": " "},
    ],
)
def test_unusable_actions_are_rejected(raw):
    with pytest.raises(ActionError):
        parse_action(raw)


def test_opening_a_program_brings_its_new_window_forward(monkeypatch):
    # 이 앱이 맨 앞이 아닐 때 연 프로그램은 뒤에 떠서, 이어지는 글자 입력이 쓰던 창으로 들어갔다.
    looks = iter([[1, 2], [1, 2], [7, 1, 2]])
    done = []
    monkeypatch.setattr(executor, "_titled_windows", lambda: next(looks))
    monkeypatch.setattr(executor.os, "startfile", done.append)
    monkeypatch.setattr(executor, "_bring_forward", done.append)
    monkeypatch.setattr(executor.time, "sleep", lambda _seconds: None)

    executor.Win32Executor().perform(Action("open", "x", text="calc"), None)

    assert done == ["calc", 7]


def test_open_takes_the_program_name_from_target_when_text_is_empty():
    # 실제 모델이 프로그램 이름을 target 칸에 적고 text를 비운 적이 있다.
    action = parse_action({"action": "open", "why": "x", "target": " calc ", "text": ""})

    assert (action.kind, action.text) == ("open", "calc")


def test_ai_must_fill_every_field_so_clicks_always_come_with_coordinates():
    assert set(ACTION_FORMAT["required"]) == set(ACTION_FORMAT["properties"])
    assert {"x", "y", "monitor", "target"} <= set(ACTION_FORMAT["required"])


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (Action("click", "x", target="저장"), "'저장'을(를) 눌렀다"),
        (Action("click", "x", monitor=1, x=500, y=250), "1번 모니터의 (500, 250)을 눌렀다"),
        (Action("double_click", "x", target="보고서.docx"), "'보고서.docx'을(를) 두 번 눌렀다"),
        (Action("type", "x", text="안녕"), "'안녕'이라고 입력했다"),
        (Action("key", "x", keys="ctrl+s"), "ctrl+s 키를 눌렀다"),
        (Action("scroll", "x", amount=-3), "화면을 아래로 내렸다"),
        (Action("open", "x", text="notepad"), "notepad을(를) 열었다"),
        (Action("wait", "x"), "잠깐 기다렸다"),
    ],
)
def test_done_actions_are_described_as_plain_facts(action, expected):
    assert describe(action) == expected


def test_key_names_are_normalised():
    assert parse_action({"action": "key", "why": "저장", "keys": " Ctrl + S "}).keys == "ctrl+s"


@pytest.mark.parametrize(
    ("monitor", "x", "y", "expected"),
    [(0, 500, 500, (960, 600)), (0, 0, 0, (0, 0)), (1, 1000, 1000, (-1, 1570)), (1, 500, 0, (-960, 491))],
)
def test_per_mille_point_maps_to_the_right_monitor(monitor, x, y, expected):
    assert to_screen(MONITORS, monitor, x, y) == expected


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        (Window("a", "a.exe", (100, 100, 900, 700)), 0),
        (Window("b", "b.exe", (-1500, 600, -700, 1200)), 1),
        (Window("걸친 창", "c.exe", (-300, 500, 500, 900)), 0),  # 가운데가 있는 쪽
        (None, 0),
    ],
)
def test_monitor_in_use_is_where_the_front_window_sits(window, expected):
    assert monitor_in_use(window, MONITORS) == expected


def test_unknown_monitor_is_rejected():
    with pytest.raises(ActionError):
        to_screen(MONITORS, 5, 10, 10)


def test_screen_point_is_scaled_for_windows_input_across_all_monitors():
    virtual_screen = (-1920, 0, 3840, 1571)  # 왼쪽, 위, 너비, 높이

    assert normalized((-1920, 0), virtual_screen) == (0, 0)
    assert normalized((1919, 1570), virtual_screen) == (65535, 65535)
    assert normalized((0, 0), virtual_screen)[0] == round(1920 * 65535 / 3839)


def test_key_names_map_to_windows_key_codes():
    assert [virtual_key(name) for name in ("ctrl", "s", "enter", "f5", "7")] == [0x11, ord("S"), 0x0D, 0x74, ord("7")]
    with pytest.raises(ActionError):
        virtual_key("nonsense")


# ---------- 위험한 동작 가려내기 ----------


def click(target="", risky=False) -> Action:
    return Action("click", "누를게요.", monitor=0, x=10, y=10, target=target, risky=risky)


@pytest.mark.parametrize(
    ("action", "element", "front"),
    [
        (click(), "삭제", NOTEPAD),  # Windows가 알려 준 버튼 이름
        (click(target="보내기 버튼"), "", NOTEPAD),  # AI가 말한 버튼 이름
        (click(), "Install now", NOTEPAD),
        (click(), "결제하기", NOTEPAD),
        (click(), "저장 안 함", NOTEPAD),
        (click(risky=True), "확인", NOTEPAD),  # AI의 판단
        (Action("key", "지울게요.", keys="delete"), "", NOTEPAD),
        (Action("key", "닫을게요.", keys="alt+f4"), "", NOTEPAD),
        (Action("key", "보낼게요.", keys="enter"), "", KAKAO),  # 메신저에서 Enter는 전송
        (Action("type", "답장할게요.", text="안녕\n"), "", KAKAO),
        (Action("open", "실행할게요.", text="C:\\Downloads\\setup.exe"), "", NOTEPAD),
    ],
)
def test_risky_actions_need_confirmation(action, element, front):
    assert risk_reason(action, element_name=element, foreground=front) is not None


TERMINAL = Window("명령 프롬프트", "WindowsTerminal.exe", (0, 0, 800, 600))
RUN_BOX = Window("실행", "explorer.exe", (0, 0, 400, 200))


@pytest.mark.parametrize(
    ("action", "front"),
    [
        # 명령 창에 글자를 넣고 Enter를 치면 무엇이든 실행된다.
        (Action("type", "명령을 칠게요.", text="del /q *.*"), TERMINAL),
        (Action("key", "실행할게요.", keys="enter"), TERMINAL),
        (Action("type", "칠게요.", text="notepad"), Window("Windows PowerShell", "powershell.exe", (0, 0, 1, 1))),
        (Action("type", "칠게요.", text="cmd"), RUN_BOX),
        (Action("key", "실행할게요.", keys="enter"), RUN_BOX),
        (Action("key", "실행 창을 열게요.", keys="win+r"), NOTEPAD),
        (Action("open", "명령 창을 열게요.", text="cmd"), NOTEPAD),
        (Action("open", "열게요.", text="PowerShell.exe"), NOTEPAD),
        (Action("open", "열게요.", text="regedit"), NOTEPAD),
        (Action("open", "열게요.", text="wt"), NOTEPAD),
    ],
)
def test_anything_that_can_run_commands_needs_confirmation(action, front):
    assert risk_reason(action, element_name="", foreground=front) is not None


@pytest.mark.parametrize(
    ("action", "element", "front"),
    [
        (click(target="파일 메뉴"), "파일", NOTEPAD),
        (Action("type", "글을 쓸게요.", text="안녕하세요"), "", NOTEPAD),
        (Action("key", "줄을 바꿀게요.", keys="enter"), "", NOTEPAD),
        (Action("key", "저장할게요.", keys="ctrl+s"), "", NOTEPAD),
        (Action("open", "메모장을 열게요.", text="notepad"), "", None),
        (Action("open", "네이버를 열게요.", text="https://naver.com"), "", None),
        (Action("scroll", "내려 볼게요.", monitor=0, x=500, y=500, amount=-3), "", NOTEPAD),
    ],
)
def test_ordinary_actions_run_without_asking(action, element, front):
    assert risk_reason(action, element_name=element, foreground=front) is None


# ---------- 조작 과정 ----------


class FakeEngine:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.images: list[list[str]] = []

    def complete_json(self, messages, schema):
        self.prompts.append(messages[-1]["content"])
        self.images.append(messages[-1]["images"])
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


class FakeExecutor:
    def __init__(self, element="", on_perform=None):
        self.done: list[Action] = []
        self.points: list[tuple[int, int] | None] = []
        self.element = element
        self._on_perform = on_perform

    def monitors(self):
        return MONITORS

    def element_name(self, x, y):
        return self.element

    def perform(self, action, point):
        self.done.append(action)
        self.points.append(point)
        if self._on_perform:
            self._on_perform()


def screen(*, skipped=False, front=NOTEPAD) -> Snapshot:
    return Snapshot(images=[] if skipped else ["aW1n"], foreground=front, hidden=[], skipped=skipped)


CLICK = {"action": "click", "why": "파일 메뉴를 누를게요.", "monitor": 0, "x": 20, "y": 30, "target": "파일"}
TYPE = {"action": "type", "why": "인사를 쓸게요.", "text": "안녕"}
DONE = {"action": "done", "why": "다 썼어요."}


@pytest.fixture
def run_agent(qapp, wait_until):
    agents = []

    def run(engine, executor=None, *, snapshot=None, confirm=None, max_steps=6, goal="메모장에 안녕이라고 써 줘"):
        executor = executor or FakeExecutor()
        agent = ControlAgent(
            engine,
            executor,
            take_snapshot=lambda keywords, width: snapshot or screen(),
            keywords=lambda: (),
            width=1920,
            max_steps=max_steps,
            settle_sec=0,
        )
        seen = {"steps": [], "asked": [], "finished": []}
        agent.step.connect(seen["steps"].append)
        agent.finished.connect(lambda message, ok: seen["finished"].append((message, ok)))

        def on_confirm(text):
            seen["asked"].append(text)
            if confirm is not None:
                agent.answer(confirm)

        agent.confirm_needed.connect(on_confirm)
        agents.append(agent)
        assert agent.run(goal)
        return agent, executor, seen

    yield run
    for agent in agents:
        agent.stop()
        agent.join()


def test_agent_looks_decides_and_acts_until_done(run_agent, wait_until):
    engine = FakeEngine(CLICK, TYPE, DONE)

    agent, executor, seen = run_agent(engine)

    assert wait_until(lambda: seen["finished"] == [("다 썼어요.", True)])
    assert [action.kind for action in executor.done] == ["click", "type"]
    assert seen["steps"] == ["파일 메뉴를 누를게요.", "인사를 쓸게요."]
    assert "메모장에 안녕이라고 써 줘" in engine.prompts[0]
    # 지금까지 한 일은 AI의 말이 아니라 실제로 한 동작으로 알려 준다. 그래야 같은 일을 되풀이하지 않는다.
    assert "1. '파일'을(를) 눌렀다" in engine.prompts[1]
    assert "2. '안녕'이라고 입력했다" in engine.prompts[2]
    assert not agent.running


def two_monitor_screen(front: Window) -> Snapshot:
    return Snapshot(images=["main", "side"], foreground=front, hidden=[], skipped=False)


def test_only_the_monitor_in_use_is_shown_to_the_ai_to_save_time(run_agent, wait_until):
    on_side = Window("메모장", "notepad.exe", (-1500, 600, -700, 1200))  # 왼쪽 보조 모니터에 있는 창
    engine = FakeEngine({**CLICK, "monitor": 0, "x": 500, "y": 500}, DONE)

    _, executor, seen = run_agent(engine, snapshot=two_monitor_screen(on_side))

    assert wait_until(lambda: len(seen["finished"]) == 1)
    assert engine.images[0] == ["side"]
    # AI가 0번이라고 답해도, 보여 준 그림이 보조 모니터이므로 그 모니터의 가운데를 누른다.
    assert executor.points == [(-960, 1031)]


CALCULATOR = Window("계산기", "CalculatorApp.exe", (100, 200, 420, 700))
FAR_CLICK = {"action": "click", "why": "= 을 누를게요.", "monitor": 0, "x": 900, "y": 900, "target": "="}


def calculator_screen(rect=(100, 200, 420, 700)) -> Snapshot:
    return Snapshot(images=["main"], foreground=CALCULATOR, hidden=[], skipped=False, focus="win", focus_rect=rect)


def test_click_that_misses_the_window_in_use_is_aimed_again_on_a_close_up(run_agent, wait_until):
    """화면 전체에서는 작은 버튼을 빗맞히곤 한다. 쓰는 창 밖을 짚었으면 그 창만 크게 보여 주고 다시 짚게 한다."""
    engine = FakeEngine(FAR_CLICK, {**CLICK, "x": 500, "y": 500}, DONE)

    _, executor, seen = run_agent(engine, snapshot=calculator_screen())

    assert wait_until(lambda: len(seen["finished"]) == 1)
    assert engine.images == [["main"], ["win"], ["main"]]
    assert "'='" in engine.prompts[1]
    assert executor.points == [(260, 450)]  # 창의 한가운데
    assert executor.done[0].target == "="  # 무엇을 누르는지는 처음 판단 그대로


def test_click_inside_the_window_in_use_is_not_aimed_again(run_agent, wait_until):
    engine = FakeEngine({**CLICK, "x": 100, "y": 300}, DONE)

    _, executor, seen = run_agent(engine, snapshot=calculator_screen())

    assert wait_until(lambda: len(seen["finished"]) == 1)
    assert engine.images == [["main"], ["main"]]
    assert executor.points == [(192, 360)]


def test_original_aim_is_kept_when_the_target_is_not_in_the_window(run_agent, wait_until):
    """작업 표시줄처럼 정말 창 밖에 있는 것을 누르려던 것일 수 있다."""
    not_here = {"action": "fail", "why": "이 창에는 없어요."}
    engine = FakeEngine(FAR_CLICK, not_here, DONE)

    _, executor, seen = run_agent(engine, snapshot=calculator_screen())

    assert wait_until(lambda: len(seen["finished"]) == 1)
    assert executor.points == [(1728, 1080)]


def test_agent_waits_until_a_window_that_is_still_opening_has_settled(qapp, wait_until):
    """막 뜨는 창은 크기가 변하는 중이라, 그때 찍은 자리로 계산하면 엉뚱한 곳을 누른다."""
    growing = [
        calculator_screen((200, 300, 300, 400)),
        calculator_screen((150, 250, 360, 550)),
        calculator_screen(),
        calculator_screen(),
    ]
    executor = FakeExecutor()
    agent = ControlAgent(
        FakeEngine(FAR_CLICK, {**CLICK, "x": 500, "y": 500}, DONE),
        executor,
        take_snapshot=lambda keywords, width: growing.pop(0) if len(growing) > 1 else growing[0],
        keywords=lambda: (),
        width=1920,
        max_steps=3,
        settle_sec=0.01,
    )
    finished = []
    agent.finished.connect(lambda message, ok: finished.append(ok))

    agent.run("계산기에서 = 을 눌러 줘")

    assert wait_until(lambda: finished == [True])
    assert executor.points == [(260, 450)]  # 다 자란 창의 한가운데
    agent.join()


def test_all_monitors_are_shown_when_the_ai_cannot_find_what_it_needs(run_agent, wait_until):
    on_main = Window("메모장", "notepad.exe", (100, 100, 900, 700))
    cannot = {"action": "fail", "why": "계산기가 보이지 않아요."}
    engine = FakeEngine(cannot, {**CLICK, "monitor": 1}, DONE)

    _, executor, seen = run_agent(engine, snapshot=two_monitor_screen(on_main))

    assert wait_until(lambda: seen["finished"] == [("다 썼어요.", True)])
    assert engine.images == [["main"], ["main", "side"], ["main", "side"]]
    assert executor.done[0].monitor == 1


def test_failing_again_with_every_monitor_shown_ends_the_task(run_agent, wait_until):
    cannot = {"action": "fail", "why": "계산기가 보이지 않아요."}

    _, _, seen = run_agent(FakeEngine(cannot, cannot), snapshot=two_monitor_screen(NOTEPAD))

    assert wait_until(lambda: seen["finished"] == [("계산기가 보이지 않아요.", False)])


def test_risky_step_waits_for_permission_and_runs_when_allowed(run_agent, wait_until):
    engine = FakeEngine(CLICK, DONE)

    _, executor, seen = run_agent(engine, FakeExecutor(element="삭제"), confirm=True)

    assert wait_until(lambda: seen["finished"] == [("다 썼어요.", True)])
    assert "파일 메뉴를 누를게요." in seen["asked"][0] and "삭제" in seen["asked"][0]
    assert len(executor.done) == 1


def test_refused_step_is_not_done_and_the_task_ends(run_agent, wait_until):
    engine = FakeEngine(CLICK, DONE)

    _, executor, seen = run_agent(engine, FakeExecutor(element="삭제"), confirm=False)

    assert wait_until(lambda: len(seen["finished"]) == 1)
    assert seen["finished"][0][1] is False
    assert executor.done == []


def test_stop_halts_before_the_next_action(run_agent, wait_until):
    release = threading.Event()
    engine = FakeEngine(CLICK, TYPE, DONE)
    executor = FakeExecutor(on_perform=lambda: release.wait(3))  # 첫 동작을 하는 중에 멈춤이 들어온다

    agent, executor, seen = run_agent(engine, executor)
    assert wait_until(lambda: len(executor.done) == 1)
    agent.stop()
    release.set()

    assert wait_until(lambda: len(seen["finished"]) == 1)
    assert seen["finished"][0][1] is False
    assert len(executor.done) == 1


def test_stop_while_waiting_for_permission_ends_the_task(run_agent, wait_until):
    agent, executor, seen = run_agent(FakeEngine(CLICK, DONE), FakeExecutor(element="삭제"))
    assert wait_until(lambda: len(seen["asked"]) == 1)

    agent.stop()

    assert wait_until(lambda: len(seen["finished"]) == 1)
    assert executor.done == []


def test_agent_does_not_operate_while_a_hidden_window_is_in_front(run_agent, wait_until):
    engine = FakeEngine(CLICK)

    _, executor, seen = run_agent(engine, snapshot=screen(skipped=True, front=KAKAO))

    assert wait_until(lambda: len(seen["finished"]) == 1)
    assert seen["finished"][0][1] is False
    assert engine.prompts == [] and executor.done == []


def test_agent_gives_up_after_the_step_limit(run_agent, wait_until):
    _, executor, seen = run_agent(FakeEngine(*[TYPE] * 10), max_steps=3)

    assert wait_until(lambda: len(seen["finished"]) == 1)
    assert seen["finished"][0][1] is False
    assert len(executor.done) == 3


def test_agent_tolerates_one_bad_answer_but_not_a_stream_of_them(run_agent, wait_until):
    _, executor, seen = run_agent(FakeEngine({"action": "fly", "why": "x"}, TYPE, DONE))
    assert wait_until(lambda: seen["finished"] == [("다 썼어요.", True)])
    assert len(executor.done) == 1

    _, executor, seen = run_agent(FakeEngine(*[{"action": "fly", "why": "x"}] * 5))
    assert wait_until(lambda: len(seen["finished"]) == 1)
    assert seen["finished"][0][1] is False and executor.done == []


def test_engine_failure_ends_the_task_with_its_message(run_agent, wait_until):
    _, _, seen = run_agent(FakeEngine(EngineError("Ollama에 연결할 수 없습니다.")))

    assert wait_until(lambda: seen["finished"] == [("Ollama에 연결할 수 없습니다.", False)])


def test_only_one_task_runs_at_a_time(run_agent, wait_until):
    release = threading.Event()
    agent, _, seen = run_agent(FakeEngine(TYPE, DONE), FakeExecutor(on_perform=lambda: release.wait(3)))
    assert wait_until(lambda: agent.running)

    assert agent.run("다른 일") is False

    release.set()
    assert wait_until(lambda: len(seen["finished"]) == 1)
