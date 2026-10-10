"""앱 설정. 값을 바꿀 때는 dataclasses.replace로 새 객체를 만든다."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

MIN_NUM_CTX = 2048
MAX_NUM_CTX = 262144
STT_DEVICES = ("cuda", "cpu")
ENGINES = ("ollama", "gemini", "agy")
INT_RANGES = {
    "screen_interval_sec": (5, 3600),
    "screen_width": (480, 3840),
    "screen_retention_days": (1, 365),
    "proactive_per_hour": (0, 60),
    "control_max_steps": (1, 40),
    "control_width": (480, 3840),
}


class SettingsError(Exception):
    """설정 파일을 읽거나 쓸 수 없을 때."""


@dataclass(frozen=True)
class Settings:
    character: str = "default"
    outfit: str = "basic"
    props: tuple[str, ...] = ()  # 걸치고 있는 소품
    panel_position: tuple[int, int] | None = None  # 패널을 옮겨 둔 자리. 없으면 화면 왼쪽 1/4
    panel_size: tuple[int, int] | None = None  # 패널의 바꿔 둔 크기 (너비, 높이). 없으면 화면의 1/4 너비에 전체 높이
    summon_hotkey: str = "Ctrl+Shift+Space"
    do_not_disturb: bool = False
    # AI 엔진. ollama는 내 PC에서 돌고, gemini는 대화와 화면 그림을 Google 서버로 보낸다.
    # agy는 Antigravity CLI로 대화만 맡고(느리다), 화면 보기와 PC 조작은 ollama에 남는다.
    engine: str = "ollama"
    gemini_model: str = "gemini-3.8-flash"  # API 키는 여기 적지 않고 환경 변수 GEMINI_API_KEY에 둔다
    agy_model: str = "gemini-3.8-flash-low"  # agy models로 고를 수 있는 이름을 본다
    model: str = "qwen3.5:9b"
    ollama_url: str = "http://localhost:11434"
    num_ctx: int = 16384
    # 음성
    tts_enabled: bool = True
    wake_word_enabled: bool = True
    voice_hotkey: str = "Ctrl+Alt+M"  # Ctrl+Alt+Space는 Claude 같은 다른 앱이 흔히 쓰고 있다
    stt_model: str = "large-v3-turbo"
    stt_device: str = "cuda"
    # 화면 보기
    screen_watch_enabled: bool = True
    screen_interval_sec: int = 30
    screen_width: int = 1280  # AI에게 보낼 그림의 가로 크기. 클수록 글자를 잘 읽지만 느리다.
    screen_retention_days: int = 7
    rest_when_fullscreen: bool = True  # 게임이나 전체 화면 영상 중에는 화면 분석을 쉰다
    blocked_apps: tuple[str, ...] = ()  # 창 제목이나 프로그램 이름에 이 낱말이 있으면 보지 않는다
    proactive_per_hour: int = 1  # 먼저 말 걸기. 0이면 하지 않는다.
    # PC 제어
    control_max_steps: int = 12  # 한 번 부탁에 할 수 있는 동작 수
    control_width: int = 1920  # 조작할 때 AI에게 보낼 그림의 가로 크기. 줄이면 엉뚱한 곳을 누르기 쉽다.


def _validated(raw: dict) -> Settings:
    known = {field.name for field in fields(Settings)}
    values = {key: value for key, value in raw.items() if key in known}

    for name in ("panel_position", "panel_size"):
        pair = values.get(name)
        if pair is None:
            continue
        if not (isinstance(pair, (list, tuple)) and len(pair) == 2 and all(type(v) is int for v in pair)):
            raise SettingsError(f"{name}은(는) 정수 두 개여야 합니다: {pair!r}")
        if name == "panel_size" and min(pair) <= 0:
            raise SettingsError(f"panel_size는 0보다 커야 합니다: {pair!r}")
        values[name] = (pair[0], pair[1])

    for name in (
        "character", "outfit", "summon_hotkey", "voice_hotkey", "model", "ollama_url", "stt_model", "gemini_model",
        "agy_model",
    ):  # fmt: skip
        if name in values and (not isinstance(values[name], str) or not values[name].strip()):
            raise SettingsError(f"{name}은(는) 비어 있지 않은 글자여야 합니다: {values[name]!r}")

    for name in (
        "do_not_disturb",
        "tts_enabled",
        "wake_word_enabled",
        "screen_watch_enabled",
        "rest_when_fullscreen",
    ):
        if name in values and not isinstance(values[name], bool):
            raise SettingsError(f"{name}은(는) true 또는 false여야 합니다: {values[name]!r}")

    for name, (low, high) in INT_RANGES.items():
        value = values.get(name, getattr(Settings, name))
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise SettingsError(f"{name}은(는) {low}~{high} 사이 정수여야 합니다: {value!r}")

    for name in ("blocked_apps", "props"):
        items = values.get(name, ())
        if not isinstance(items, (list, tuple)) or not all(isinstance(item, str) for item in items):
            raise SettingsError(f"{name}은(는) 글 목록이어야 합니다: {items!r}")
        values[name] = tuple(items)

    num_ctx = values.get("num_ctx", Settings.num_ctx)
    if isinstance(num_ctx, bool) or not isinstance(num_ctx, int) or not MIN_NUM_CTX <= num_ctx <= MAX_NUM_CTX:
        raise SettingsError(f"num_ctx는 {MIN_NUM_CTX}~{MAX_NUM_CTX} 사이 정수여야 합니다: {num_ctx!r}")

    if values.get("engine", Settings.engine) not in ENGINES:
        raise SettingsError(f"engine은 {', '.join(ENGINES)} 중 하나여야 합니다: {values['engine']!r}")

    if values.get("stt_device", Settings.stt_device) not in STT_DEVICES:
        raise SettingsError(f"stt_device는 {', '.join(STT_DEVICES)} 중 하나여야 합니다: {values['stt_device']!r}")

    if not str(values.get("ollama_url", Settings.ollama_url)).startswith(("http://", "https://")):
        raise SettingsError(f"ollama_url은 http:// 또는 https://로 시작해야 합니다: {values['ollama_url']!r}")

    return Settings(**values)


def load_settings(path: Path) -> Settings:
    """설정 파일을 읽는다. 파일이 없으면 기본값을 돌려준다."""
    if not path.exists():
        return Settings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SettingsError(f"설정 파일을 읽을 수 없습니다 ({path}): {error}") from error
    if not isinstance(raw, dict):
        raise SettingsError(f"설정 파일 형식이 잘못됐습니다 ({path})")
    return _validated(raw)


def save_settings(path: Path, settings: Settings) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(settings), ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as error:
        raise SettingsError(f"설정 파일을 저장할 수 없습니다 ({path}): {error}") from error
