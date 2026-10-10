"""agy 연결이 실제로 되는지 본다. Antigravity에 로그인돼 있어야 한다.

요청 세 번(대화, 작업 고르기 두 가지)을 보낸다. 한 번에 7초쯤 걸리고 토큰을 2만 5천 개쯤 쓴다.

    uv run python bench/agy_check.py
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

from assister.ai.agy import AgyEngine
from assister.ai.engine import EngineError
from assister.ai.prompt import build_planner_prompt, build_system_prompt
from assister.ai.tools import ACTION_FORMAT, SCHEMAS

STATE = "[지금 남은 할 일]\n없음\n\n[지금 울릴 알람]\n없음"


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    engine = AgyEngine()
    now = datetime.now()
    try:
        engine.warm_up()
        started = time.perf_counter()
        system = build_system_prompt("모모", persona="", facts=["이름은 상민"], now=now)
        talk = [{"role": "system", "content": system}, {"role": "user", "content": "안녕! 내 이름 기억해?"}]
        reply = engine.chat(talk)
        print(f"대화 ({time.perf_counter() - started:.1f}초): {reply!r}")

        planner = build_planner_prompt(SCHEMAS, recent=[], state=STATE, now=now)
        for said in ("10분 뒤에 빨래 꺼내라고 알려 줘.", "바탕화면에 메모.txt 파일 만들어 줘"):
            started = time.perf_counter()
            plan = engine.complete_json([{"role": "system", "content": planner}, {"role": "user", "content": said}], ACTION_FORMAT)
            print(f"작업 고르기 ({time.perf_counter() - started:.1f}초) {said} -> {plan}")
    except EngineError as error:
        print(f"안 됨: {error}")
        return 1
    finally:
        engine.close()
    print("세 가지 모두 됨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
