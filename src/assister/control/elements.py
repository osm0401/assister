"""맨 앞 창에서 실제로 누를 수 있는 것을 목록으로 만들어, AI가 번호로 고르게 한다.

화면 그림을 보고 좌표를 짐작하게 했더니 없는 버튼을 지어내 눌렀다(다나와에서 메모리를 찾다가 프린터를 눌렀다).
목록에서 고르면 있는 것만 누를 수 있고, 자리는 Windows가 알려 준 그대로다. 글만 보내므로 그림을 못 받는 엔진도 쓸 수 있다.

밖으로 나가는 것을 줄이려고: 맨 앞 창의 것만, 브라우저에서는 페이지 안쪽의 것만(다른 탭 제목이나 즐겨찾기는 뺀다),
입력 칸은 이름만(적힌 내용은 읽지 않는다), 비밀번호 칸은 빼고, 이름은 짧게 자른다.
"""

from __future__ import annotations

import ctypes
import logging
import re
from collections import Counter
from collections.abc import Sequence
from ctypes import wintypes
from dataclasses import dataclass

from assister.control.actions import Action, ActionError, parse_action
from assister.system import uia

log = logging.getLogger(__name__)

# UI Automation의 종류 번호 → 사용자와 AI에게 보여 줄 이름
KINDS = {
    50000: "버튼", 50031: "버튼", 50005: "링크", 50004: "입력 칸", 50003: "선택 상자", 50002: "체크 상자",
    50013: "고르기 단추", 50011: "메뉴", 50019: "탭", 50007: "목록 항목", 50024: "목록 항목",
}  # fmt: skip
TEXT = 50020
INPUT = "입력 칸"
WRAPPER = "목록 항목"  # 안에 링크나 버튼을 품고 있을 때가 많다
MAX_CHOICES = 70
MAX_TEXTS = 80
NAME_CHARS = 40
TEXT_CHARS = 60
MIN_SIDE = 6  # 이보다 작은 것은 누를 수 없는 장식으로 본다
SAME_ROW = 14  # 세로로 이만큼 안에 있으면 같은 줄로 본다
NEAR = 320  # 같은 줄에서 이만큼 안에 있어야 '옆'이라고 한다
MIN_CHOICES = 4  # 이보다 적게 나오면 이 창은 목록으로 조작할 수 없다고 보고 그림 방식으로 넘긴다

CHOICES = ("press", "type", "key", "scroll", "open", "wait", "done", "fail")
_FIELDS = {
    "why": {"type": "string"},
    "action": {"type": "string", "enum": list(CHOICES)},
    "number": {"type": "integer"},
    "text": {"type": "string"},
    "submit": {"type": "boolean"},
    "keys": {"type": "string"},
    "amount": {"type": "integer"},
    "risky": {"type": "boolean"},
}
CHOICE_FORMAT = {"type": "object", "properties": _FIELDS, "required": list(_FIELDS)}

PROMPT = """너는 사용자의 Windows PC를 대신 조작한다. 목표를 이루기 위해 지금 할 동작 하나만 JSON으로 낸다.

[누를 수 있는 것]은 지금 맨 앞 창에서 실제로 누를 수 있는 것 전부다. 번호로 고른다. 목록에 없는 것은 누를 수 없다.

- press: number의 것을 누른다.
- type: number의 입력 칸에 text를 넣는다. 검색처럼 넣고 바로 실행해야 하면 submit을 true로 한다.
- key: keys를 누른다. 예: enter, tab, ctrl+l
- scroll: 필요한 것이 목록에 없을 때 화면을 넘긴다. amount가 음수면 아래로, 양수면 위로.
- open: text의 프로그램이나 웹 주소를 연다. 사이트 안에서 찾을 때 검색 결과 주소를 알면 그 주소를 바로 연다.
- wait: 화면이 바뀌기를 기다린다.
- done: 목표를 이뤘다. why에 결과를 적는다. 찾아 달라는 목표면 [화면에 보이는 글]에서 읽은 이름과 값을 그대로 적는다.
- fail: 할 수 없다. why에 이유를 적는다.

why에는 지금 하려는 일을 사용자에게 보여 줄 한국어 한 문장으로 적는다. 화면에 없는 것은 지어내지 않는다.
삭제, 결제, 메시지나 메일 전송, 설정 변경, 프로그램 설치, 파일 덮어쓰기, 저장하지 않고 닫기에 해당하면 risky를 true로 한다.
[이미 끝낸 동작]은 실제로 실행된 것이다. 같은 동작을 되풀이하지 않는다.

[목표] {goal}

[이미 끝낸 동작]
{history}

[맨 앞 창] {window}

[누를 수 있는 것]
{choices}

[화면에 보이는 글]
{texts}"""


@dataclass(frozen=True)
class Element:
    kind: str
    name: str
    rect: tuple[int, int, int, int]  # 화면 좌표 (왼쪽, 위, 오른쪽, 아래)

    @property
    def center(self) -> tuple[int, int]:
        return (self.rect[0] + self.rect[2]) // 2, (self.rect[1] + self.rect[3]) // 2


