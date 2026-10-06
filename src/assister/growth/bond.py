"""오래 지내면 달라지는 것: 친밀도 레벨, 기분, 열리는 옷과 소품. 전부 계산만 한다."""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from assister.character.pack import CharacterPack

# 사용자가 한 마디 할 때마다 1점. 이 점수를 넘으면 다음 레벨이다.
LEVEL_POINTS = (0, 10, 30, 70, 150, 300)
SLEEPY_HOURS = (*range(0, 6), 23)
HAPPY_TALKS = 10

CLOSENESS = (
    "아직 서로 알아 가는 사이다.",
    "조금 익숙해진 사이다.",
    "편하게 이야기하는 사이다.",
    "가까운 사이다. 농담도 편하게 한다.",
    "아주 가까운 사이다. 오랜 친구처럼 대한다.",
    "누구보다 가까운 사이다. 오랜 친구처럼 대한다.",
)


class Mood(Enum):
    CALM = "평온함"
    HAPPY = "기분 좋음"
    SLEEPY = "졸림"


def level_for(points: int) -> int:
    return sum(points >= needed for needed in LEVEL_POINTS)


def points_to_next(points: int) -> int | None:
    """다음 레벨까지 남은 점수. 가장 높은 레벨이면 None."""
    return next((needed - points for needed in LEVEL_POINTS if needed > points), None)


def mood_at(now: datetime, talks_today: int) -> Mood:
    """하루 흐름에 따른 기분. 밤늦게는 졸리고, 오늘 얘기를 많이 했으면 기분이 좋다."""
    if now.hour in SLEEPY_HOURS:
        return Mood.SLEEPY
    return Mood.HAPPY if talks_today >= HAPPY_TALKS else Mood.CALM


def idle_expression(mood: Mood) -> str:
    """말하지 않을 때의 표정."""
    return {Mood.SLEEPY: "sleepy", Mood.HAPPY: "smile"}.get(mood, "neutral")


# 방금 한 말에 어울리는 표정. 위에서부터 먼저 맞는 것을 쓴다.
# ponytail: 낱말만 본다. 반어나 긴 문맥은 못 읽는다. 어색하면 AI에게 표정까지 고르게 바꾼다.
REACTIONS = (
    ("sad", ("미안", "죄송", "아쉽", "속상", "안타깝", "힘들었")),
    ("surprised", ("헉", "정말요?", "진짜요?", "세상에", "깜짝")),
    ("excited", ("축하", "대단", "대박", "최고", "신나", "멋져")),
    ("sleepy", ("하암", "졸려", "졸리")),
    ("thinking", ("글쎄", "모르겠", "음…", "음...", "고민")),
    ("smile", ("좋아", "고마", "반가", "다행", "잘했", "ㅎㅎ", "재밌", "귀엽")),
)


def reaction(text: str) -> str | None:
    """캐릭터가 방금 한 말에 어울리는 표정. 딱히 없으면 None."""
    return next((face for face, words in REACTIONS if any(word in text for word in words)), None)


def tone_note(*, level: int, mood: Mood) -> str:
    """AI에게 주는 말투 귀띔. 말투를 정해 주지는 않고, 얼마나 가까운지와 지금 기분만 알려 준다."""
    closeness = CLOSENESS[min(level, len(CLOSENESS)) - 1]
    return f"[사용자와의 사이] 친밀도 레벨 {level}. {closeness} 가까울수록 더 편하게 말한다.\n[지금 기분] {mood.value}"


def newly_unlocked(pack: CharacterPack, *, old_level: int, new_level: int) -> list[str]:
    """레벨이 오르며 새로 열린 옷과 소품의 이름."""
    items = [*pack.outfits, *pack.props]
    return [item.label for item in items if old_level < item.unlock_level <= new_level]
