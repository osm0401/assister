import base64
import io
from datetime import datetime, timedelta

import pytest
from PIL import Image

from assister.ai.engine import EngineError
from assister.memory.store import MemoryStore
from assister.screen import capture
from assister.screen.capture import take_snapshot
from assister.screen.privacy import DEFAULT_SENSITIVE, Window, is_hidden, mask
from assister.screen.watcher import ScreenWatcher

NOW = datetime(2026, 10, 6, 15, 0, 0)

# ---------- 무엇을 가릴지 ----------


@pytest.mark.parametrize(
    "window",
    [
        Window("KB국민은행 - Chrome", "chrome.exe", (0, 0, 10, 10)),
        Window("민지", "KakaoTalk.exe", (0, 0, 10, 10)),  # 제목이 대화 상대 이름뿐이어도 프로그램 이름으로 잡는다
        Window("로그인 - 네이버", "msedge.exe", (0, 0, 10, 10)),
        Window("새 InPrivate 탭", "msedge.exe", (0, 0, 10, 10)),
        Window("1Password", "1Password.exe", (0, 0, 10, 10)),
    ],
)
def test_sensitive_windows_are_hidden_by_default(window):
    assert is_hidden(window, DEFAULT_SENSITIVE)


@pytest.mark.parametrize("title", ["카카오페이 - Chrome", "신한카드 결제내역", "토스증권", "Toss"])
def test_payment_sites_are_hidden(title):
    assert is_hidden(Window(title, "chrome.exe", (0, 0, 10, 10)), DEFAULT_SENSITIVE)


@pytest.mark.parametrize("title", ["새 페이지 - Edge", "그래픽카드 추천 - Chrome", "토스트 맛있게 굽는 법", "카드뉴스 만들기"])
def test_lookalike_words_do_not_hide_ordinary_pages(title):
    """'페이'가 '페이지'에, '카드'가 '그래픽카드'에 걸리면 화면을 거의 못 본다."""
    assert not is_hidden(Window(title, "msedge.exe", (0, 0, 10, 10)), DEFAULT_SENSITIVE)


def test_ordinary_windows_are_not_hidden_unless_the_user_blocks_them():
    game = Window("Stardew Valley", "Stardew Valley.exe", (0, 0, 10, 10))

    assert not is_hidden(game, DEFAULT_SENSITIVE)
    assert is_hidden(game, (*DEFAULT_SENSITIVE, "stardew"))


def test_mask_blacks_out_only_hidden_windows():
    image = Image.new("RGB", (400, 200), "white")
    bank = Window("우리은행", "chrome.exe", (-100, 20, 100, 120))  # 왼쪽 모니터에 걸친 창
    notes = Window("메모장", "notepad.exe", (150, 20, 300, 120))

    masked, hidden = mask(image, [bank, notes], DEFAULT_SENSITIVE, origin=(-200, 0))

    assert hidden == [bank]
    assert masked.getpixel((150, 60)) == (0, 0, 0)  # 은행 창 안 (화면 좌표 -50 → 그림 좌표 150)
    assert masked.getpixel((400 - 10, 60)) == (255, 255, 255)  # 메모장은 그대로
    assert image.getpixel((150, 60)) == (255, 255, 255)  # 원본은 건드리지 않는다


# ---------- 화면 찍기 ----------


class FakeDesktop:
    """모니터 두 대(왼쪽 보조, 오른쪽 주)짜리 가짜 바탕화면."""

    def __init__(self, windows, foreground=None, typing_password=False):
        self._windows = windows
        self._foreground = foreground
        self._typing_password = typing_password
        self.grabs = 0

    def password_focused(self):
        return self._typing_password

    def list_windows(self):
        return self._windows

    def foreground_window(self):
        return self._foreground

    def monitor_rects(self):
        return [(0, 0, 1920, 1200), (-1920, 0, 0, 1080)]

    def grab(self):
        self.grabs += 1
        return Image.new("RGB", (3840, 1200), "white"), (-1920, 0)