@dataclass(frozen=True)
class Listing:
    window: str
    area: tuple[int, int, int, int]
    elements: tuple[Element, ...]
    texts: tuple[str, ...]

    def lines(self) -> str:
        return "\n".join(f"{number} [{item.kind}] {item.name}" for number, item in enumerate(self.elements, start=1))

    def prompt(self, goal: str, history: Sequence[str]) -> str:
        done = "\n".join(f"{number}. {line}" for number, line in enumerate(history, start=1)) or "아직 없음"
        return PROMPT.format(
            goal=goal, history=done, window=self.window, choices=self.lines(), texts="\n".join(self.texts) or "(없음)"
        )


@dataclass(frozen=True)
class Choice:
    action: Action  # 무엇을 하기로 했는지 (끝냈는지, 위험한지를 볼 때 쓴다)
    moves: tuple[tuple[Action, tuple[int, int] | None], ...]  # 실제로 차례대로 할 동작과 누를 자리
    said: str  # '이미 끝낸 동작'에 적을 말
    name: str = ""  # 고른 것의 이름


def _clean(text: str, limit: int) -> str:
    return " ".join(text.split())[:limit]


def _center(rect: tuple[int, int, int, int]) -> tuple[float, float]:
    return (rect[0] + rect[2]) / 2, (rect[1] + rect[3]) / 2


def _in_order(rect: tuple[int, int, int, int]) -> tuple[int, int]:
    """읽는 차례: 위에서 아래로, 같은 줄에서는 왼쪽부터."""
    return rect[1] // SAME_ROW, rect[0]


def _beside(rect: tuple[int, int, int, int], words: Sequence[tuple[str, tuple[int, int, int, int]]]) -> str:
    """같은 줄에서 가장 가까이 있는 글자."""
    nearest: tuple[float, str] | None = None
    for word, other in words:
        if other == rect or abs(_center(other)[1] - _center(rect)[1]) > SAME_ROW:
            continue
        gap = max(other[0] - rect[2], rect[0] - other[2], 0)
        if gap <= NEAR and (nearest is None or gap < nearest[0]):
            nearest = (gap, word)
    return nearest[1] if nearest else ""


def _told_apart(elements: list[Element], words: Sequence[tuple[str, tuple[int, int, int, int]]]) -> list[Element]:
    """이름이 같은 것들에 어느 줄의 것인지, 몇 번째인지를 붙여 구분한다."""
    counts = Counter(item.name for item in elements)
    labelled = []
    for item in elements:
        beside = _beside(item.rect, words) if counts[item.name] > 1 else ""
        beside = "" if beside == item.name else beside
        labelled.append(Element(item.kind, f"{item.name} ({beside[:16]} 줄)" if beside else item.name, item.rect))
    counts, met = Counter(item.name for item in labelled), Counter()
    result = []
    for item in labelled:
        met[item.name] += 1
        again = counts[item.name] > 1
        result.append(Element(item.kind, f"{item.name} ({met[item.name]}번째)" if again else item.name, item.rect))
    return result


def _holds(outer: Element, inner: Element) -> bool:
    def area(rect: tuple[int, int, int, int]) -> int:
        return (rect[2] - rect[0]) * (rect[3] - rect[1])

    x, y = _center(inner.rect)
    around = outer.rect[0] <= x < outer.rect[2] and outer.rect[1] <= y < outer.rect[3]
    return around and area(outer.rect) > area(inner.rect)  # 더 큰 쪽이 품는 쪽이다


def _without_wrappers(elements: list[Element]) -> list[Element]:
    """같은 것을 두 번 세지 않는다.

    웹 페이지는 링크를 '목록 항목'으로 한 번 더 싸서 내주곤 한다. 다른 것을 품고 있는 목록 항목과,
    같은 이름의 더 작은 것을 품고 있는 것은 껍데기로 보고 뺀다.
    """
    kept = []
    for item in elements:
        inside = [other for other in elements if _holds(item, other)]
        shell = (item.kind == WRAPPER and inside) or any(other.name == item.name for other in inside)
        if not shell:
            kept.append(item)
    return kept


