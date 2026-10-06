from datetime import datetime

import pytest

from assister.character.art import generate_placeholder_pack
from assister.character.pack import load_pack
from assister.growth.bond import (
    Mood,
    idle_expression,
    level_for,
    mood_at,
    newly_unlocked,
    points_to_next,
    reaction,
    tone_note,
)
from assister.memory.store import MemoryStore


@pytest.mark.parametrize(("points", "level"), [(0, 1), (9, 1), (10, 2), (29, 2), (30, 3), (70, 4), (150, 5), (9999, 6)])
def test_level_rises_with_talk(points, level):
    assert level_for(points) == level


def test_points_to_next_level_and_nothing_left_at_the_top():
    assert points_to_next(0) == 10
    assert points_to_next(25) == 5
    assert points_to_next(9999) is None


@pytest.mark.parametrize(
    ("hour", "talks_today", "expected"),
    [
        (3, 0, Mood.SLEEPY),  # 새벽
        (3, 30, Mood.SLEEPY),  # 많이 얘기해도 새벽에는 졸리다
        (10, 0, Mood.CALM),
        (10, 12, Mood.HAPPY),  # 오늘 얘기를 많이 했다
        (23, 2, Mood.SLEEPY),
    ],
)
def test_mood_follows_the_time_of_day_and_how_much_we_talked(hour, talks_today, expected):
    assert mood_at(datetime(2026, 10, 6, hour, 0), talks_today) == expected


def test_idle_face_shows_the_mood():
    assert idle_expression(Mood.SLEEPY) == "sleepy"
    assert idle_expression(Mood.HAPPY) == "smile"
    assert idle_expression(Mood.CALM) == "neutral"


@pytest.mark.parametrize(
    ("said", "face"),
    [
        ("정말 축하해요! 대단해요!", "excited"),
        ("좋아요, 같이 해 봐요.", "smile"),
        ("고마워요, 저도 반가워요.", "smile"),
        ("미안해요, 그건 못 해요.", "sad"),
        ("헉, 정말요?", "surprised"),
        ("음… 글쎄요, 잘 모르겠어요.", "thinking"),
        ("하암, 저도 졸려요.", "sleepy"),
        ("오후 3:10에 '빨래' 알려 줄게요.", None),
    ],
)
def test_face_follows_what_the_character_just_said(said, face):
    assert reaction(said) == face


def test_tone_note_tells_the_ai_how_close_we_are_and_how_it_feels():
    early = tone_note(level=1, mood=Mood.CALM)
    close = tone_note(level=5, mood=Mood.SLEEPY)

    assert "레벨 1" in early and "레벨 5" in close
    assert early != close
    assert "졸" in close


def test_level_up_unlocks_outfits_and_props(tmp_path):
    pack = load_pack(generate_placeholder_pack(tmp_path / "default"))

    assert newly_unlocked(pack, old_level=1, new_level=1) == []
    assert newly_unlocked(pack, old_level=1, new_level=2) == ["하늘 가디건", "리본"]
    assert newly_unlocked(pack, old_level=2, new_level=5) == ["분홍 원피스", "안경", "헤드폰"]


def test_state_values_survive_reopening(tmp_path):
    path = tmp_path / "memory.db"
    first = MemoryStore(path)
    assert first.get_state("bond_points", 0) == 0
    first.set_state("bond_points", 12)
    first.close()

    second = MemoryStore(path)
    try:
        assert second.get_state("bond_points", 0) == 12
    finally:
        second.close()


def test_talks_today_counts_only_what_the_user_said_today(tmp_path):
    memory = MemoryStore(tmp_path / "memory.db")
    try:
        memory.add_message("user", "어제 한 말", datetime(2026, 10, 5, 23, 0))
        memory.add_message("user", "오늘 한 말", datetime(2026, 10, 6, 9, 0))
        memory.add_message("assistant", "캐릭터의 답", datetime(2026, 10, 6, 9, 0))

        assert memory.count_user_messages_on(datetime(2026, 10, 6, 15, 0)) == 1
    finally:
        memory.close()
