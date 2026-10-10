from datetime import datetime, timedelta

import pytest
from PySide6.QtCore import QObject, QPoint, Qt, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QSizeGrip

from assister import app as app_module
from assister import paths
from assister.ai.engine import EngineError
from assister.character.art import generate_placeholder_pack
from assister.character.pack import load_pack
from assister.character.panel import MIN_SIZE
from assister.character.stage import MAX_SHOW_MS, MIN_SHOW_MS, THINKING_TEXT, CharacterStage, show_duration_ms
from assister.config import Settings, load_settings, save_settings
from assister.system.hotkey import HotkeyError
from assister.system.shell import CommandResult

# ---------- 왼쪽 패널: 캐릭터, 대화 기록, 입력창 ----------


@pytest.fixture
def pack(tmp_path):
    return load_pack(generate_placeholder_pack(tmp_path / "characters" / "default"))


@pytest.fixture
def stage(qapp, pack):
    stage = CharacterStage(pack, "basic")
    stage.show()
    yield stage
    stage.close()


def test_panel_fills_the_left_quarter_of_the_screen_from_top_to_bottom(stage):
    area = QGuiApplication.primaryScreen().availableGeometry()
    panel = stage.panel.geometry()

    assert (panel.x(), panel.y()) == (area.x(), area.y())
    # 화면의 1/4. 화면이 아주 좁으면 쓸 수 있는 가장 좁은 폭까지만 줄어든다.
    assert (panel.width(), panel.height()) == (max(area.width() // 4, MIN_SIZE[0]), area.height())


def test_character_keeps_its_proportions(stage):
    assert abs(stage.character.width() * 4 - stage.character.height() * 3) <= 4  # 480x640 그림의 비율 유지


def test_saying_writes_into_the_chat_log_and_changes_expression(stage):
    stage.say("안녕하세요!", "smile")

    assert stage.speech == "안녕하세요!"
    assert stage.panel.log.entries() == [("assistant", "안녕하세요!")]
    assert stage.character.expression == "smile"
    assert stage.settling


def test_expression_returns_to_the_idle_one_after_speaking(stage):
    stage.say("안녕하세요!", "smile")

    stage.settle()

    assert stage.character.expression == "neutral" and not stage.settling


def test_thinking_then_streaming_rewrites_one_entry_until_released(stage):
    stage.think()
    assert stage.speech == THINKING_TEXT
    assert stage.character.expression == "thinking"

    stage.stream("안녕")
    stage.stream("안녕하세요!")
    assert stage.panel.log.entries() == [("assistant", "안녕하세요!")]
    assert stage.character.expression == "talking"
    assert not stage.settling  # 글이 나오는 동안에는 표정을 풀지 않는다

    stage.release()
    assert stage.settling

    stage.say("또 왔어요")
    assert [text for _, text in stage.panel.log.entries()] == ["안녕하세요!", "또 왔어요"]


def test_a_sudden_remark_takes_over_the_entry_still_being_thought_about(stage):
    # '…'만 떠 있다가 답 대신 오류나 알림을 말하게 되면, 빈 '…' 덩어리를 남기지 않는다.
    stage.think()

    stage.say("Ollama에 연결할 수 없습니다.", "sad")

    assert stage.panel.log.entries() == [("assistant", "Ollama에 연결할 수 없습니다.")]


def test_chat_log_keeps_who_said_what_in_order(stage):
    stage.load_history([("user", "안녕"), ("assistant", "안녕하세요!")])
    stage.show_user("바탕화면에 뭐 있어?")
    stage.think()
    stage.note("명령 · Get-ChildItem $Desktop")  # 한 일은 그 일로 하는 답보다 위에 적힌다
    stage.stream("메모.txt가 있어요.")

    assert stage.panel.log.entries() == [
        ("user", "안녕"),
        ("assistant", "안녕하세요!"),
        ("user", "바탕화면에 뭐 있어?"),
        ("note", "명령 · Get-ChildItem $Desktop"),
        ("assistant", "메모.txt가 있어요."),
    ]


def test_short_remark_stays_on_one_line_and_long_one_wraps_inside_the_panel(stage):
    stage.say("네!")
    short = stage.panel.log._entries[-1]._label
    line_height = short.fontMetrics().lineSpacing()
    assert short.height() < line_height * 2

    stage.say("<b>가나다라마바사</b> " * 30)
    long = stage.panel.log._entries[-1]._label
    assert long.height() > line_height * 2
    assert long.width() <= stage.panel.log.entry_width
    # 인터넷에서 온 글이 섞일 수 있다. 꾸밈 기호로 읽지 않고 글자 그대로 보여 준다.
    assert long.textFormat() == Qt.TextFormat.PlainText


def test_lists_show_todos_alarms_and_what_was_seen(stage):
    stage.panel.show_tasks(["책 반납", "<보고서>"], ["오후 9:00 약 먹기"])
    stage.panel.show_screen_notes([])

    assert "1. 책 반납" in stage.panel.tasks.text and "2. &lt;보고서&gt;" in stage.panel.tasks.text
    assert "오후 9:00 약 먹기" in stage.panel.tasks.text
    assert "아직 본 것이 없어요" in stage.panel.screen.text


def test_panel_can_be_put_away_and_brought_back(stage):
    stage.toggle()
    assert not stage.panel.isVisible()

    stage.toggle()
    assert stage.panel.isVisible()

    stage.panel.hide_clicked.emit()
    assert not stage.panel.isVisible()


def test_panel_goes_where_it_was_left_unless_that_spot_is_off_every_screen(qapp, pack):
    stage = CharacterStage(pack, "basic")

    stage.show((120, 80))
    assert (stage.panel.x(), stage.panel.y()) == (120, 80)

    stage.show((50_000, 50_000))  # 그때 쓰던 모니터를 뽑았을 때
    area = QGuiApplication.primaryScreen().availableGeometry()
    assert (stage.panel.x(), stage.panel.y()) == (area.x(), area.y())
    stage.close()


def test_moving_the_panel_reports_where_it_ended_up_and_double_click_sends_it_back(stage):
    places, sizes, docked = [], [], []
    stage.moved.connect(lambda x, y: places.append((x, y)))
    stage.resized.connect(lambda width, height: sizes.append((width, height)))
    stage.docked.connect(lambda: docked.append(True))

    stage.panel.move(140, 60)
    stage.panel.resize(320, 480)
    stage.panel._moved_timer.timeout.emit()  # 끌기를 멈춘 뒤
    assert (places, sizes) == ([(140, 60)], [(320, 480)])

    QTest.mouseDClick(stage.panel, Qt.MouseButton.LeftButton, pos=QPoint(40, 8))
    area = QGuiApplication.primaryScreen().availableGeometry()
    assert stage.panel.geometry().getRect() == (area.x(), area.y(), max(area.width() // 4, MIN_SIZE[0]), area.height())
    # 되돌린 자리를 '옮겨 둔 자리'로 다시 알리지 않는다.
    assert docked == [True] and not stage.panel._moved_timer.isActive()


def test_panel_takes_the_size_it_was_left_at_but_never_more_than_the_screen(qapp, pack):
    stage = CharacterStage(pack, "basic")
    area = QGuiApplication.primaryScreen().availableGeometry()

    stage.show(None, (300, 500))
    assert (stage.panel.width(), stage.panel.height()) == (300, 500)

    stage.show(None, (90_000, 90_000))
    assert (stage.panel.width(), stage.panel.height()) == (area.width(), area.height())
    stage.close()


def test_resizing_rewraps_the_talk_and_there_is_a_corner_to_drag(stage):
    stage.say("가나다라마바사 " * 30)
    label = stage.panel.log._entries[-1]._label
    narrow = label.width()

    stage.panel.resize(stage.panel.width() + 300, stage.panel.height())

    assert narrow < label.width() <= stage.panel.log.entry_width
    assert stage.panel.findChild(QSizeGrip) is not None


def test_input_bar_emits_what_was_typed(stage):
    received = []
    stage.submitted.connect(received.append)
    edit = stage.input_bar._edit

    edit.setText("  할 일 목록  ")
    edit.returnPressed.emit()

    assert stage.input_bar.isVisible()
    assert received == ["할 일 목록"]
    assert edit.text() == ""


def test_mic_button_asks_to_listen_and_shows_what_the_microphone_is_doing(stage):
    clicks = []
    stage.mic_clicked.connect(lambda: clicks.append(True))

    stage.input_bar._mic.click()
    stage.input_bar.set_mic_state("hearing")

    assert clicks == [True]
    assert stage.input_bar.mic_state == "hearing"
    assert "듣는 중" in stage.input_bar._mic.toolTip()


def test_heard_words_are_shown_briefly_in_the_input_bar(stage):
    stage.input_bar.show_heard("불 켜 줘")

    assert "불 켜 줘" in stage.input_bar._edit.placeholderText()


def test_changing_outfit_and_props_redraws(stage):
    before = stage.character.grab().toImage()

    stage.character.set_outfit("dress")
    stage.character.set_props(("glasses",))

    assert stage.character.grab().toImage() != before


@pytest.mark.parametrize(
    ("text", "expected"),
    [("짧은 말", MIN_SHOW_MS), ("가" * 100, 9000), ("가" * 1000, MAX_SHOW_MS)],
)
def test_longer_remark_keeps_its_expression_longer(text, expected):
    assert show_duration_ms(text) == expected


# ---------- 앱 전체 (AI, 목소리, 마이크는 흉내) ----------


class FakeEngine:
    def __init__(self, *, plans=(), replies=(), fail_with: EngineError | None = None):
        self.plans = list(plans)
        self.replies = list(replies)
        self.fail_with = fail_with
        self.warmed = 0
        self.asked: list[str] = []

    def warm_up(self):
        self.warmed += 1
        if self.fail_with:
            raise self.fail_with

    def complete_json(self, messages, schema):
        if self.fail_with:
            raise self.fail_with
        return self.plans.pop(0) if self.plans else {"actions": []}

    def chat(self, messages, *, on_delta=None, should_stop=None):
        if self.fail_with:
            raise self.fail_with
        self.asked.append(messages[-1]["content"])
        reply = self.replies.pop(0) if self.replies else "네!"
        if on_delta:
            # 실제 엔진처럼 여러 조각으로 나눠 보낸다.
            middle = len(reply) // 2
            on_delta(reply[:middle])
            on_delta(reply[middle:])
        return reply

    def close(self):
        pass


class FakeSpeaker(QObject):
    started = Signal()
    finished = Signal()
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.said: list[str] = []
        self.stops = 0

    def say(self, text):
        self.said.append(text)

    def stop(self):
        self.stops += 1

    def shutdown(self):
        pass


class FakeListener(QObject):
    heard = Signal(str, str)
    state_changed = Signal(str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self.armed = 0
        self.paused = False
        self.wake_enabled: bool | None = None

    def start(self):
        self.running = True

    def set_wake_enabled(self, enabled):
        self.wake_enabled = enabled

    def arm(self):
        self.armed += 1

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def shutdown(self):
        self.running = False


class FakeWatcher(QObject):
    noted = Signal(str, str)
    state_changed = Signal(str)
    failed = Signal(str)

    def __init__(self):
        super().__init__()
        self.running = False
        self.enabled: bool | None = None

    def start(self):
        self.running = True

    def set_enabled(self, enabled):
        self.enabled = enabled

    def shutdown(self):
        self.running = False


class FakeAgent(QObject):
    started = Signal(str)
    step = Signal(str)
    confirm_needed = Signal(str)
    finished = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.goals: list[str] = []
        self.answers: list[bool] = []
        self.stops = 0
        self.running = False

    def run(self, goal):
        self.goals.append(goal)
        return True

    def stop(self):
        self.stops += 1

    def answer(self, allowed):
        self.answers.append(allowed)

    def join(self, timeout=3.0):
        pass


class FakeHotkey(QObject):
    """실제로 키를 가로채지 않는 단축키. 테스트에서 pressed를 직접 보낸다."""

    pressed = Signal()
    made: list["FakeHotkey"] = []

    def __init__(self, text, parent=None, *, allow_bare=False):
        super().__init__(parent)
        self.text, self.allow_bare, self.released = text, allow_bare, False
        FakeHotkey.made.append(self)

    def release(self):
        self.released = True


@pytest.fixture
def make_friend(qapp, tmp_path, monkeypatch, wait_until):
    monkeypatch.setenv(paths.DATA_DIR_ENV, str(tmp_path / "data"))
    monkeypatch.setattr(paths, "characters_dir", lambda: tmp_path / "characters")

    def refuse_hotkey(*_args, **_kwargs):
        raise HotkeyError("테스트에서는 전역 단축키를 등록하지 않습니다")

    monkeypatch.setattr(app_module, "GlobalHotkey", refuse_hotkey)
    # 진짜 시작프로그램 폴더를 건드리지 않는다.
    monkeypatch.setattr(app_module.autostart, "startup_folder", lambda: tmp_path / "Startup")
    # 진짜 명령을 실행하거나 인터넷을 검색하지 않는다.
    monkeypatch.setattr(app_module.shell, "run", lambda command: CommandResult("", 0))
    monkeypatch.setattr(app_module, "search", lambda query: [])
    # 진짜 문서·다운로드 폴더에 파일을 만들지 않는다.
    monkeypatch.setattr(app_module.documents, "known_folder", lambda kind: tmp_path / kind)
    friends = []

    def make(engine=None, *, settings: Settings | None = None, wait_ready: bool = True):
        if settings is not None:
            save_settings(paths.settings_path(), settings)
        friend = app_module.DesktopFriend(
            qapp,
            engine=engine or FakeEngine(),
            speaker=FakeSpeaker(),
            listener=FakeListener(),
            watcher=FakeWatcher(),
            agent=FakeAgent(),
        )
        friend.start()
        friends.append(friend)
        if wait_ready:
            # 모델이 올라왔다는 신호가 화면 스레드에 닿을 때까지 기다린다.
            assert wait_until(lambda: friend._model_ready)
        return friend

    yield make
    for friend in friends:
        friend.shutdown()


def speech_of(friend) -> str:
    return friend.stage.speech


def test_app_starts_with_generated_character_greets_and_wakes_the_model(make_friend, wait_until, tmp_path):
    engine = FakeEngine()

    friend = make_friend(engine)

    assert (tmp_path / "characters" / "default" / "outfits" / "basic" / "neutral.png").exists()
    assert "안녕하세요" in speech_of(friend)
    # 단축키 등록이 실패해도 앱은 뜨고, 이유를 알려 준다.
    assert "단축키" in speech_of(friend)
    assert wait_until(lambda: engine.warmed == 1)
    assert friend.listener.running


def test_greeting_mentions_what_is_left_to_do_today(make_friend, tmp_path):
    from assister.tasks.repository import TaskRepository

    now = datetime.now()
    seeded = TaskRepository(tmp_path / "data" / "tasks.db")
    seeded.add_todo("우유 사기", now)
    seeded.add_todo("메일 답장", now)
    seeded.add_alarm("회의", now + timedelta(hours=1))
    seeded.close()

    friend = make_friend()

    assert "할 일 2개" in friend._greeting() and "알람 1개" in friend._greeting()


def test_greeting_stays_short_when_nothing_is_pending(make_friend):
    friend = make_friend()

    assert friend._greeting() == "안녕하세요, 모모예요!"


def test_typed_talk_is_answered_by_the_ai_and_remembered(make_friend, wait_until):
    engine = FakeEngine(replies=["저도 반가워요!"])
    friend = make_friend(engine)

    friend.stage.submitted.emit("안녕, 반가워")

    assert friend.stage.character.expression == "thinking"
    assert wait_until(lambda: speech_of(friend) == "저도 반가워요!")
    assert engine.asked == ["안녕, 반가워"]
    assert [m.content for m in friend.memory.recent_messages(5)] == ["안녕, 반가워", "저도 반가워요!"]


def test_answer_is_spoken_sentence_by_sentence(make_friend, wait_until):
    friend = make_friend(FakeEngine(replies=["첫 문장이에요. 둘째 문장이에요."]))

    friend.stage.submitted.emit("말해 봐")

    assert wait_until(lambda: friend.speaker.said == ["첫 문장이에요.", "둘째 문장이에요."])


def test_speech_and_ears_wait_for_the_voice_to_finish(make_friend, wait_until, qapp):
    friend = make_friend(FakeEngine(replies=["지금 말하는 중이에요."]))
    friend.stage.submitted.emit("말해 봐")
    assert wait_until(lambda: friend.speaker.said == ["지금 말하는 중이에요."])

    friend.speaker.started.emit()
    qapp.processEvents()
    assert friend.listener.paused  # 자기 목소리를 듣지 않게 귀를 막는다
    assert friend.stage.character.expression == "talking"
    assert not friend.stage.settling

    friend.speaker.finished.emit()
    qapp.processEvents()
    assert not friend.listener.paused
    assert friend.stage.settling


@pytest.mark.parametrize("settings", [Settings(tts_enabled=False), Settings(do_not_disturb=True)])
def test_no_voice_when_turned_off_or_do_not_disturb(make_friend, wait_until, settings):
    friend = make_friend(FakeEngine(replies=["조용히 답할게요."]), settings=settings)

    friend.stage.submitted.emit("말해 봐")

    assert wait_until(lambda: speech_of(friend) == "조용히 답할게요." and friend.stage.settling)
    assert friend.speaker.said == []


def test_ai_can_set_an_alarm_and_it_rings_aloud(make_friend, wait_until, monkeypatch):
    plan = {"actions": [{"tool": "add_alarm", "minutes_from_now": 10, "message": "빨래 꺼내기"}]}
    friend = make_friend(FakeEngine(plans=[plan], replies=["."]))

    friend.stage.submitted.emit("10분 뒤에 빨래 꺼내라고 해 줘")
    assert wait_until(lambda: speech_of(friend).endswith("'빨래 꺼내기' 알려 줄게요.") and not friend._replying)
    assert [alarm.message for alarm in friend.repository.list_pending_alarms()] == ["빨래 꺼내기"]
    assert friend.speaker.said == [speech_of(friend)]

    later = datetime.now() + timedelta(minutes=11)
    monkeypatch.setattr(app_module, "datetime", type("Clock", (), {"now": staticmethod(lambda: later)}))
    friend._check_alarms()

    assert speech_of(friend) == "빨래 꺼내기 시간이에요!"
    assert friend.stage.character.expression == "surprised"
    assert friend.speaker.said[-1] == "빨래 꺼내기 시간이에요!"


def test_simple_commands_still_work_when_the_ai_is_down(make_friend, wait_until):
    friend = make_friend(FakeEngine(fail_with=EngineError("Ollama에 연결할 수 없습니다.")))

    friend.stage.submitted.emit("10분 뒤 빨래 꺼내기")

    assert wait_until(lambda: "빨래 꺼내기" in speech_of(friend))
    assert [alarm.message for alarm in friend.repository.list_pending_alarms()] == ["빨래 꺼내기"]


def test_ai_failure_is_explained_for_ordinary_talk(make_friend, wait_until):
    friend = make_friend(FakeEngine(fail_with=EngineError("Ollama에 연결할 수 없습니다.")))

    friend.stage.submitted.emit("오늘 뭐 했어?")

    assert wait_until(lambda: "Ollama" in speech_of(friend) and friend.stage.character.expression == "sad")


def test_command_spoken_with_the_name_is_answered(make_friend, wait_until):
    engine = FakeEngine(replies=["불은 제가 못 켜요."])
    friend = make_friend(engine)

    friend.listener.heard.emit("불 켜 줘", "wake")

    assert wait_until(lambda: engine.asked == ["불 켜 줘"])
    assert "불 켜 줘" in friend.stage.input_bar._edit.placeholderText()


def test_bare_name_gets_a_short_acknowledgement_without_asking_the_ai(make_friend, qapp):
    engine = FakeEngine()
    friend = make_friend(engine)

    friend.listener.heard.emit("", "wake_only")
    qapp.processEvents()

    assert speech_of(friend) == app_module.WAKE_ACK
    assert engine.asked == []
    assert friend.speaker.said == []


def test_mic_button_starts_listening_and_new_input_interrupts_speech(make_friend):
    friend = make_friend()

    friend.stage.mic_clicked.emit()
    friend.stage.submitted.emit("잠깐만")

    assert friend.listener.armed == 1
    assert friend.speaker.stops >= 1


def test_microphone_state_and_failure_reach_the_screen(make_friend, qapp):
    friend = make_friend()

    friend.listener.state_changed.emit("hearing")
    assert friend.stage.input_bar.mic_state == "hearing"

    friend.listener.failed.emit("마이크를 열 수 없습니다: 장치 없음")
    qapp.processEvents()
    assert "마이크" in speech_of(friend)
    assert load_settings(paths.settings_path()).wake_word_enabled is False


def test_voice_and_wake_word_switches_are_applied_and_remembered(make_friend):
    friend = make_friend()
    assert friend.listener.wake_enabled is None  # 시작할 때는 설정값대로 만들어져 있다

    friend.menu.tts_toggled.emit(False)
    friend.menu.wake_toggled.emit(False)

    saved = load_settings(paths.settings_path())
    assert saved.tts_enabled is False and saved.wake_word_enabled is False
    assert friend.listener.wake_enabled is False
    assert friend.speaker.stops >= 1


def set_clock(monkeypatch, moment: datetime) -> None:
    monkeypatch.setattr(app_module, "datetime", type("Clock", (), {"now": staticmethod(lambda: moment)}))


def test_offer_from_the_screen_is_spoken_but_only_occasionally(make_friend, monkeypatch):
    start = datetime(2026, 10, 6, 15, 0)
    set_clock(monkeypatch, start)
    friend = make_friend()
    assert friend.watcher.running

    # 켠 직후에는 말을 걸지 않는다.
    friend.watcher.noted.emit("코드를 고치는 중", "오류 같이 볼까요?")
    assert "오류" not in speech_of(friend)

    set_clock(monkeypatch, start + timedelta(minutes=61))
    friend.watcher.noted.emit("코드를 고치는 중", "오류 같이 볼까요?")
    assert speech_of(friend) == "오류 같이 볼까요?"
    assert friend.speaker.said == ["오류 같이 볼까요?"]
    # 이어서 "응"이라고 답해도 알아듣도록 대화 기록에 남긴다.
    assert friend.memory.recent_messages(1)[0].content == "오류 같이 볼까요?"

    set_clock(monkeypatch, start + timedelta(minutes=80))
    friend.watcher.noted.emit("코드를 고치는 중", "또 도와줄까요?")
    assert speech_of(friend) == "오류 같이 볼까요?"


@pytest.mark.parametrize("settings", [Settings(do_not_disturb=True), Settings(proactive_per_hour=0)])
def test_no_offer_during_do_not_disturb_or_when_turned_off(make_friend, monkeypatch, settings):
    set_clock(monkeypatch, datetime(2026, 10, 6, 15, 0))
    friend = make_friend(settings=settings)

    set_clock(monkeypatch, datetime(2026, 10, 6, 18, 0))
    friend.watcher.noted.emit("영상을 보는 중", "자막 찾아 줄까요?")

    assert "자막" not in speech_of(friend)


def test_screen_watching_can_be_seen_and_switched_off_at_any_time(make_friend):
    friend = make_friend()

    friend.watcher.state_changed.emit("watching")
    assert friend.stage.input_bar.eye_state == "watching"

    friend.stage.input_bar._eye.click()

    assert friend.watcher.enabled is False
    assert load_settings(paths.settings_path()).screen_watch_enabled is False

    friend.menu.screen_toggled.emit(True)
    assert friend.watcher.enabled is True


def test_screen_watching_stays_off_when_it_was_turned_off_before(make_friend):
    friend = make_friend(settings=Settings(screen_watch_enabled=False))

    assert friend.watcher.enabled is False


def test_request_to_operate_the_pc_starts_the_operator(make_friend, wait_until):
    plan = {"actions": [{"tool": "control_pc", "goal": "메모장 열어서 안녕이라고 써 줘"}]}
    friend = make_friend(FakeEngine(plans=[plan]))

    friend.stage.submitted.emit("메모장 열어서 안녕이라고 써 줘")

    assert wait_until(lambda: friend.agent.goals == ["메모장 열어서 안녕이라고 써 줘"])
    assert wait_until(lambda: "Esc" in speech_of(friend))


def test_esc_stops_the_operator_only_while_it_is_working(make_friend, monkeypatch):
    FakeHotkey.made.clear()
    monkeypatch.setattr(app_module, "GlobalHotkey", FakeHotkey)
    friend = make_friend()
    assert all(hotkey.text != "Esc" for hotkey in FakeHotkey.made)  # 평소에는 Esc를 가로채지 않는다

    friend.agent.started.emit("메모장 열기")
    escape = FakeHotkey.made[-1]
    assert (escape.text, escape.allow_bare) == ("Esc", True)
    assert not friend.stage.panel.isVisible()  # 패널 밑을 누를 수 있게 조작하는 동안 비켜 준다

    escape.pressed.emit()
    assert friend.agent.stops == 1

    friend.agent.finished.emit("멈췄어요.", False)
    assert escape.released
    assert friend.stage.panel.isVisible()
    assert speech_of(friend) == "멈췄어요."
    assert friend.memory.recent_messages(1)[0].content == "멈췄어요."


def test_operator_is_not_allowed_to_work_without_a_stop_key(make_friend):
    friend = make_friend()  # 이 테스트 환경에서는 단축키 등록이 전부 실패한다

    friend.agent.started.emit("메모장 열기")

    assert friend.agent.stops == 1
    assert "Esc" in speech_of(friend)


@pytest.mark.parametrize(("button", "allowed"), [("Yes", True), ("No", False)])
def test_risky_step_is_put_to_the_user_and_the_answer_passed_back(make_friend, button, allowed):
    from PySide6.QtWidgets import QMessageBox

    friend = make_friend()

    friend.agent.confirm_needed.emit("파일을 지울게요. ('삭제'을(를) 누르는 일이에요)")

    assert "파일을 지울게요." in friend._permission.text()
    friend._permission.button(getattr(QMessageBox.StandardButton, button)).click()
    assert friend.agent.answers == [allowed]


def asking_to_delete(make_friend, monkeypatch, wait_until):
    """'old.txt 지워 줘'라고 해서 허락을 구하는 데까지 간다."""
    ran = []
    monkeypatch.setattr(app_module.shell, "run", lambda command: ran.append(command) or CommandResult("보냄", 0))
    plan = {"actions": [{"tool": "run_command", "command": "Remove-Item old.txt"}]}
    engine = FakeEngine(plans=[plan], replies=["지웠어요."])
    friend = make_friend(engine)
    friend.stage.submitted.emit("old.txt 지워 줘")
    assert wait_until(lambda: friend._permission is not None)
    return friend, engine, ran


def test_risky_command_waits_until_the_user_says_yes(make_friend, monkeypatch, wait_until):
    friend, engine, ran = asking_to_delete(make_friend, monkeypatch, wait_until)

    assert "Remove-Item old.txt" in friend._permission.text() and ran == []
    assert "휴지통" in friend.speaker.said[-1] and "Remove-Item" not in friend.speaker.said[-1]  # 까닭만 읽어 준다
    assert friend.listener.armed == 1  # PC를 만지지 않고 말로 답할 수 있게 귀를 연다

    friend.listener.heard.emit("음 글쎄", "command")  # 알아듣지 못한 말은 대답이 아니다

    assert friend._permission is not None and friend.listener.armed == 2 and ran == []

    friend.listener.heard.emit("허락", "command")

    assert wait_until(lambda: ran == ["Remove-Item old.txt"])
    assert friend._permission is None
    assert wait_until(lambda: engine.asked == ["old.txt 지워 줘"])  # 허락을 구하던 중에 한 말은 AI에게 넘기지 않는다


def test_risky_command_is_dropped_when_the_user_says_no(make_friend, monkeypatch, wait_until):
    friend, engine, ran = asking_to_delete(make_friend, monkeypatch, wait_until)

    friend.listener.heard.emit("아니 하지 마", "command")

    assert wait_until(lambda: "실행하지 않을게요" in speech_of(friend))
    assert ran == [] and engine.asked == []


def test_operator_narrates_what_it_is_doing(make_friend):
    friend = make_friend()

    friend.agent.step.emit("파일 메뉴를 누를게요.")

    assert speech_of(friend) == "파일 메뉴를 누를게요."


def menu_item(friend, label: str):
    return next(action for action, name, _level in friend.menu._lockable if name == label)


def test_talking_raises_the_bond_and_a_new_level_unlocks_outfits_and_props(make_friend, wait_until):
    engine = FakeEngine(replies=["네!"] * 10)
    friend = make_friend(engine)
    assert not menu_item(friend, "하늘 가디건").isEnabled()
    assert "레벨 2에 열림" in menu_item(friend, "하늘 가디건").text()

    for number in range(9):
        friend.stage.submitted.emit(f"말 {number}")
    assert wait_until(lambda: len(engine.asked) == 9 and not friend._replying)
    assert "친밀도 레벨 1 · 다음 레벨까지 1마디" in friend.menu._bond.text()

    friend.stage.submitted.emit("열 번째 말")

    assert wait_until(lambda: "친밀도 레벨 2" in speech_of(friend))
    assert "하늘 가디건" in speech_of(friend) and "리본" in speech_of(friend)
    assert menu_item(friend, "하늘 가디건").isEnabled() and menu_item(friend, "리본").isEnabled()
    assert not menu_item(friend, "분홍 원피스").isEnabled()
    assert friend.memory.get_state("bond_points", 0) == 10


def test_bond_is_remembered_across_restarts(make_friend):
    first = make_friend()
    first.memory.set_state("bond_points", 35)
    first.shutdown()

    second = make_friend()

    assert "친밀도 레벨 3" in second.menu._bond.text()
    assert menu_item(second, "안경").isEnabled()


def test_props_can_be_worn_and_are_remembered(make_friend):
    friend = make_friend()
    before = friend.stage.character.grab().toImage()

    friend.menu.prop_toggled.emit("ribbon", True)

    assert load_settings(paths.settings_path()).props == ("ribbon",)
    assert friend.stage.character.grab().toImage() != before

    friend.menu.prop_toggled.emit("ribbon", False)
    assert load_settings(paths.settings_path()).props == ()


def test_character_looks_sleepy_late_at_night(make_friend, monkeypatch):
    set_clock(monkeypatch, datetime(2026, 10, 6, 3, 0))

    friend = make_friend()

    assert friend.stage.idle_expression == "sleepy"


def test_ai_is_told_how_close_we_are_and_how_the_character_feels(make_friend, wait_until, monkeypatch):
    set_clock(monkeypatch, datetime(2026, 10, 6, 3, 0))
    seen = []

    class Recording(FakeEngine):
        def chat(self, messages, *, on_delta=None, should_stop=None):
            seen.append(messages[0]["content"])
            return super().chat(messages, on_delta=on_delta, should_stop=should_stop)

    friend = make_friend(Recording())
    friend.stage.submitted.emit("안녕")

    assert wait_until(lambda: len(seen) == 1)
    assert "친밀도 레벨 1" in seen[0] and "졸림" in seen[0]


def test_settings_window_saves_character_and_app_settings_then_restarts(make_friend, monkeypatch, tmp_path):
    restarts = []
    monkeypatch.setattr(app_module, "restart", lambda: restarts.append(True))
    friend = make_friend()

    friend.menu.settings_requested.emit()
    dialog = friend._settings_dialog
    dialog.name.setText("하루")
    dialog.interval.setValue(12)
    dialog.blocked.setPlainText("stardew")
    dialog.accept()

    assert load_settings(paths.settings_path()).screen_interval_sec == 12
    assert load_settings(paths.settings_path()).blocked_apps == ("stardew",)
    assert load_pack(tmp_path / "characters" / "default").name == "하루"
    assert restarts == [True]


def test_voice_preview_speaks_with_the_chosen_voice_and_cancel_puts_the_old_one_back(make_friend):
    friend = make_friend()
    original = friend.speaker.voice = friend.pack.voice

    friend.menu.settings_requested.emit()
    friend._settings_dialog.voice.setCurrentText("ko-KR-InJoonNeural")
    friend._settings_dialog.preview.click()

    assert friend.speaker.voice.name == "ko-KR-InJoonNeural"
    assert friend.speaker.said == ["안녕하세요, 모모예요."]

    friend._settings_dialog.reject()
    assert friend.speaker.voice == original


def test_start_with_windows_follows_the_switch_in_settings(make_friend, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "restart", lambda: None)
    startup = tmp_path / "Startup"
    friend = make_friend()

    friend.menu.settings_requested.emit()
    assert not friend._settings_dialog.autostart.isChecked()
    friend._settings_dialog.autostart.setChecked(True)
    friend._settings_dialog.accept()

    assert (startup / "assister.cmd").exists()


def test_clicking_the_tray_icon_calls_the_character(make_friend, qapp):
    from PySide6.QtWidgets import QSystemTrayIcon

    friend = make_friend()
    friend.stage.hide()

    friend.menu.tray.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)

    assert friend.stage.panel.isVisible()


def test_switching_character_keeps_each_character_s_own_name_and_voice(make_friend, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "restart", lambda: None)
    generate_placeholder_pack(tmp_path / "characters" / "haru", name="하루")
    first = make_friend(settings=Settings(outfit="dress", props=("ribbon",)))

    first.menu.settings_requested.emit()
    first._settings_dialog.character.setCurrentText("haru")
    first._settings_dialog.name.setText("이 이름은 저장되면 안 됨")
    first._settings_dialog.accept()

    saved = load_settings(paths.settings_path())
    assert (saved.character, saved.outfit, saved.props) == ("haru", "basic", ())
    assert load_pack(tmp_path / "characters" / "default").name == "모모"
    assert make_friend().pack.name == "하루"


def test_cancelled_settings_change_nothing(make_friend, monkeypatch):
    restarts = []
    monkeypatch.setattr(app_module, "restart", lambda: restarts.append(True))
    friend = make_friend()

    friend.menu.settings_requested.emit()
    friend._settings_dialog.name.setText("하루")
    friend._settings_dialog.reject()

    assert friend.pack.name == "모모" and restarts == []
    assert not paths.settings_path().exists()


def test_memory_can_be_exported_to_a_file(make_friend, monkeypatch, tmp_path):
    archive = tmp_path / "기억.zip"
    monkeypatch.setattr(app_module, "pick_save_path", lambda: str(archive))
    friend = make_friend()
    friend.memory.add_fact("이름은 상민", datetime(2026, 10, 6, 15, 0))

    friend.menu.export_requested.emit()

    assert archive.exists()
    assert "기억" in speech_of(friend)


def test_importing_memory_asks_first_then_replaces_and_restarts(make_friend, monkeypatch, tmp_path):
    from assister.memory.backup import export_data

    source = make_friend()
    source.memory.set_state("bond_points", 77)
    archive = tmp_path / "기억.zip"
    export_data(paths.data_dir(), archive)
    source.memory.set_state("bond_points", 1)

    asked, restarts = [], []
    monkeypatch.setattr(app_module, "pick_open_path", lambda: str(archive))
    monkeypatch.setattr(app_module, "confirm", lambda text: asked.append(text) or True)
    monkeypatch.setattr(app_module, "restart", lambda: restarts.append(True))

    source.menu.import_requested.emit()

    assert len(asked) == 1 and "사라" in asked[0]
    assert restarts == [True]
    assert make_friend().memory.get_state("bond_points", 0) == 77


def test_import_is_dropped_when_refused_or_the_file_is_not_a_memory_file(make_friend, monkeypatch, tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"not a zip")
    restarts = []
    monkeypatch.setattr(app_module, "restart", lambda: restarts.append(True))
    monkeypatch.setattr(app_module, "pick_open_path", lambda: str(bad))
    monkeypatch.setattr(app_module, "confirm", lambda text: True)
    friend = make_friend()

    friend.menu.import_requested.emit()

    assert restarts == [] and friend.stage.character.expression == "sad"
    assert friend.memory.count_messages() == 0  # 앱은 꺼지지 않고 그대로 쓸 수 있다


def test_face_matches_the_answer_once_it_is_said(make_friend, wait_until, qapp):
    friend = make_friend(FakeEngine(replies=["정말 축하해요!"]), settings=Settings(tts_enabled=False))

    friend.stage.submitted.emit("나 시험 붙었어")

    assert wait_until(lambda: speech_of(friend) == "정말 축하해요!" and not friend._replying)
    assert friend.stage.character.expression == "excited"


def test_face_matches_the_answer_after_the_voice_finishes(make_friend, wait_until, qapp):
    friend = make_friend(FakeEngine(replies=["미안해요, 그건 못 해요."]))
    friend.stage.submitted.emit("달에 데려다 줘")
    assert wait_until(lambda: len(friend.speaker.said) == 1 and not friend._replying)

    friend.speaker.started.emit()
    qapp.processEvents()
    assert friend.stage.character.expression == "talking"

    friend.speaker.finished.emit()
    qapp.processEvents()
    assert friend.stage.character.expression == "sad"


def test_talking_before_the_model_is_loaded_says_it_is_still_waking_up(make_friend, qapp):
    """처음 켜면 모델을 올리는 데 1분 넘게 걸릴 수 있다. 말없이 '…'만 띄우지 않는다."""
    import threading

    gate = threading.Event()

    class Slow(FakeEngine):
        def warm_up(self):
            gate.wait(3)

    friend = make_friend(Slow(), wait_ready=False)
    try:
        friend.stage.submitted.emit("안녕")
        qapp.processEvents()

        assert "깨우는 중" in speech_of(friend)
    finally:
        gate.set()


def test_second_copy_of_the_app_is_refused_while_one_is_running(tmp_path):
    first = app_module.single_instance_lock(tmp_path)
    assert first is not None

    assert app_module.single_instance_lock(tmp_path, wait_ms=50) is None

    first.unlock()
    again = app_module.single_instance_lock(tmp_path, wait_ms=50)
    assert again is not None
    again.unlock()


def test_the_engine_named_in_settings_is_the_one_that_gets_built(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    gemini = app_module.make_engine(Settings(engine="gemini", gemini_model="gemini-3.5-flash-lite"))
    ollama = app_module.make_engine(Settings())

    antigravity = app_module.make_engine(Settings(engine="agy"))

    assert (type(gemini).__name__, gemini.model) == ("GeminiEngine", "gemini-3.5-flash-lite")
    assert (type(ollama).__name__, ollama.model) == ("OllamaEngine", "qwen3.5:9b")
    assert (type(antigravity).__name__, antigravity.model) == ("AgyEngine", "gemini-3.8-flash-low")
    for engine in (gemini, ollama, antigravity):
        engine.close()


def test_an_engine_that_cannot_see_leaves_the_screen_work_to_the_local_one(monkeypatch, make_friend, wait_until):
    class Blind(FakeEngine):
        sees_images = False

    local, asked_for = FakeEngine(), []
    monkeypatch.setattr(app_module, "make_engine", lambda settings: asked_for.append(settings.engine) or local)

    friend = make_friend(Blind())

    # 그림을 읽지 못하는 엔진(agy)으로 대화하더라도, 화면 보기와 PC 조작은 내 PC의 Ollama가 맡는다.
    assert asked_for == ["ollama"] and friend._eyes is local
    assert wait_until(lambda: local.warmed == 1)  # 따로 도는 Ollama도 켤 때 미리 올려 둔다
    seeing = make_friend(FakeEngine())
    assert seeing._eyes is seeing._engine


def test_do_not_disturb_is_remembered(make_friend):
    friend = make_friend()

    friend.menu.do_not_disturb_toggled.emit(True)

    assert load_settings(paths.settings_path()).do_not_disturb is True


def test_where_the_panel_was_moved_and_how_big_it_was_made_are_remembered(make_friend):
    friend = make_friend()

    friend.stage.moved.emit(111, 222)
    friend.stage.resized.emit(333, 444)

    saved = load_settings(paths.settings_path())
    assert (saved.panel_position, saved.panel_size) == ((111, 222), (333, 444))

    friend.stage.docked.emit()  # 왼쪽 자리로 되돌리면 둘 다 잊는다

    saved = load_settings(paths.settings_path())
    assert (saved.panel_position, saved.panel_size) == (None, None)


def test_panel_opens_at_the_remembered_size(make_friend):
    friend = make_friend(settings=Settings(panel_size=(300, 500)))

    assert (friend.stage.panel.width(), friend.stage.panel.height()) == (300, 500)


def test_asking_for_a_write_up_makes_a_word_file_without_touching_the_screen(make_friend, wait_until, tmp_path):
    request = {"tool": "write_document", "topic": "API가 무엇인지 자세하게", "filename": "API 정리", "folder": "downloads"}
    engine = FakeEngine(plans=[{"actions": [request]}], replies=["API란\nAPI는 프로그램끼리의 약속이다."])
    friend = make_friend(engine)

    friend.stage.submitted.emit("API를 자세하게 다운로드에 워드 파일로 정리 해줘")

    assert wait_until(lambda: speech_of(friend).startswith("'API 정리.docx' 파일을 다운로드 폴더에 저장했어요."))
    assert (tmp_path / "downloads" / "API 정리.docx").exists()
    assert friend.agent.goals == []  # 화면을 조작하러 가지 않는다
    assert ("note", "문서 쓰는 중 · API 정리") in friend.stage.panel.log.entries()


def test_panel_opens_with_the_earlier_talk(make_friend, wait_until):
    first = make_friend(FakeEngine(replies=["안녕하세요!"]))
    first.stage.submitted.emit("안녕")
    assert wait_until(lambda: first.stage.speech == "안녕하세요!")
    first.shutdown()

    second = make_friend()

    assert second.stage.panel.log.entries()[:2] == [("user", "안녕"), ("assistant", "안녕하세요!")]


def test_chat_log_shows_the_talk_and_the_command_that_was_run(make_friend, monkeypatch, wait_until):
    monkeypatch.setattr(app_module.shell, "run", lambda command: CommandResult("메모.txt", 0))
    plan = {"actions": [{"tool": "run_command", "command": "Get-ChildItem $Desktop"}]}
    friend = make_friend(FakeEngine(plans=[plan], replies=["메모.txt가 있어요."]))

    friend.stage.submitted.emit("바탕화면에 뭐 있어?")

    said = [("user", "바탕화면에 뭐 있어?"), ("note", "명령 · Get-ChildItem $Desktop"), ("assistant", "메모.txt가 있어요.")]
    assert wait_until(lambda: friend.stage.panel.log.entries()[-3:] == said)


def test_panel_lists_and_status_follow_what_happens(make_friend, wait_until):
    actions = [{"tool": "add_todo", "text": "책 반납"}, {"tool": "add_alarm", "message": "약 먹기", "minutes_from_now": 10}]
    friend = make_friend(FakeEngine(plans=[{"actions": actions}]))
    assert "남은 할 일이 없어요" in friend.stage.panel.tasks.text
    assert "친밀도 레벨 1 · 다음 레벨까지 10" in friend.stage.panel._status.text()

    friend.stage.submitted.emit("할 일에 책 반납 적고 10분 뒤 약 먹으라고 해 줘")

    assert wait_until(lambda: "1. 책 반납" in friend.stage.panel.tasks.text and "약 먹기" in friend.stage.panel.tasks.text)
    assert "다음 레벨까지 9" in friend.stage.panel._status.text()
