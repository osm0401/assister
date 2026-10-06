"""AI가 고른 PC 조작 동작을 읽고, 실행 전에 확인을 받아야 하는지 가려낸다. 실제 조작은 하지 않는다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from assister.screen.privacy import Window

POINTER_KINDS = ("click", "double_click", "scroll")
KINDS = (*POINTER_KINDS, "type", "key", "open", "wait", "done", "fail")
PER_MILLE = 1000

_FIELDS = {
    # AI는 이 순서대로 채운다. 무엇을 할지와 누를 것의 이름을 먼저 정하고 나서 좌표를 찾게 한다.
    "why": {"type": "string"},
    "action": {"type": "string", "enum": list(KINDS)},
    "target": {"type": "string"},
    "monitor": {"type": "integer"},
    "x": {"type": "integer"},
    "y": {"type": "integer"},
    "text": {"type": "string"},
    "keys": {"type": "string"},
    "amount": {"type": "integer"},
    "risky": {"type": "boolean"},
}
# 칸을 전부 채우게 한다. 골라 채우게 두면 click인데 좌표를 빼먹는다. 동작에 안 쓰는 칸은 parse_action이 버린다.
ACTION_FORMAT = {"type": "object", "properties": _FIELDS, "required": list(_FIELDS)}

MODIFIER_KEYS = ("ctrl", "alt", "shift", "win")
NAMED_KEYS = (
    "enter", "tab", "space", "backspace", "delete", "up", "down", "left", "right",
    "home", "end", "pageup", "pagedown", *(f"f{number}" for number in range(1, 13)),
)  # fmt: skip
# Esc는 사용자가 조작을 멈출 때 쓰는 키라서 AI가 누르지 못한다.
# Shift+Delete는 휴지통을 거치지 않고 지우므로 하지 않는다.
FORBIDDEN_KEYS = {"esc": "Esc는 누르지 않습니다", "shift+delete": "휴지통을 거치지 않는 삭제는 하지 않습니다"}

# 확인을 받을 작업: 삭제, 결제, 메시지·메일 전송, 설정 변경, 프로그램 설치, 파일 덮어쓰기, 저장 안 된 창 닫기
RISK_WORDS = (
    "삭제", "지우", "제거", "휴지통", "delete", "remove", "erase",
    "결제", "구매", "주문", "pay", "buy", "purchase", "checkout",
    "보내기", "전송", "게시", "send", "post", "submit",
    "설치", "install", "덮어쓰", "바꾸기", "overwrite", "replace",
    "저장 안 함", "저장하지 않", "don't save", "discard",
    "적용", "apply", "초기화", "reset", "포맷", "format", "로그아웃", "탈퇴",
)  # fmt: skip
RISKY_KEYS = {"delete": "지우는 키", "alt+f4": "창을 닫는 키", "win+r": "무엇이든 실행할 수 있는 실행 창을 여는 키"}
# 명령을 받아 실행하는 창. 여기에 글자를 넣고 Enter를 치면 파일 삭제든 설치든 무엇이든 된다.
COMMAND_WINDOWS = (
    "cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe",
    "conhost.exe", "openconsole.exe", "wsl.exe", "bash.exe",
)  # fmt: skip
RUN_BOX_TITLES = ("실행", "run")
# 열기만 해도 시스템을 바꿀 수 있는 도구로 이어지는 프로그램.
SYSTEM_TOOLS = frozenset({
    "cmd", "powershell", "pwsh", "wt", "windowsterminal", "bash", "wsl",
    "regedit", "reg", "mmc", "msconfig", "taskschd", "schtasks", "diskpart", "format",
    "control", "sc", "net", "rundll32", "mshta", "wscript", "cscript", "msiexec",
})  # fmt: skip
# 이런 프로그램에서 Enter는 메시지나 메일을 보내는 일이다.
MESSAGING = ("kakaotalk", "카카오톡", "discord", "slack", "telegram", "whatsapp", "outlook", "메일", "mail", "teams")
SAFE_TO_OPEN = re.compile(r"https?://\S+|[\w .-]+", re.IGNORECASE)
RUNNABLE = re.compile(r"\.(exe|msi|bat|cmd|ps1|vbs|js|scr|lnk|reg)$", re.IGNORECASE)


class ActionError(Exception):
    """AI가 고른 동작을 쓸 수 없을 때."""


@dataclass(frozen=True)
class Action:
    kind: str
    why: str  # 지금 하려는 일을 사용자에게 보여 줄 한 문장
    monitor: int = 0
    x: int = 0  # 그 모니터 그림의 왼쪽 위가 0, 오른쪽 아래가 1000
    y: int = 0
    target: str = ""  # 누르려는 것의 이름 (AI가 본 것)
    text: str = ""
    keys: str = ""
    amount: int = 0
    risky: bool = False  # AI가 스스로 위험하다고 본 동작


def _integer(raw: dict, key: str) -> int:
    value = raw.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ActionError(f"{key}는 정수여야 합니다: {value!r}")
    return value


def _normal_keys(text: str) -> str:
    parts = [part.strip().lower() for part in text.split("+")]
    combo = "+".join(parts)
    if combo in FORBIDDEN_KEYS:
        raise ActionError(FORBIDDEN_KEYS[combo])
    *modifiers, key = parts
    known_key = key in NAMED_KEYS or key in MODIFIER_KEYS or (len(key) == 1 and key.isalnum())
    if not known_key or any(modifier not in MODIFIER_KEYS for modifier in modifiers):
        raise ActionError(f"모르는 키입니다: {text!r}")
    return combo


def parse_action(raw: object) -> Action:
    """AI가 보낸 JSON을 Action으로 바꾼다. 동작에 필요 없는 칸은 버린다."""
    if not isinstance(raw, dict):
        raise ActionError(f"동작을 알아볼 수 없습니다: {raw!r}")
    kind = raw.get("action")
    if kind not in KINDS:
        raise ActionError(f"모르는 동작입니다: {kind!r}")
    why = str(raw.get("why") or "").strip()
    risky = raw.get("risky") is True

    if kind in POINTER_KINDS:
        if "x" not in raw or "y" not in raw:
            raise ActionError(f"{kind}에는 x, y가 필요합니다")
        x, y = _integer(raw, "x"), _integer(raw, "y")
        if not (0 <= x <= PER_MILLE and 0 <= y <= PER_MILLE):
            raise ActionError(f"좌표가 화면 밖입니다: ({x}, {y})")
        return Action(
            kind, why, monitor=_integer(raw, "monitor"), x=x, y=y,
            target=str(raw.get("target") or "").strip(),
            amount=_integer(raw, "amount") if kind == "scroll" else 0, risky=risky,
        )  # fmt: skip
    if kind in ("type", "open"):
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ActionError(f"{kind}에는 text가 필요합니다")
        return Action(kind, why, text=text if kind == "type" else text.strip(), risky=risky)
    if kind == "key":
        return Action(kind, why, keys=_normal_keys(str(raw.get("keys") or "")), risky=risky)
    if kind == "wait":
        return Action(kind, why, amount=_integer(raw, "amount"))
    return Action(kind, why)


def describe(action: Action) -> str:
    """끝낸 동작을 사실 그대로 적는다. AI에게 '지금까지 한 일'로 보여 준다."""
    where = f"'{action.target}'을(를)" if action.target else f"{action.monitor}번 모니터의 ({action.x}, {action.y})을"
    if action.kind == "click":
        return f"{where} 눌렀다"
    if action.kind == "double_click":
        return f"{where} 두 번 눌렀다"
    if action.kind == "type":
        return f"'{action.text}'이라고 입력했다"
    if action.kind == "key":
        return f"{action.keys} 키를 눌렀다"
    if action.kind == "scroll":
        return "화면을 위로 올렸다" if action.amount > 0 else "화면을 아래로 내렸다"
    if action.kind == "open":
        return f"{action.text}을(를) 열었다"
    return "잠깐 기다렸다"


def monitor_in_use(window: Window | None, monitors: Sequence[tuple[int, int, int, int]]) -> int:
    """맨 앞 창의 가운데가 놓인 모니터 번호. 알 수 없으면 주 모니터(0)."""
    if window is None:
        return 0
    center_x, center_y = (window.rect[0] + window.rect[2]) // 2, (window.rect[1] + window.rect[3]) // 2
    for index, (left, top, right, bottom) in enumerate(monitors):
        if left <= center_x < right and top <= center_y < bottom:
            return index
    return 0


def to_screen(monitors: Sequence[tuple[int, int, int, int]], monitor: int, x: int, y: int) -> tuple[int, int]:
    """모니터 번호와 0~1000 좌표를 실제 화면 좌표로 바꾼다."""
    if not 0 <= monitor < len(monitors):
        raise ActionError(f"{monitor}번 모니터가 없습니다")
    left, top, right, bottom = monitors[monitor]
    return (
        left + min(round((right - left) * x / PER_MILLE), right - left - 1),
        top + min(round((bottom - top) * y / PER_MILLE), bottom - top - 1),
    )


def _risk_word(*texts: str) -> str | None:
    joined = " ".join(texts).lower()
    return next((word for word in RISK_WORDS if word in joined), None)


def risk_reason(action: Action, *, element_name: str, foreground: Window | None) -> str | None:
    """실행 전에 확인을 받아야 하면 그 이유를, 아니면 None을 돌려준다.

    element_name은 누르려는 자리에 있는 것의 이름으로, Windows가 알려 준 값이다.
    """
    if action.risky:
        return "AI가 조심해야 할 동작이라고 봤어요"

    if action.kind in ("click", "double_click"):
        word = _risk_word(element_name, action.target)
        return f"'{element_name or action.target}'을(를) 누르는 일이에요" if word else None

    front = f"{foreground.title}\n{foreground.process}".lower() if foreground else ""
    sends = any(name in front for name in MESSAGING)
    runs_commands = foreground is not None and (
        foreground.process.lower() in COMMAND_WINDOWS or foreground.title.strip().lower() in RUN_BOX_TITLES
    )
    if action.kind in ("key", "type") and runs_commands:
        return "명령을 실행할 수 있는 창에 입력하는 일이에요"
    if action.kind == "key":
        if action.keys in RISKY_KEYS:
            return f"{RISKY_KEYS[action.keys]}예요"
        return "메시지나 메일을 보내는 일일 수 있어요" if sends and action.keys.endswith("enter") else None
    if action.kind == "type":
        return "메시지나 메일을 보내는 일일 수 있어요" if sends and "\n" in action.text else None
    if action.kind == "open":
        name = action.text.strip().lower().removesuffix(".exe")
        if name in SYSTEM_TOOLS:
            return "명령 창이나 시스템 도구를 여는 일이에요"
        plain = SAFE_TO_OPEN.fullmatch(action.text) and not RUNNABLE.search(action.text)
        return None if plain else "파일이나 프로그램을 실행하는 일이에요"
    return None