def decode(image_b64: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(image_b64)))


def test_snapshot_has_one_downscaled_image_per_monitor_with_hidden_windows_blacked_out():
    bank = Window("신한은행", "chrome.exe", (-1900, 100, -1000, 900))
    editor = Window("main.py - VS Code", "Code.exe", (0, 0, 1920, 1200))
    desktop = FakeDesktop([editor, bank], foreground=editor)

    snapshot = take_snapshot(DEFAULT_SENSITIVE, 960, desktop)

    assert not snapshot.skipped
    assert snapshot.foreground == editor
    assert snapshot.hidden == ["신한은행"]
    main, sub = (decode(image) for image in snapshot.images)
    assert main.size == (960, 600) and sub.size == (960, 540)
    assert max(sub.getpixel((300, 250))) < 30  # 보조 모니터의 은행 창은 검게
    assert min(main.getpixel((480, 300))) > 220  # 주 모니터는 그대로


def test_snapshot_carries_a_small_fingerprint_of_how_the_screen_looks():
    editor = Window("main.py - VS Code", "Code.exe", (0, 0, 1920, 1200))

    first = take_snapshot(DEFAULT_SENSITIVE, 960, FakeDesktop([editor], foreground=editor))
    second = take_snapshot(DEFAULT_SENSITIVE, 960, FakeDesktop([editor], foreground=editor))

    assert len(first.look) == 2 * capture.LOOK_SIZE[0] * capture.LOOK_SIZE[1]  # 모니터 두 대
    assert first.look == second.look


def test_snapshot_also_carries_a_close_up_of_the_window_in_use():
    calculator = Window("계산기", "CalculatorApp.exe", (100, 200, 420, 700))
    desktop = FakeDesktop([calculator], foreground=calculator)

    snapshot = take_snapshot(DEFAULT_SENSITIVE, 960, desktop)

    assert snapshot.focus_rect == (100, 200, 420, 700)
    close_up = decode(snapshot.focus)
    # 작은 창은 글자가 잘 보이게 키워서 보낸다. 비율은 그대로다.
    assert max(close_up.size) >= capture.FOCUS_MIN_SIDE
    assert close_up.width / close_up.height == pytest.approx(320 / 500, abs=0.02)


def test_close_up_is_clipped_to_the_screen_and_skipped_for_slivers():
    half_off = Window("메모장", "notepad.exe", (1700, 900, 2300, 1500))  # 오른쪽 아래로 삐져나간 창
    snapshot = take_snapshot(DEFAULT_SENSITIVE, 960, FakeDesktop([half_off], foreground=half_off))
    assert snapshot.focus_rect == (1700, 900, 1920, 1200)

    sliver = Window("알림", "x.exe", (10, 10, 60, 40))
    snapshot = take_snapshot(DEFAULT_SENSITIVE, 960, FakeDesktop([sliver], foreground=sliver))
    assert snapshot.focus == "" and snapshot.focus_rect is None


@pytest.mark.parametrize(
    ("window", "expected"),
    [
        (Window("Ready Or Not", "ReadyOrNot.exe", (0, 0, 1920, 1200)), True),  # 주 모니터를 꽉 채운 게임
        (Window("영상", "vlc.exe", (-1920, 0, 0, 1080)), True),  # 보조 모니터를 꽉 채운 영상
        (Window("main.py - VS Code", "Code.exe", (-8, -8, 1928, 1168)), False),  # 최대화한 창은 작업 표시줄을 남긴다
        (Window("메모장", "notepad.exe", (100, 100, 900, 700)), False),
        (Window("Program Manager", "explorer.exe", (0, 0, 1920, 1200)), False),  # 바탕화면 자체
    ],
)
def test_snapshot_tells_whether_something_fills_a_whole_monitor(window, expected):
    snapshot = take_snapshot(DEFAULT_SENSITIVE, 960, FakeDesktop([window], foreground=window))

    assert snapshot.fullscreen is expected


