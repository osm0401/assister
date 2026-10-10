"""Gemini 연결이 실제로 되는지 본다. 키를 넣은 뒤 한 번 돌려 본다.

환경 변수 GEMINI_API_KEY에 키가 있어야 한다. 요청 세 번(대화, 작업 고르기, 그림 읽기)을 보낸다.
그림은 여기서 만든 가짜 화면이고, 사용자의 진짜 화면은 보내지 않는다.

    uv run python bench/gemini_check.py
"""

from __future__ import annotations

import base64
import io
import sys
import time
from datetime import datetime

from PIL import Image, ImageDraw

from assister.ai.engine import EngineError
from assister.ai.gemini import GeminiEngine
from assister.ai.prompt import build_planner_prompt
from assister.ai.tools import ACTION_FORMAT, SCHEMAS

SEEN = {"type": "object", "properties": {"color": {"type": "string"}, "shape": {"type": "string"}}, "required": ["color", "shape"]}


def fake_screen() -> str:
    """흰 바탕에 빨간 원 하나. 모델이 그림을 정말 읽는지 보려는 것이다."""
    image = Image.new("RGB", (320, 240), "white")
    ImageDraw.Draw(image).ellipse((100, 60, 220, 180), fill="red")
    buffer = io.BytesIO()
    image.save(buffer, "JPEG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    engine = GeminiEngine()
    try:
        engine.warm_up()
        started = time.perf_counter()
        pieces: list[str] = []
        reply = engine.chat([{"role": "user", "content": "한 문장으로 인사해 줘."}], on_delta=pieces.append)
        print(f"대화 ({time.perf_counter() - started:.1f}초, 조각 {len(pieces)}개): {reply}")

        started = time.perf_counter()
        planner = build_planner_prompt(SCHEMAS, recent=[], state="[지금 남은 할 일]\n없음\n\n[지금 울릴 알람]\n없음", now=datetime.now())
        talk = [{"role": "system", "content": planner}, {"role": "user", "content": "10분 뒤에 빨래 꺼내라고 알려 줘."}]
        print(f"작업 고르기 ({time.perf_counter() - started:.1f}초): {engine.complete_json(talk, ACTION_FORMAT)}")

        started = time.perf_counter()
        look = [{"role": "user", "content": "그림에 있는 도형의 색과 모양을 영어 낱말로 답해.", "images": [fake_screen()]}]
        print(f"그림 읽기 ({time.perf_counter() - started:.1f}초): {engine.complete_json(look, SEEN)}")
    except EngineError as error:
        print(f"안 됨: {error}")
        return 1
    finally:
        engine.close()
    print("세 가지 모두 됨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
