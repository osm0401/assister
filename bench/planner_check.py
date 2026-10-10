"""실제 모델이 사용자의 말을 맞는 작업으로 바꾸는지 잰다.

지시문(ai/prompt.py)이나 작업 틀(ai/tools.py)을 고친 뒤에 돌려서, 전보다 나빠지지 않았는지 본다.
Ollama가 꺼져 있으면 켠다. 기억과 할 일은 임시 폴더에만 쓰고, 인터넷 검색과 명령 실행은 실제로 하지 않는다.

    uv run python bench/planner_check.py
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

from assister.ai.agy import AgyEngine
from assister.ai.chat import ChatSession
from assister.ai.engine import OllamaEngine, start_ollama
from assister.ai.gemini import GeminiEngine
from assister.ai.search import Hit
from assister.ai.tools import ToolBox
from assister.memory.store import MemoryStore
from assister.system.shell import CommandResult, risk_reason
from assister.tasks.repository import TaskRepository

NOW = datetime(2026, 10, 6, 15, 0)  # 화요일 오후 3시
MODEL = "qwen3.5:9b"

# (사용자의 말, 실행돼야 할 작업들, 알람이 생긴다면 그 시각과 되풀이)
CASES: list[tuple[str, set[str], tuple[str, str] | None]] = [
    ("안녕! 내 이름은 상민이야. 매운 음식을 좋아해.", {"remember"}, None),
    ("나 고양이 두 마리 키워", {"remember"}, None),
    ("내 이름 기억해?", set(), None),
    ("오늘 좀 피곤하다", set(), None),
    ("저녁 뭐 먹을까?", set(), None),
    ("고마워!", set(), None),
    ("10분 뒤에 빨래 꺼내라고 알려 줘.", {"add_alarm"}, ("10/06 15:10", "")),
    ("두 시간 뒤에 약 먹으라고 해줘", {"add_alarm"}, ("10/06 17:00", "")),
    ("오후 6시 반에 저녁 약속 있어. 알람 맞춰줘", {"add_alarm"}, ("10/06 18:30", "")),
    ("내일 아침 9시에 회의 있다고 알려줘", {"add_alarm"}, ("10/07 09:00", "")),
    ("모레 오전 10시 반에 병원 예약 있어 알려줘", {"add_alarm"}, ("10/08 10:30", "")),
    ("다음 주 화요일 오후 3시에 병원 간다고 알려줘", {"add_alarm"}, ("10/13 15:00", "")),
    ("10월 20일 오전 10시에 치과 예약 알려줘", {"add_alarm"}, ("10/20 10:00", "")),
    ("매일 아침 9시에 약 먹으라고 해줘", {"add_alarm"}, ("10/07 09:00", "daily")),
    ("알람 뭐 있어?", {"list_alarms"}, None),
    ("그 알람 취소해줘", {"cancel_alarm"}, None),
    ("할 일에 우유 사기 적어 줘.", {"add_todo"}, None),
    ("할 일 두 개 적어줘. 장보기랑 세탁소 가기", {"add_todo"}, None),
    ("할 일 뭐 남았어?", {"list_todos"}, None),
    ("1번 다 했어", {"complete_todo"}, None),
    ("책 반납 다 했어", {"complete_todo"}, None),
    ("30분 있다가 스트레칭 하라고 해줘 그리고 할 일에 메일 답장 추가해줘", {"add_alarm", "add_todo"}, ("10/06 15:30", "")),
    ("어제 나 뭐 했어?", {"review_day"}, None),
    ("오늘 내가 PC로 뭐 했는지 알려줘", {"review_day"}, None),
    ("메모장 열어서 안녕이라고 써 줘", {"control_pc"}, None),
    ("크롬에서 네이버 열어줘", {"control_pc"}, None),
    ("계산기 좀 켜줘", {"control_pc"}, None),
    ("오늘 서울 날씨 어때?", {"web_search"}, None),
    ("요즘 달러 환율 얼마야?", {"web_search"}, None),
    ("오늘 뉴스 뭐 있는지 검색해 줘", {"web_search"}, None),
    ("바탕화면에 메모.txt 파일 만들어 줘", {"run_command"}, None),
    ("다운로드 폴더에 뭐 있어?", {"run_command"}, None),
    ("C 드라이브 용량 얼마나 남았어?", {"run_command"}, None),
    ("바탕화면에 있는 old.txt 지워 줘", {"run_command"}, None),
    ("API를 자세하게 다운로드에 워드 파일로 정리 해줘", {"write_document"}, None),
    ("파이썬 설치하는 법을 문서로 만들어서 바탕화면에 저장해 줘", {"write_document"}, None),
    ("김치찌개 끓이는 법을 워드로 정리해 줘", {"write_document"}, None),
    ("워드 파일에 오늘 있었던일 정리해서 다운로드에 생성해줘", {"write_document"}, None),
    ("오늘 있었던 일 말해 줘", {"review_day"}, None),
]
FOUND = [Hit("검색 결과", "https://example.com/", "서울은 맑고 낮 최고 21도, 달러 환율은 1,380원")]
COMMANDS: list[str] = []  # AI가 만든 명령. 실제로 실행하지는 않고 끝에 보여 준다
DOCUMENTS: list[tuple[str, str]] = []  # AI가 정한 (저장할 곳, 파일 이름). 실제로 저장하지는 않는다


def check(engine: OllamaEngine, text: str, expected: set[str], alarm: tuple[str, str] | None) -> tuple[bool, str]:
    folder = Path(tempfile.mkdtemp())
    tasks, memory = TaskRepository(folder / "tasks.db"), MemoryStore(folder / "memory.db")
    try:
        tasks.add_todo("책 반납", NOW)
        tasks.add_todo("보고서 쓰기", NOW)
        existing = tasks.add_alarm("약 먹기", NOW.replace(hour=21))
        memory.add_fact("떡볶이를 좋아함", NOW)
        toolbox = ToolBox(
            tasks,
            memory,
            start_control=lambda goal: True,
            search=lambda query: FOUND,  # 실제 검색은 안 함
            shell=lambda command: COMMANDS.append(command) or CommandResult("메모.txt", 0),
            confirm=lambda text: True,
            write=lambda topic: "제목\n글",
            save_document=lambda kind, name, text: DOCUMENTS.append((kind, name)) or Path(f"{name}.docx"),
        )
        used: list[str] = []
        session = ChatSession(engine, memory, toolbox, character_name="모모", clock=lambda: NOW)
        reply = session.reply(text, on_tool=used.append)
        made = [(a.due_at.strftime("%m/%d %H:%M"), a.repeat) for a in tasks.list_pending_alarms() if a.id != existing.id]
        ok = set(used) == expected and (alarm is None or made == [alarm])
        return ok, f"작업={used} 알람={made} 답={reply!r}"
    finally:
        tasks.close()
        memory.close()


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    # ASSISTER_ENGINE=gemini 또는 agy 로 돌리면 그 엔진으로 잰다. agy는 한 마디에 15초쯤 걸린다.
    chosen = os.environ.get("ASSISTER_ENGINE")
    engines = {"gemini": GeminiEngine, "agy": AgyEngine}
    engine = engines[chosen]() if chosen in engines else OllamaEngine(MODEL, start_server=start_ollama)
    engine.warm_up()
    started = time.perf_counter()
    misses = 0
    for text, expected, alarm in CASES:
        ok, detail = check(engine, text, expected, alarm)
        misses += not ok
        if not ok:
            print(f"X {text}\n    기대: 작업={sorted(expected)} 알람={alarm}\n    실제: {detail}")
    for command in COMMANDS:
        print(f"명령: {command}  ->  {risk_reason(command) or '바로 실행'}")
    for kind, name in DOCUMENTS:
        print(f"문서: {name}.docx -> {kind}")
    seconds = (time.perf_counter() - started) / len(CASES)
    print(f"맞음 {len(CASES) - misses}/{len(CASES)}, 한 마디에 평균 {seconds:.1f}초")
    return 1 if misses else 0


if __name__ == "__main__":
    sys.exit(main())