def test_nothing_is_captured_while_a_hidden_window_is_in_front():
    chat = Window("민지", "KakaoTalk.exe", (0, 0, 500, 800))
    desktop = FakeDesktop([chat], foreground=chat)

    snapshot = take_snapshot(DEFAULT_SENSITIVE, 960, desktop)

    assert snapshot.skipped and snapshot.images == []
    assert desktop.grabs == 0


def test_nothing_is_captured_while_typing_in_a_password_field():
    """창 제목에 '로그인'이 없어도, 커서가 비밀번호 입력란에 있으면 찍지 않는다."""
    page = Window("우리 회사 포털", "chrome.exe", (0, 0, 1920, 1200))
    desktop = FakeDesktop([page], foreground=page, typing_password=True)

    snapshot = take_snapshot(DEFAULT_SENSITIVE, 960, desktop)

    assert snapshot.skipped and desktop.grabs == 0


def test_real_focus_can_be_asked_without_crashing():
    assert capture.password_focused() in (True, False)


def test_real_desktop_can_be_listed():
    """이 PC의 실제 창과 모니터를 읽을 수 있는지만 본다. 화면은 찍지 않는다."""
    assert len(capture.monitor_rects()) >= 1
    windows = capture.list_windows()
    assert all(isinstance(window, Window) for window in windows)
    assert all(window.process for window in windows)


# ---------- 화면 기록 ----------


@pytest.fixture
def memory(tmp_path):
    store = MemoryStore(tmp_path / "memory.db")
    yield store
    store.close()


def test_screen_notes_are_kept_in_order_and_old_ones_are_purged(memory):
    memory.add_screen_note("메일을 쓰는 중", "outlook.exe", NOW - timedelta(days=8))
    memory.add_screen_note("코드를 고치는 중", "Code.exe", NOW - timedelta(minutes=5))
    memory.add_screen_note("영상을 보는 중", "chrome.exe", NOW)

    assert [note.summary for note in memory.recent_screen_notes(2)] == ["코드를 고치는 중", "영상을 보는 중"]

    assert memory.purge_screen_notes(NOW - timedelta(days=7)) == 1
    assert [note.app for note in memory.recent_screen_notes(10)] == ["Code.exe", "chrome.exe"]


# ---------- 지켜보기 ----------


class FakeEngine:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[list[dict]] = []

    def complete_json(self, messages, schema):
        self.calls.append(messages)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def snapshot_of(*, skipped=False, look=b"", fullscreen=False):
    editor = Window("main.py - VS Code", "Code.exe", (0, 0, 10, 10))
    return capture.Snapshot(
        images=[] if skipped else ["aW1n"], foreground=editor, hidden=[], skipped=skipped, look=look, fullscreen=fullscreen
    )


@pytest.fixture
def watcher_with(qapp, memory):
    def make(engine, snapshot):
        watcher = ScreenWatcher(
            engine,
            memory,
            interval_sec=30,
            retention_days=7,
            keywords=lambda: DEFAULT_SENSITIVE,
            width=960,
            take_snapshot=lambda keywords, width: snapshot,
            clock=lambda: NOW,
        )
        seen = {"noted": [], "states": [], "failed": []}
        watcher.noted.connect(lambda summary, offer: seen["noted"].append((summary, offer)))
        watcher.state_changed.connect(seen["states"].append)
        watcher.failed.connect(seen["failed"].append)
        return watcher, seen

    return make


