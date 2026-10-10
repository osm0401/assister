"""사용자의 말 한 마디에 답을 만드는 과정.

1. 필요한 작업을 고른다 (AI, 정해진 틀로)
2. 작업을 실제로 한다
3. 알람·할 일을 처리했으면 그 확인 문장으로 답하고, 아니면 AI가 답을 쓴다
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import datetime

from assister.ai.engine import ChatEngine
from assister.ai.prompt import build_planner_prompt, build_system_prompt, outcome_section
from assister.ai.tools import ACTION_FORMAT, Outcome, ToolBox, action_note
from assister.memory.store import MemoryStore, Message
from assister.tasks.commands import spoken_clock, spoken_day_offset
from assister.tasks.service import format_time

log = logging.getLogger(__name__)

FALLBACK_REPLY = "음, 뭐라고 답해야 할지 모르겠어요. 다시 말해 줄래요?"
DEFAULT_HISTORY_LIMIT = 12
PLANNER_HISTORY_LIMIT = 4
RECALL_LIMIT = 4
SCREEN_NOTES_LIMIT = 3
MAX_ACTIONS = 5
SUMMARY_DAYS = 3  # 답할 때 보여 줄 지난 날 요약의 수
MAX_DIGEST_DAYS = 3  # 한 번 켤 때 요약할 날 수. 오래 꺼 뒀다 켜도 한참 걸리지 않게 한다.
MAX_DIGEST_CHARS = 6000
SPEAKERS = {"user": "사용자", "assistant": "캐릭터"}
DIGEST_PROMPT = """아래는 {month}월 {day}일에 사용자와 캐릭터가 나눈 대화다. 나중에 떠올릴 수 있게 한국어 두세 문장으로 줄인다.
사용자가 알려 준 사실, 세운 계획, 그날의 기분을 중심으로 적고, 인사말 같은 것은 뺀다. 요약만 쓴다.

