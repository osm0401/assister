"""로컬 모델이 이 PC에서 얼마나 빠르고 정확한지 잰다.

재는 것: 모델 올리는 시간, 대화 속도, 도구 호출, 화면 요약(모니터 1대·2대),
스크린샷 좌표 클릭 정확도. 화면은 bench/mock_screen.py의 가짜 화면을 쓴다.

사용법:
    uv run python bench/vision_bench.py --model qwen3.5:9b
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).parent))
from mock_screen import Target, render  # noqa: E402

OLLAMA_URL = "http://localhost:11434"
OUT_DIR = Path(__file__).parent / "out"
NUM_CTX = 16384
CALL_TIMEOUT_SEC = 900.0
MONITORS = {"main": (1920, 1200), "sub": (1920, 1080)}

PERSONA = "너는 사용자의 바탕화면에 사는 친근한 캐릭터야. 한국어로 두세 문장 안에서 짧게 답해."
CHAT_PROMPT = "오늘 좀 피곤하네. 저녁 뭐 먹을지 하나만 추천해 줘."
SUMMARY_PROMPT = "이 화면에서 사용자가 지금 무엇을 하고 있는지 한국어 두 문장으로 요약해."
TOOL_PROMPT = "10분 뒤에 빨래 꺼내라고 알려 줘."
CLICK_ASK = (
    "스크린샷에서 '{name}' 버튼을 마우스로 클릭하려고 해. "
    '클릭할 지점을 JSON으로만 답해. 형식: {{"x": 정수, "y": 정수}}. '
)
# 모델마다 익숙한 좌표 방식이 달라서 두 가지로 물어본다.
CLICK_STYLES = {
    "pixel": "좌표는 이 이미지의 픽셀 기준이고 이미지 크기는 가로 {width}, 세로 {height}야.",
    "relative": "좌표는 이미지 왼쪽 위가 (0, 0), 오른쪽 아래가 (1000, 1000)인 상대 좌표야.",
}
CLICK_FORMAT = {
    "type": "object",
    "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
    "required": ["x", "y"],
}
ALARM_TOOL = {
    "type": "function",
    "function": {
        "name": "add_alarm",
        "description": "지정한 시간이 지나면 사용자에게 알려 주는 알람을 등록한다.",
        "parameters": {
            "type": "object",
            "properties": {
                "minutes_from_now": {"type": "integer", "description": "지금부터 몇 분 뒤인지"},
                "message": {"type": "string", "description": "알람 때 보여 줄 말"},
            },
            "required": ["minutes_from_now", "message"],
        },
    },
}


def encode_image(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def base_payload(model: str, messages: list[dict]) -> dict:
    return {
        "model": model,
        "messages": messages,
        "think": False,
        "keep_alive": "10m",
        "options": {"num_ctx": NUM_CTX, "temperature": 0.2},
    }


def speed_of(stats: dict) -> float:
    """초당 생성 토큰 수."""
    duration_ns = stats.get("eval_duration") or 0
    return round(stats.get("eval_count", 0) / (duration_ns / 1e9), 1) if duration_ns else 0.0


def call(client: httpx.Client, payload: dict) -> tuple[dict, float]:
    """한 번에 응답을 받고 (응답, 걸린 초)를 돌려준다."""
    started = time.perf_counter()
    response = client.post(f"{OLLAMA_URL}/api/chat", json={**payload, "stream": False})
    response.raise_for_status()
    return response.json(), round(time.perf_counter() - started, 2)


def call_streaming(client: httpx.Client, payload: dict) -> dict:
    """글자가 나오는 대로 받으면서 첫 글자까지 걸린 시간을 잰다."""
    started = time.perf_counter()
    first_token_sec = None
    pieces: list[str] = []
    stats: dict = {}
    with client.stream("POST", f"{OLLAMA_URL}/api/chat", json={**payload, "stream": True}) as response:
        response.raise_for_status()
        for line in response.iter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            piece = chunk.get("message", {}).get("content", "")
            if piece and first_token_sec is None:
                first_token_sec = round(time.perf_counter() - started, 2)
            pieces.append(piece)
            if chunk.get("done"):
                stats = chunk
    return {
        "first_token_sec": first_token_sec,
        "total_sec": round(time.perf_counter() - started, 2),
        "tokens_per_sec": speed_of(stats),
        "reply": "".join(pieces).strip(),
    }


def measure_load(client: httpx.Client, model: str) -> dict:
    payload = base_payload(model, [{"role": "user", "content": "안녕"}])
    stats, seconds = call(client, payload)
    running = client.get(f"{OLLAMA_URL}/api/ps").json().get("models", [])
    entry = next((m for m in running if m.get("name") == model), {})
    size, size_vram = entry.get("size", 0), entry.get("size_vram", 0)
    return {
        "load_sec": round(stats.get("load_duration", 0) / 1e9, 1),
        "first_call_sec": seconds,
        "memory_gb": round(size / 1e9, 1),
        "gpu_share_pct": round(100 * size_vram / size) if size else None,
    }


def measure_chat(client: httpx.Client, model: str) -> dict:
    messages = [{"role": "system", "content": PERSONA}, {"role": "user", "content": CHAT_PROMPT}]
    return call_streaming(client, base_payload(model, messages))


def measure_tool(client: httpx.Client, model: str) -> dict:
    payload = {**base_payload(model, [{"role": "user", "content": TOOL_PROMPT}]), "tools": [ALARM_TOOL]}
    stats, seconds = call(client, payload)
    calls = stats.get("message", {}).get("tool_calls") or []
    arguments = calls[0]["function"]["arguments"] if calls else None
    correct = bool(calls) and calls[0]["function"]["name"] == "add_alarm" and arguments.get("minutes_from_now") == 10
    return {"total_sec": seconds, "called": bool(calls), "correct": correct, "arguments": arguments}


def measure_summary(client: httpx.Client, model: str, images: list[str]) -> dict:
    message = {"role": "user", "content": SUMMARY_PROMPT, "images": images}
    stats, seconds = call(client, base_payload(model, [message]))
    return {
        "total_sec": seconds,
        "prompt_tokens": stats.get("prompt_eval_count"),
        "tokens_per_sec": speed_of(stats),
        "reply": stats.get("message", {}).get("content", "").strip(),
    }


def inside(x: float, y: float, rect: tuple[int, int, int, int]) -> bool:
    left, top, width, height = rect
    return left <= x <= left + width and top <= y <= top + height


def measure_click(
    client: httpx.Client, model: str, image: str, target: Target, size: tuple[int, int], style: str
) -> dict:
    width, height = size
    prompt = (CLICK_ASK + CLICK_STYLES[style]).format(name=target.name, width=width, height=height)
    payload = {
        **base_payload(model, [{"role": "user", "content": prompt, "images": [image]}]),
        "format": CLICK_FORMAT,
    }
    stats, seconds = call(client, payload)
    raw = stats.get("message", {}).get("content", "")
    try:
        point = json.loads(raw)
        x, y = float(point["x"]), float(point["y"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {
            "target": target.name,
            "style": style,
            "total_sec": seconds,
            "raw": raw[:120],
            "hit_pixel": False,
            "hit_per_mille": False,
        }
    return {
        "target": target.name,
        "style": style,
        "total_sec": seconds,
        "answer": [x, y],
        "rect": list(target.rect),
        # 모델마다 좌표를 픽셀로 주기도, 0~1000 비율로 주기도 해서 둘 다 채점한다.
        "hit_pixel": inside(x, y, target.rect),
        "hit_per_mille": inside(x / 1000 * width, y / 1000 * height, target.rect),
    }


def unload(client: httpx.Client, model: str) -> None:
    client.post(f"{OLLAMA_URL}/api/chat", json={"model": model, "messages": [], "keep_alive": 0})


def run(model: str, target_count: int, styles: list[str], clicks_only: bool) -> dict:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    shots: dict[str, str] = {}
    targets: list[Target] = []
    for name, (width, height) in MONITORS.items():
        path = OUT_DIR / f"mock_{name}.png"
        rendered = render(path, width, height)
        shots[name] = encode_image(path)
        if name == "main":
            targets = rendered[:target_count]

    result: dict = {"model": model, "num_ctx": NUM_CTX}
    with httpx.Client(timeout=CALL_TIMEOUT_SEC) as client:
        steps = [
            ("load", lambda: measure_load(client, model)),
            # 앞에서 본 적 없는 순서로 두 장을 줘서, 이전 계산을 재사용하지 못하게 하고 잰다.
            ("screen_two_fresh", lambda: measure_summary(client, model, [shots["sub"], shots["main"]])),
        ]
        if not clicks_only:
            steps += [
                ("chat", lambda: measure_chat(client, model)),
                ("tool", lambda: measure_tool(client, model)),
                ("screen_one", lambda: measure_summary(client, model, [shots["main"]])),
                ("screen_two", lambda: measure_summary(client, model, [shots["main"], shots["sub"]])),
            ]
        try:
            for name, step in steps:
                result[name] = step()
                print(f"[{model}] {name}: {json.dumps(result[name], ensure_ascii=False)}", flush=True)
            clicks = []
            for style in styles:
                for target in targets:
                    clicks.append(measure_click(client, model, shots["main"], target, MONITORS["main"], style))
                    print(f"[{model}] click: {json.dumps(clicks[-1], ensure_ascii=False)}", flush=True)
            result["clicks"] = clicks
        except httpx.HTTPError as error:
            result["error"] = f"{type(error).__name__}: {error}"
            print(f"[{model}] 중단: {result['error']}", flush=True)
        finally:
            unload(client, model)

    suffix = "_clicks" if clicks_only else ""
    out_path = OUT_DIR / f"{model.replace(':', '_').replace('/', '_')}{suffix}.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"저장: {out_path}", flush=True)
    return result


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="로컬 모델 속도·정확도 측정")
    parser.add_argument("--model", required=True)
    parser.add_argument("--targets", type=int, default=10, help="좌표 클릭으로 채점할 버튼 수")
    parser.add_argument("--styles", nargs="+", choices=sorted(CLICK_STYLES), default=["pixel", "relative"])
    parser.add_argument("--clicks-only", action="store_true", help="좌표 클릭만 다시 잰다")
    args = parser.parse_args()
    run(args.model, args.targets, args.styles, args.clicks_only)


if __name__ == "__main__":
    main()
