"""PC 조작이 한 단계씩 무엇을 보고 무엇을 고르는지 명령 창에 보여 주면서 실제로 해 본다.

    uv run python bench/control_check.py "다나와에서 DDR4 4GB 가장 싼 것 찾아 줘" --start https://www.danawa.com

단계마다 [맨 앞 창]의 누를 수 있는 것 목록, AI가 [고른 것], 실제로 한 [조작]이 찍힌다.
마우스와 키보드가 실제로 움직인다. 멈추려면 이 창에서 Ctrl+C를 누른다.
위험해 보이는 동작(삭제, 결제 등)은 묻지 않고 하지 않는 것으로 친다.

--start 를 주면 그 주소를 먼저 열고 시작한다. 주지 않으면 지금 맨 앞에 있는 창에서 시작하는데,
그 창에 보이는 버튼 이름과 글자가 AI에게 전해진다.
--engine ollama 를 주면 agy 대신 내 PC의 모델이 고른다.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from PySide6.QtCore import QCoreApplication

from assister.ai.agy import AgyEngine
from assister.ai.engine import OllamaEngine, start_ollama
from assister.control import elements
from assister.control.actions import Action
from assister.control.agent import ControlAgent
from assister.control.executor import Win32Executor
from assister.screen.capture import take_snapshot
from assister.screen.privacy import DEFAULT_SENSITIVE

OPEN_WAIT_SEC = 5
MAX_WAIT_SEC = 300


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="PC 조작을 단계마다 보여 주며 해 본다")
    parser.add_argument("goal")
    parser.add_argument("--start", help="먼저 열 웹 주소나 프로그램")
    parser.add_argument("--engine", choices=("agy", "ollama"), default="agy")
    parser.add_argument("--steps", type=int, default=10)
    options = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    for noisy in ("httpx", "comtypes"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    app = QCoreApplication([])
    chooser = AgyEngine() if options.engine == "agy" else OllamaEngine("qwen3.5:9b", start_server=start_ollama)
    chooser.warm_up()
    executor = Win32Executor()
    if options.start:
        print(f"[먼저 열기] {options.start}")
        executor.perform(Action("open", "", text=options.start), None)
        time.sleep(OPEN_WAIT_SEC)

    agent = ControlAgent(
        chooser,  # 목록을 내주지 않는 창에서는 그림을 봐야 하는데, agy는 그림을 못 읽으니 그때는 실패로 끝난다
        executor,
        take_snapshot=take_snapshot,
        keywords=lambda: DEFAULT_SENSITIVE,
        width=1920,
        max_steps=options.steps,
        chooser=chooser,
        list_elements=elements.look,
    )
    finished: list[tuple[str, bool]] = []
    agent.finished.connect(lambda message, ok: finished.append((message, ok)))
    agent.confirm_needed.connect(lambda text: (print(f"[허락이 필요한 동작이라 하지 않음] {text}"), agent.answer(False)))
    print(f"[목표] {options.goal}")
    started = time.perf_counter()
    agent.run(options.goal)
    try:
        while not finished and time.perf_counter() - started < MAX_WAIT_SEC:
            app.processEvents()
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("[멈춤]")
    agent.stop()
    agent.join()
    app.processEvents()
    message, ok = finished[0] if finished else ("끝나지 않았어요.", False)
    print(f"[끝] {'성공' if ok else '실패'} · {time.perf_counter() - started:.0f}초 · {message}")
    chooser.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
