import threading
from datetime import datetime, timedelta

import pytest

from assister.memory.store import MemoryStore, keywords

NOW = datetime(2026, 10, 6, 15, 0, 0)


@pytest.fixture
def store(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    yield memory
    memory.close()


def talk(store, *lines, start=NOW):
    """user, assistant 순서로 번갈아 대화를 쌓는다."""
    for index, line in enumerate(lines):
        store.add_message("user" if index % 2 == 0 else "assistant", line, start + timedelta(minutes=index))


def test_recent_messages_come_back_oldest_first(store):
    talk(store, "하나", "둘", "셋", "넷")

    assert [message.content for message in store.recent_messages(3)] == ["둘", "셋", "넷"]
    assert store.recent_messages(3)[0].role == "assistant"


def test_everything_is_kept_and_survives_reopening(tmp_path):
    path = tmp_path / "memory.db"
    first = MemoryStore(path)
    talk(first, *[f"말 {number}" for number in range(40)])
    first.add_fact("이름은 상민", NOW)
    first.close()

    second = MemoryStore(path)
    try:
        assert second.count_messages() == 40
        assert [fact.text for fact in second.list_facts()] == ["이름은 상민"]
    finally:
        second.close()


def test_search_finds_old_talk_even_when_particles_differ(store):
    talk(store, "내일 우유를 사야 해", "알겠어요, 우유 기억해 둘게요", "오늘 날씨 좋다", "그러게요")

    found = store.search("우유는 샀어?", exclude_recent=0)

    assert [message.content for message in found] == ["내일 우유를 사야 해", "알겠어요, 우유 기억해 둘게요"]


def test_search_skips_the_recent_window_already_in_context(store):
    talk(store, "예전에 고양이 얘기 했지", "네, 고양이 좋아하신다고", "요즘 고양이 사진 봤어", "귀엽죠")

    found = store.search("고양이 얘기", exclude_recent=2)

    assert [message.content for message in found] == ["예전에 고양이 얘기 했지", "네, 고양이 좋아하신다고"]


def test_search_prefers_messages_matching_more_words(store):
    talk(store, "제주도 여행 가고 싶다", "좋죠", "여행 얘기 그만", "네", "제주도 여행 숙소 예약했어", "잘됐네요")

    found = store.search("제주도 여행 숙소 어디였지", limit=1, exclude_recent=0)

    assert [message.content for message in found] == ["제주도 여행 숙소 예약했어"]


def test_search_with_nothing_to_look_for_returns_nothing(store):
    talk(store, "안녕", "안녕하세요")

    assert store.search("?", exclude_recent=0) == []
    assert store.search("없는말없는말", exclude_recent=0) == []


def test_same_fact_is_not_stored_twice(store):
    store.add_fact("매운 음식을 좋아함", NOW)
    store.add_fact("  매운 음식을 좋아함 ", NOW)

    assert len(store.list_facts()) == 1


def test_blank_message_or_fact_is_rejected(store):
    with pytest.raises(ValueError):
        store.add_message("user", "  ", NOW)
    with pytest.raises(ValueError):
        store.add_fact("", NOW)


def test_can_be_used_from_another_thread(store):
    errors = []

    def work():
        try:
            store.add_message("assistant", "다른 스레드에서 한 말", NOW)
        except Exception as error:  # noqa: BLE001 (테스트에서 스레드 오류를 모으기 위함)
            errors.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    thread.join()

    assert errors == []
    assert store.count_messages() == 1


def test_past_days_without_a_summary_are_found_and_summaries_are_kept(store):
    store.add_message("user", "그저께 한 말", NOW - timedelta(days=2))
    store.add_message("user", "어제 한 말", NOW - timedelta(days=1))
    store.add_message("assistant", "어제 한 답", NOW - timedelta(days=1))
    store.add_message("user", "오늘 한 말", NOW)

    days = store.days_to_summarise(NOW)
    assert days == [(NOW - timedelta(days=2)).date(), (NOW - timedelta(days=1)).date()]  # 오늘은 아직 끝나지 않았다
    assert [m.content for m in store.messages_on(days[1])] == ["어제 한 말", "어제 한 답"]

    store.add_summary(days[1], "어제는 인사를 나눴다.")

    assert store.days_to_summarise(NOW) == [days[0]]
    assert store.recent_summaries(3) == [(days[1], "어제는 인사를 나눴다.")]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("우유는 샀어?", [("우유는", "우유"), ("샀어",)]),
        ("제주도 여행", [("제주도", "제주"), ("여행",)]),
        ("그 회의에서", [("회의에서", "회의에", "회의")]),
        ("?!", []),
    ],
)
def test_keywords_strip_endings(query, expected):
    assert keywords(query) == expected
