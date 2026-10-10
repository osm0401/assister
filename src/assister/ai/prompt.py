"""AI에게 줄 지시문을 만든다.

한 마디에 AI를 두 번 부른다. 먼저 '어떤 작업이 필요한지'만 정해진 틀로 고르게 하고(planner),
그 작업을 실제로 한 뒤, 결과를 보여 주며 답을 쓰게 한다(system). 작은 모델은 답을 쓰면서
동시에 도구를 부르는 일을 자주 빠뜨려서 이렇게 나눴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

WEEKDAYS = ("월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일")
CALENDAR_DAYS = 14
SPEAKERS = {"user": "사용자", "assistant": "캐릭터"}

BASE = """너는 '{name}'야. 사용자의 Windows 바탕화면에 떠 있는 캐릭터로, 사용자와 이야기하고 할 일과 알람을 챙겨 준다.

- 한국어로 답한다.
- 답은 작은 말풍선에 뜨고 목소리로도 읽힌다. 한두 문장으로 짧게 말하고, 꼭 필요할 때만 길게 말한다.
- 굵은 글씨, 제목, 글머리표 같은 꾸밈 기호는 쓰지 않는다.
- 말투는 대화 내내 한 가지로 유지한다.
- 알람, 할 일, 기억을 실제로 바꾸는 일은 네가 답하기 전에 이미 처리되고, 그 결과가 맨 아래 참고 메모에 적혀 있다. 메모에 없는 일은 해 뒀다거나 해 주겠다고 말하지 않는다.
- 참고 메모는 너만 보는 것이다. 메모가 있다는 사실이나 메모의 문구를 사용자에게 그대로 옮기지 않고, 결과만 자연스럽게 말한다.
- 알람 시각을 고치는 일과 할 일을 지우는 일은 아직 할 수 없다. 부탁받으면 못 한다고 말한다.
- 모르는 것은 모른다고 말한다.

지금 시각: {now}"""

PLANNER = """사용자가 방금 한 말을 처리하려면 어떤 작업이 필요한지 고른다. 대답은 하지 않고 작업 목록만 JSON으로 낸다.

- 필요한 작업이 없으면 actions를 빈 목록으로 둔다. 그냥 하는 말, 질문, 인사, 감탄에는 작업이 필요 없다.
- 사용자가 자기 자신에 대해 알려 주는 말(이름, 나이, 직업, 가족, 반려동물, 좋아하거나 싫어하는 것, 습관)은 remember로 적는다. 사실 하나에 작업 하나씩 만든다.
- 사용자가 자기에 대해 묻는 말("내 이름 기억해?")은 알려 주는 말이 아니니 remember를 쓰지 않는다.
- "다 했어", "끝냈어"는 할 일을 끝냈다는 말이다(complete_todo). cancel_alarm은 사용자가 알람을 취소하거나 지워 달라고 분명히 말했을 때만 쓴다.
- 작업마다 필요한 칸만 채운다.
- 번호가 필요한 작업은 아래 [지금 남은 할 일], [지금 울릴 알람]에 적힌 번호를 쓴다. 사용자가 이름으로 말하면 맞는 번호를 찾아 쓴다.
- 아래 [직전 대화]는 참고용이다. 거기 있는 말은 이미 처리했으니 다시 처리하지 않는다.

쓸 수 있는 작업:
{tools}

지금 시각: {now}"""

WRITER = """아래 주제로 문서에 들어갈 글을 한국어로 쓴다.

- 첫 줄은 제목이다.
- 내용을 몇 덩어리로 나누고, 덩어리마다 소제목을 단다. 소제목 줄은 반드시 '# '로 시작한다.
- 목록은 줄 맨 앞에 '- '를 붙인다.
- 굵은 글씨나 표 같은 다른 꾸밈 기호는 쓰지 않는다.
- 글만 쓴다. 인사말이나 '알겠습니다' 같은 말은 붙이지 않는다.

모양은 이렇게 한다.
제목
# 소제목
본문 문단
- 목록
# 소제목
본문 문단

주제: {topic}"""

NOTHING_DONE = (
    "(참고 메모: 이번 말로는 알람, 할 일, 기억을 바꾸는 작업을 하지 않았다. "
    "사용자가 그런 일을 부탁한 것이 아니면 이 메모는 무시하고 평소처럼 이야기한다. "
    "부탁했다면 아직 하지 못했다고 말하고 다시 말해 달라고 한다.)"
)
RESULT_HEADER = "(참고 메모: 아래는 방금 처리한 결과로, 사용자에게는 아직 알리지 않았다. 하지 못한 일이 있으면 사실대로 알려 준다.)"


def format_now(now: datetime) -> str:
    period = "오전" if now.hour < 12 else "오후"
    return (
        f"{now.year}년 {now.month}월 {now.day}일 {WEEKDAYS[now.weekday()]} "
        f"{period} {now.hour % 12 or 12}:{now.minute:02d}"
    )


def build_system_prompt(name: str, *, persona: str, facts: Sequence[str], now: datetime) -> str:
    """말투 메모가 비어 있으면 말투를 정해 주지 않고 AI 엔진 본래의 성격에 맡긴다."""
    sections = [BASE.format(name=name, now=format_now(now))]
    if persona.strip():
        sections.append(f"[말투 메모]\n{persona.strip()}")
    if facts:
        sections.append("[사용자에 대해 기억하는 것]\n" + "\n".join(f"- {fact}" for fact in facts))
    return "\n\n".join(sections)


def outcome_section(results: Sequence[str]) -> str:
    """방금 처리한 결과(새로 기억한 사실, 하지 못한 일)를 답하는 AI에게 알려 주는 글."""
    if not results:
        return NOTHING_DONE
    return RESULT_HEADER + "\n" + "\n".join(f"- {result}" for result in results)


def _describe(schema: dict) -> str:
    function = schema["function"]
    fields = [
        f"{field}({info.get('description', '')})" for field, info in function["parameters"]["properties"].items()
    ]
    return f"- {function['name']}: {function['description']} 칸: {', '.join(fields) or '없음'}"


def _calendar(now: datetime) -> str:
    """앞으로 2주의 날짜와 요일. '다음 주 화요일' 같은 말을 AI가 계산하지 않고 찾아 쓰게 한다."""
    days = [now + timedelta(days=offset) for offset in range(CALENDAR_DAYS)]
    lines = [f"{day:%Y-%m-%d} {WEEKDAYS[day.weekday()]}{'(오늘)' if index == 0 else ''}" for index, day in enumerate(days)]
    return "[달력]\n" + "\n".join(lines)


def build_planner_prompt(
    schemas: Sequence[dict], *, recent: Sequence[tuple[str, str]], state: str, now: datetime
) -> str:
    """state는 지금 남은 할 일과 알람을 번호와 함께 적은 글이다."""
    sections = [
        PLANNER.format(tools="\n".join(_describe(schema) for schema in schemas), now=format_now(now)),
        _calendar(now),
        state,
    ]
    if recent:
        lines = [f"{SPEAKERS.get(role, role)}: {content}" for role, content in recent]
        sections.append("[직전 대화]\n" + "\n".join(lines))
    return "\n\n".join(sections)