{talk}"""
NEEDS_CANCEL_WORD = frozenset({"cancel_alarm"})
CANCEL_WORD = re.compile(r"취소|지워|지우|삭제|없애|꺼\s?줘|끄|cancel", re.IGNORECASE)
# '10분 뒤', '두 시간 후', '30분 있다가'처럼 지금부터 얼마 뒤인지를 말하는 표현.
RELATIVE_TIME = re.compile(r"(?:초|분|시간)\s*(?:뒤|후|있다|이따)")


def _recalled(messages: list[Message]) -> str:
    speakers = {"user": "사용자", "assistant": "나"}
    lines = [
        f"- {m.created_at.month}월 {m.created_at.day}일 {speakers.get(m.role, m.role)}: {m.content}" for m in messages
    ]
    return "[지금 하는 말과 관련 있어 보이는 예전 대화]\n" + "\n".join(lines)


class ChatSession:
    def __init__(
        self,
        engine: ChatEngine,
        memory: MemoryStore,
        toolbox: ToolBox,
        *,
        character_name: str,
        persona: str = "",
        clock: Callable[[], datetime] = datetime.now,
        history_limit: int = DEFAULT_HISTORY_LIMIT,
        extra_context: Callable[[], str] | None = None,
    ) -> None:
        """extra_context는 답할 때마다 지시문에 덧붙일 글을 돌려준다 (친밀도, 기분 같은 것)."""
        self._extra_context = extra_context
        self._engine = engine
        self._memory = memory
        self._toolbox = toolbox
        self._name = character_name
        self._persona = persona
        self._clock = clock
        self._history_limit = history_limit

    def warm_up(self) -> None:
        """첫 답이 늦지 않게 AI 모델을 미리 올려 둔다."""
        self._engine.warm_up()

    def digest(self) -> int:
        """아직 요약하지 않은 지난 날의 대화를 날마다 몇 문장으로 줄여 둔다. 요약한 날 수를 돌려준다.

        대화는 전부 남아 있지만 AI에게는 최근 몇 마디만 보여 주므로, 며칠 전 이야기는 이 요약으로 잇는다.
        """
        days = self._memory.days_to_summarise(self._clock())[-MAX_DIGEST_DAYS:]
        for day in days:
            lines = [f"{SPEAKERS.get(m.role, m.role)}: {m.content}" for m in self._memory.messages_on(day)]
            prompt = DIGEST_PROMPT.format(month=day.month, day=day.day, talk="\n".join(lines)[-MAX_DIGEST_CHARS:])
            summary = self._engine.chat([{"role": "user", "content": prompt}]).strip()
            if summary:
                self._memory.add_summary(day, summary)
        return len(days)

    def reply(
        self,
        user_text: str,
        *,
        on_delta: Callable[[str], None] | None = None,
        on_tool: Callable[[str], None] | None = None,
        on_action: Callable[[str], None] | None = None,
        should_stop: Callable[[], bool] | None = None,
    ) -> str:
        """답을 만들어 돌려준다. 엔진이 실패하면 EngineError가 그대로 올라간다.

        on_action은 실행한 명령이나 검색어를 사용자에게 보여 줄 한 줄로 받는다.
        """
        now = self._clock()
        history = self._memory.recent_messages(self._history_limit)
        related = self._memory.search(user_text, limit=RECALL_LIMIT, exclude_recent=self._history_limit)
        # 엔진이 실패해도 사용자가 한 말은 남도록 먼저 기억한다.
        self._memory.add_message("user", user_text, now)

        # 이번에 새로 기억하는 사실은 '방금 알게 된 것'으로 따로 알려 주므로, 그 전에 알던 것만 읽어 둔다.
        facts = [fact.text for fact in self._memory.list_facts()]
        outcomes = self._act(user_text, history, now, on_tool, on_action)

        confirmations = [outcome.text for outcome in outcomes if outcome.shown]
        if confirmations:
            # 알람·할 일을 처리했으면 실제 값으로 만든 확인 문장만 보여 준다.
            # AI에게 다시 말하게 하면 시각을 틀리게 옮기는 일이 잦았다.
            text = " ".join(confirmations)
            if on_delta:
                on_delta(text)
        else:
            results = [outcome.text if outcome.ok else f"하지 못함: {outcome.text}" for outcome in outcomes]
            sections = [build_system_prompt(self._name, persona=self._persona, facts=facts, now=now)]
            if self._extra_context and (extra := self._extra_context()):
                sections.append(extra)
            if summaries := self._memory.recent_summaries(SUMMARY_DAYS):
                lines = [f"- {day.month}월 {day.day}일: {summary}" for day, summary in summaries]
                sections.append("[지난 며칠 동안 나눈 대화]\n" + "\n".join(lines))
            if related:
                sections.append(_recalled(related))
            if notes := self._memory.recent_screen_notes(SCREEN_NOTES_LIMIT):
                lines = [f"- {format_time(note.created_at, now)} {note.summary}" for note in notes]
                sections.append("[최근에 본 사용자의 화면]\n" + "\n".join(lines))
            sections.append(outcome_section(results))
            messages = [
                {"role": "system", "content": "\n\n".join(sections)},
                *({"role": message.role, "content": message.content} for message in history),
                {"role": "user", "content": user_text},
            ]
            text = self._engine.chat(messages, on_delta=on_delta, should_stop=should_stop).strip() or FALLBACK_REPLY

        self._memory.add_message("assistant", text, self._clock())
        return text

    def _act(
        self,
        user_text: str,
        history: list[Message],
        now: datetime,
        on_tool: Callable[[str], None] | None,
        on_action: Callable[[str], None] | None = None,
    ) -> list[Outcome]:
        """필요한 작업을 AI에게 고르게 한 뒤 실제로 하고, 작업마다 결과를 돌려준다."""
        recent = [(message.role, message.content) for message in history[-PLANNER_HISTORY_LIMIT:]]
        planner = build_planner_prompt(
            self._toolbox.schemas, recent=recent, state=self._toolbox.state_summary(now), now=now
        )
        plan = self._engine.complete_json(
            [{"role": "system", "content": planner}, {"role": "user", "content": user_text}], ACTION_FORMAT
        )

        actions = plan.get("actions") if isinstance(plan, dict) else None
        if not isinstance(actions, list):
            log.warning("작업 목록을 알아볼 수 없어 아무 작업도 하지 않습니다: %r", plan)
            return []

        results = []
        for action in actions[:MAX_ACTIONS]:
            name = action.get("tool") if isinstance(action, dict) else None
            if not isinstance(name, str):
                log.warning("알아볼 수 없는 작업을 건너뜁니다: %r", action)
                continue
            if name in NEEDS_CANCEL_WORD and not CANCEL_WORD.search(user_text):
                # 되돌릴 수 없는 작업은 사용자가 그렇게 말했을 때만 한다. AI가 넘겨짚은 것은 버린다.
                log.warning("사용자가 말하지 않은 %s 작업을 건너뜁니다: %r", name, user_text)
                continue
            if on_tool:
                on_tool(name)
            settled = _settle_time(action, user_text)
            if name == "write_document":
                # 무엇을 써 달라고 했는지는 AI가 옮겨 적은 주제보다 사용자가 한 말이 정확하다.
                settled = {**settled, "request": user_text}
            if on_action and (note := action_note(name, settled)):
                on_action(note)
            results.append(self._toolbox.execute(name, settled, now))
        return results


def _settle_time(action: dict, user_text: str) -> dict:
    """알람 시각에서, 사용자가 실제로 말한 것이 AI가 계산해 낸 것을 이기게 한다.

    AI는 '오후 6시 반'을 '90분 뒤'로, '모레'를 사흘 뒤 날짜로 잘못 바꾸곤 한다.
    말에서 바로 읽을 수 있는 것(오전·오후가 붙은 시각, 내일·모레)은 말한 그대로 쓴다.
    """
    if action.get("tool") != "add_alarm":
        return action
    settled = dict(action)
    has_time = bool(str(settled.get("time") or "").strip())

    if RELATIVE_TIME.search(user_text):
        # '10분 뒤'라고 말했다. AI가 덧붙인 시각은 암산한 값이니 버린다.
        if settled.get("minutes_from_now"):
            settled.pop("time", None)
        return settled

    if clock := spoken_clock(user_text):
        settled["time"] = clock
        settled.pop("minutes_from_now", None)
    elif has_time:
        settled.pop("minutes_from_now", None)

    repeats = bool(str(settled.get("repeat") or "").strip())
    day_offset = spoken_day_offset(user_text)
    if day_offset is not None and not repeats and str(settled.get("time") or "").strip():
        settled["day_offset"] = day_offset
        settled.pop("date", None)
    return settled