def build(seen: Sequence[uia.Seen], area: tuple[int, int, int, int], goal: str, *, window: str = "") -> Listing:
    """읽어 온 것들을 추리고 이름을 다듬어, AI에게 보여 줄 목록으로 만든다."""

    def shown(rect: tuple[int, int, int, int]) -> bool:
        x, y = _center(rect)
        big = rect[2] - rect[0] >= MIN_SIDE and rect[3] - rect[1] >= MIN_SIDE
        return big and area[0] <= x < area[2] and area[1] <= y < area[3]

    visible = sorted((item for item in seen if shown(item.rect)), key=lambda item: _in_order(item.rect))
    words = [(_clean(item.name, TEXT_CHARS), item.rect) for item in visible if item.type == TEXT]
    words = [(word, rect) for word, rect in words if len(word) >= 2]

    elements: list[Element] = []
    spots: set[tuple[str, int, int]] = set()
    for item in visible:
        kind = KINDS.get(item.type)
        if kind is None or not item.enabled or item.password:
            continue
        name = _clean(item.name, NAME_CHARS) or _clean(item.help, NAME_CHARS)
        if not name:
            beside = _beside(item.rect, words)
            if not beside:
                continue  # 이름도 없고 옆에 글자도 없으면 무엇인지 알려 줄 길이 없다
            name = f"이름 없는 {kind} ({beside[:16]} 옆)"
        spot = (name, item.rect[0] // 4, item.rect[1] // 4)
        if spot not in spots:  # 같은 자리에 같은 이름으로 겹쳐 있는 것은 하나만
            spots.add(spot)
            elements.append(Element(kind, name, item.rect))

    elements = _without_wrappers(elements)
    # 너무 많으면 목표의 낱말이 든 것과 입력 칸을 먼저 남긴다.
    keys = [word.lower() for word in re.findall(r"[0-9A-Za-z가-힣]+", goal) if len(word) >= 2]
    elements.sort(key=lambda item: -(sum(key in item.name.lower() for key in keys) + (item.kind == INPUT)))
    kept = sorted(elements[:MAX_CHOICES], key=lambda item: _in_order(item.rect))
    texts = tuple(dict.fromkeys(word for word, _ in words))[:MAX_TEXTS]
    return Listing(_clean(window, TEXT_CHARS), area, tuple(_told_apart(kept, words)), texts)


def parse_choice(raw: object, listing: Listing) -> Choice:
    """AI가 고른 것을 실제로 할 동작들로 바꾼다. 목록에 없는 번호는 받지 않는다."""
    if not isinstance(raw, dict) or raw.get("action") not in CHOICES:
        raise ActionError(f"동작을 알아볼 수 없습니다: {raw!r}")
    kind = raw["action"]
    why = str(raw.get("why") or "").strip()
    risky = raw.get("risky") is True

    if kind in ("press", "type"):
        number = raw.get("number")
        if isinstance(number, bool) or not isinstance(number, int) or not 1 <= number <= len(listing.elements):
            raise ActionError(f"목록에 없는 번호입니다: {number!r}")
        item = listing.elements[number - 1]
        press = Action("click", why, target=item.name, risky=risky)
        if kind == "press":
            return Choice(press, ((press, item.center),), f"'{item.name}'을(를) 눌렀다", item.name)
        text = raw.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ActionError("type에는 text가 필요합니다")
        submit = raw.get("submit") is True
        # 칸을 누르고, 적혀 있던 것을 전부 고른 뒤 새로 넣는다.
        moves = [(press, item.center), (Action("key", why, keys="ctrl+a"), None), (Action("type", why, text=text), None)]
        if submit:
            moves.append((Action("key", why, keys="enter"), None))
        said = f"'{item.name}'에 '{text}'을(를) 넣었다" + (" 그리고 Enter를 눌렀다" if submit else "")
        return Choice(Action("type", why, text=text, target=item.name, risky=risky), tuple(moves), said, item.name)
    if kind == "scroll":
        amount = raw.get("amount")
        amount = amount if isinstance(amount, int) and not isinstance(amount, bool) and amount else -3
        middle = ((listing.area[0] + listing.area[2]) // 2, (listing.area[1] + listing.area[3]) // 2)
        action = Action("scroll", why, amount=amount)
        return Choice(action, ((action, middle),), "화면을 위로 올렸다" if amount > 0 else "화면을 아래로 내렸다")
    if kind in ("key", "open"):
        action = parse_action({"action": kind, "why": why, "keys": raw.get("keys"), "text": raw.get("text"), "risky": risky})
        said = f"{action.keys} 키를 눌렀다" if kind == "key" else f"{action.text}을(를) 열었다"
        return Choice(action, ((action, None),), said)
    action = Action(kind, why)
    return Choice(action, ((action, None),) if kind == "wait" else (), "잠깐 기다렸다")


def look(goal: str) -> Listing | None:
    """맨 앞 창에서 누를 수 있는 것을 읽어 목록으로 만든다. 읽을 수 없으면 None."""
    user32 = ctypes.windll.user32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    seen, page = uia.harvest(hwnd)
    kind = ctypes.create_unicode_buffer(100)
    user32.GetClassNameW(hwnd, kind, len(kind))
    log.info("맨 앞 창(종류 %s)에서 %d개를 읽음%s", kind.value, len(seen), ", 브라우저 페이지 안쪽만 씀" if page else "")
    if not seen:
        return None
    box = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(box))
    title = ctypes.create_unicode_buffer(300)
    user32.GetWindowTextW(hwnd, title, len(title))
    return build(seen, page or (box.left, box.top, box.right, box.bottom), goal, window=title.value)