def test_each_look_is_summarised_stored_as_text_and_old_notes_dropped(watcher_with, memory):
    memory.add_screen_note("지난주 화면", "x.exe", NOW - timedelta(days=9))
    engine = FakeEngine({"summary": "VS Code에서 파이썬 코드를 고치는 중", "offer": "오류 같이 볼까요?"})
    watcher, seen = watcher_with(engine, snapshot_of())

    watcher.tick()

    assert seen["noted"] == [("VS Code에서 파이썬 코드를 고치는 중", "오류 같이 볼까요?")]
    assert seen["states"] == ["watching"]
    assert [(note.summary, note.app) for note in memory.recent_screen_notes(5)] == [
        ("VS Code에서 파이썬 코드를 고치는 중", "Code.exe")
    ]
    sent = engine.calls[0][-1]
    assert sent["images"] == ["aW1n"] and "main.py - VS Code" in sent["content"]


def test_unchanged_screen_is_not_analysed_again(watcher_with, memory):
    """자리를 비워 화면이 그대로일 때, 30초마다 같은 그림을 분석하느라 그래픽카드를 돌리지 않는다."""
    engine = FakeEngine({"summary": "문서를 읽는 중", "offer": ""}, {"summary": "영상을 보는 중", "offer": ""})
    same = snapshot_of(look=bytes([10] * 64))
    watcher, seen = watcher_with(engine, same)

    watcher.tick()
    watcher.tick()
    watcher.tick()
    assert len(engine.calls) == 1 and len(memory.recent_screen_notes(5)) == 1

    # 시계 숫자가 바뀐 정도의 작은 차이도 같은 화면으로 본다.
    watcher._take_snapshot = lambda keywords, width: snapshot_of(look=bytes([10] * 63 + [14]))
    watcher.tick()
    assert len(engine.calls) == 1

    watcher._take_snapshot = lambda keywords, width: snapshot_of(look=bytes([200] * 64))
    watcher.tick()
    assert len(engine.calls) == 2 and seen["noted"][-1] == ("영상을 보는 중", "")


def test_watcher_rests_while_a_game_or_video_fills_the_screen(watcher_with, memory):
    """게임 중에 30초마다 그래픽카드를 나눠 쓰면 게임이 끊긴다."""
    engine = FakeEngine({"summary": "게임을 하는 중", "offer": ""})
    watcher, seen = watcher_with(engine, snapshot_of(fullscreen=True))

    watcher.tick()
    assert engine.calls == [] and memory.recent_screen_notes(5) == []
    assert seen["states"] == ["resting"]

    watcher.rest_when_fullscreen = False
    watcher.tick()
    assert len(engine.calls) == 1 and seen["states"] == ["resting", "watching"]


def test_hidden_screen_is_neither_sent_to_the_ai_nor_recorded(watcher_with, memory):
    engine = FakeEngine()
    watcher, seen = watcher_with(engine, snapshot_of(skipped=True))

    watcher.tick()

    assert engine.calls == [] and memory.recent_screen_notes(5) == []
    assert seen["states"] == ["hidden"] and seen["noted"] == []


def test_ai_failure_is_reported_once_not_every_look(watcher_with):
    engine = FakeEngine(EngineError("Ollama에 연결할 수 없습니다."), EngineError("또 실패"), {"summary": "웹 서핑", "offer": ""})
    watcher, seen = watcher_with(engine, snapshot_of())

    watcher.tick()
    watcher.tick()
    watcher.tick()

    assert seen["failed"] == ["Ollama에 연결할 수 없습니다."]
    assert seen["noted"] == [("웹 서핑", "")]


def test_malformed_answer_is_ignored(watcher_with, memory):
    watcher, seen = watcher_with(FakeEngine(["요약"], {"summary": 3}), snapshot_of())

    watcher.tick()
    watcher.tick()

    assert memory.recent_screen_notes(5) == [] and seen["noted"] == []


def test_turned_off_watcher_does_not_look(watcher_with):
    engine = FakeEngine({"summary": "x", "offer": ""})
    watcher, seen = watcher_with(engine, snapshot_of())

    watcher.set_enabled(False)

    assert not watcher.should_look(busy=False)
    assert seen["states"] == ["off"]
    watcher.set_enabled(True)
    assert watcher.should_look(busy=False) and not watcher.should_look(busy=True)
