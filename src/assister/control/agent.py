"""화면을 보고 한 동작씩 PC를 조작한다.

보기 → AI가 다음 동작 하나를 고름 → 위험하면 사용자에게 확인 → 실행, 을 목표를 이룰 때까지 되풀이한다.
사용자는 언제든 멈출 수 있고(stop), 가려 둔 창이 앞에 있으면 조작하지 않는다.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import replace
from typing import Protocol

from PySide6.QtCore import QObject, Signal

from assister.ai.engine import ChatEngine, EngineError
from assister.control.actions import (
    ACTION_FORMAT,
    POINTER_KINDS,
    Action,
    ActionError,
    describe,
    monitor_in_use,
    parse_action,
    risk_reason,
    to_screen,
)
from assister.control.elements import MIN_CHOICES, CHOICE_FORMAT, Choice, Listing, parse_choice
from assister.screen.capture import Snapshot

log = logging.getLogger(__name__)

MAX_BAD_ANSWERS = 2
OPEN_SETTLE_FACTOR = 3  # 프로그램을 연 뒤에는 평소의 몇 배를 기다린다
WINDOW_SETTLE_SEC = 0.3  # 창이 자리 잡았는지 다시 찍어 보는 간격
MAX_SETTLE_LOOKS = 6
STOPPED = "멈췄어요."
REFUSED = "알겠어요, 하지 않을게요."
HIDDEN = "가려 둔 창이 앞에 있어서 대신 조작하지 않을게요."
OUT_OF_STEPS = "여기까지 해 봤는데 끝내지 못했어요."
STUCK = "같은 동작만 되풀이하게 돼서 멈췄어요."
MAX_REPEATS = 3  # 똑같은 동작을 이만큼 잇달아 했으면 그다음에는 하지 않고 멈춘다
# 잇달아 해도 이상하지 않은 동작. 긴 글을 내리거나 Tab으로 칸을 옮길 때가 그렇다.
REPEATABLE = ("scroll", "wait", "key")
SAME_SPOT = 30  # 좌표가 이만큼(천분율) 안에서 다르면 같은 자리로 본다
TYPE_SETTLE_SEC = 0.15  # 칸을 누르고, 고르고, 글자를 넣는 사이에 두는 틈
CONFUSED = "어떻게 해야 할지 모르겠어요."

PROMPT = """너는 사용자의 Windows PC를 대신 조작한다. 목표를 이루기 위해 지금 할 동작 하나만 JSON으로 낸다.

{screens} 좌표는 그 그림의 왼쪽 위가 (0, 0), 오른쪽 아래가 (1000, 1000)이다.

- click, double_click: monitor, x, y와 target(누를 것에 적힌 이름)
- type: text. 지금 커서가 있는 곳에 글자를 넣는다. 먼저 넣을 곳을 눌러 둔다.
- key: keys. 예: enter, tab, ctrl+s, win
- scroll: monitor, x, y, amount(양수는 위로, 음수는 아래로)
- open: text에 프로그램 이름이나 웹 주소. 예: notepad, https://www.naver.com
- wait: 화면이 바뀌기를 기다린다
- done: 목표를 이뤘다. why에 결과를 한 문장으로 적는다.
- fail: 할 수 없다. why에 이유를 적는다.

why에는 지금 하려는 일을 사용자에게 보여 줄 한국어 한 문장으로 적는다.
삭제, 결제, 메시지나 메일 전송, 설정 변경, 프로그램 설치, 파일 덮어쓰기, 저장하지 않고 닫기에 해당하면 risky를 true로 한다.
검게 칠한 곳은 가린 창이니 건드리지 않는다.

아래 [이미 끝낸 동작]은 실제로 실행된 것이다. 같은 동작을 다시 하지 않고, 그다음 동작을 고른다.
목표에 필요한 동작이 [이미 끝낸 동작]에 모두 있으면 done으로 끝낸다.

[목표] {goal}

[이미 끝낸 동작]
{history}"""
ONE_WINDOW = "화면 그림은 사용자가 지금 쓰고 있는 창 하나를 잘라 낸 것이다. monitor는 0으로 둔다. 필요한 것이 이 창에 없으면 fail로 답한다."
ONE_SCREEN = "화면 그림은 사용자가 지금 쓰고 있는 모니터 한 장이다. monitor는 0으로 둔다. 필요한 것이 이 화면에 없으면 fail로 답한다."
ALL_SCREENS = "화면 그림은 모니터마다 한 장이고 첫 장이 0번 모니터다."


class Executor(Protocol):
    def monitors(self) -> list[tuple[int, int, int, int]]: ...
    def element_name(self, x: int, y: int) -> str: ...
    def perform(self, action: Action, point: tuple[int, int] | None) -> None:
        """동작을 실제로 한다. point는 누르거나 굴릴 자리의 화면 좌표다 (그런 동작이 아니면 None)."""


class ControlAgent(QObject):
    started = Signal(str)  # 목표
    step = Signal(str)  # 지금 하는 일 한 문장
    confirm_needed = Signal(str)  # 확인받을 일. answer(True/False)로 답한다.
    finished = Signal(str, bool)  # 마무리 말, 목표를 이뤘는지

    def __init__(
        self,
        engine: ChatEngine,
        executor: Executor,
        *,
        take_snapshot: Callable[[Sequence[str], int], Snapshot],
        keywords: Callable[[], Sequence[str]],
        width: int,
        max_steps: int,
        settle_sec: float = 0.8,
        chooser: ChatEngine | None = None,
        list_elements: Callable[[str], Listing | None] | None = None,
    ) -> None:
        """chooser와 list_elements를 주면, 화면 그림 대신 누를 수 있는 것의 목록을 보여 주고 번호로 고르게 한다.

        chooser는 그 목록에서 고르는 AI다(글만 읽으면 된다). 목록을 내주지 않는 창에서는 engine이 그림을 보고 조작한다.
        """
        super().__init__()
        self._engine = engine
        self._chooser = chooser
        self._list_elements = list_elements
        self._executor = executor
        self._take_snapshot = take_snapshot
        self._keywords = keywords
        self._width = width
        self._max_steps = max_steps
        self._settle_sec = settle_sec
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._answered = threading.Event()
        self._allowed = False

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def run(self, goal: str) -> bool:
        """조작을 시작한다. 이미 다른 일을 하는 중이면 False."""
        with self._lock:
            if self.running:
                return False
            self._stop.clear()
            self._thread = threading.Thread(target=self._work, args=(goal,), name="control-agent", daemon=True)
            self._thread.start()
        return True

    def stop(self) -> None:
        """다음 동작을 하기 전에 멈춘다. 확인을 기다리는 중이었다면 하지 않는 것으로 친다."""
        self._stop.set()
        self._answered.set()

    def answer(self, allowed: bool) -> None:
        self._allowed = allowed
        self._answered.set()

    def join(self, timeout: float = 3.0) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def _work(self, goal: str) -> None:
        self.started.emit(goal)
        try:
            message, ok = self._pursue(goal)
        except EngineError as error:
            message, ok = str(error), False
        except OSError as error:
            log.warning("PC 조작 중 오류: %s", error)
            message, ok = f"조작하다가 문제가 생겼어요: {error}", False
        self.finished.emit(message, ok)

    def _pursue(self, goal: str) -> tuple[str, bool]:
        history: list[str] = []
        bad_answers = 0
        last, repeats = None, 0
        pressed_on: tuple[str, ...] | None = None  # 마지막으로 무언가를 눌렀을 때의 목록
        # 그림 한 장을 읽는 데 몇 초씩 걸려서, 처음에는 지금 쓰는 모니터만 보여 준다.
        # 거기서 못 찾겠다고 하면 그때부터 모든 모니터를 보여 준다.
        widened = 0
        for _ in range(self._max_steps):
            if self._stop.is_set():
                return STOPPED, False
            snapshot = self._look()
            if snapshot.skipped:
                return HIDDEN, False

            listing = self._listing(goal)
            if listing is not None:
                names = tuple(item.name for item in listing.elements)
                if names == pressed_on:
                    history.append("(방금 누른 뒤에도 화면이 그대로다)")
                pressed_on = None
                try:
                    choice = self._choose(goal, history, listing)
                except ActionError as error:
                    bad_answers += 1
                    log.warning("쓸 수 없는 동작: %s", error)
                    if bad_answers > MAX_BAD_ANSWERS:
                        return CONFUSED, False
                    history.append(f"(쓸 수 없는 동작이었다: {error})")
                    continue
                outcome = self._carry_out(choice, snapshot, history)
                if outcome is not None:
                    return outcome
                what = (choice.action.kind, choice.name or choice.action.text or choice.action.keys)
                repeats = repeats + 1 if what == last and choice.action.kind not in REPEATABLE else 0
                last = what
                if repeats >= MAX_REPEATS:
                    return STUCK, False
                if choice.action.kind == "click":
                    pressed_on = names
                continue

            monitors = self._executor.monitors()
            in_use = monitor_in_use(snapshot.foreground, monitors)
            # (설명, 그림들, 그림마다 화면에서 차지하는 자리)
            views = [(ALL_SCREENS, snapshot.images, list(monitors))]
            if len(snapshot.images) > 1 and in_use < len(snapshot.images):
                views.insert(0, (ONE_SCREEN, [snapshot.images[in_use]], [monitors[in_use]]))
            can_widen = widened < len(views) - 1
            hint, images, areas = views[min(widened, len(views) - 1)]

            done = "\n".join(f"{number}. {line}" for number, line in enumerate(history, start=1)) or "아직 없음"
            prompt = PROMPT.format(goal=goal, history=done, screens=hint)
            raw = self._engine.complete_json([{"role": "user", "content": prompt, "images": images}], ACTION_FORMAT)
            try:
                action = parse_action(raw)
                if len(images) == 1:
                    # 보여 준 그림이 한 장뿐이니, AI가 몇 번 모니터라고 답하든 그 그림 안에서 한 동작이다.
                    action = replace(action, monitor=0)
                point = to_screen(areas, action.monitor, action.x, action.y) if action.kind in POINTER_KINDS else None
            except ActionError as error:
                bad_answers += 1
                log.warning("쓸 수 없는 동작: %s (%r)", error, raw)
                if bad_answers > MAX_BAD_ANSWERS:
                    return CONFUSED, False
                history.append(f"(쓸 수 없는 동작이었다: {error})")
                continue

            if action.kind == "done":
                return action.why or "다 했어요.", True
            if action.kind == "fail":
                if can_widen:
                    widened += 1
                    continue
                return action.why or CONFUSED, False

            # 같은 것만 계속 누르면(실제로 12번을 그렇게 다 썼다) 일찍 그만둔다.
            what = (action.kind, action.target or action.text or (action.x // SAME_SPOT, action.y // SAME_SPOT))
            repeats = repeats + 1 if what == last and action.kind not in REPEATABLE else 0
            last = what
            if repeats >= MAX_REPEATS:
                return STUCK, False

            if action.kind in ("click", "double_click"):
                point = self._aim_again(action, point, snapshot) or point

            element = self._executor.element_name(*point) if action.kind in ("click", "double_click") else ""
            reason = risk_reason(action, element_name=element, foreground=snapshot.foreground)
            if reason and not self._ask(f"{action.why} ({reason})"):
                return (STOPPED if self._stop.is_set() else REFUSED), False
            if self._stop.is_set():
                return STOPPED, False

            self.step.emit(action.why)
            self._executor.perform(action, point)
            history.append(describe(action))
            log.info("조작: %s", history[-1])
            # 화면이 바뀔 틈을 준다. 프로그램을 연 뒤에는 뜨는 데 시간이 더 걸린다. 그동안에도 멈춤은 바로 듣는다.
            self._stop.wait(self._settle_sec * (OPEN_SETTLE_FACTOR if action.kind == "open" else 1))
        return OUT_OF_STEPS, False

    def _listing(self, goal: str) -> Listing | None:
        """맨 앞 창에서 누를 수 있는 것의 목록. 목록으로 조작할 수 없는 창이면 None(그림 방식으로 한다)."""
        if self._chooser is None or self._list_elements is None:
            return None
        listing = self._list_elements(goal)
        if listing is None or len(listing.elements) < MIN_CHOICES:
            log.info("이 창은 누를 수 있는 것을 알려 주지 않아 화면 그림으로 조작합니다")
            return None
        return listing

    def _choose(self, goal: str, history: list[str], listing: Listing) -> Choice:
        """목표와 목록을 주고 AI에게 번호 하나를 고르게 한다. 무엇을 보냈고 무엇을 골랐는지 기록에 남긴다."""
        log.info(
            "[맨 앞 창] %s | 누를 수 있는 것 %d개, 화면의 글 %d줄\n%s",
            listing.window, len(listing.elements), len(listing.texts), listing.lines(),
        )  # fmt: skip
        raw = self._chooser.complete_json([{"role": "user", "content": listing.prompt(goal, history)}], CHOICE_FORMAT)
        choice = parse_choice(raw, listing)
        log.info("[고른 것] %s %s — %s", choice.action.kind, choice.name or choice.action.text or choice.action.keys, choice.action.why)
        return choice

    def _carry_out(self, choice: Choice, snapshot: Snapshot, history: list[str]) -> tuple[str, bool] | None:
        """고른 동작을 실제로 한다. 조작을 끝내야 하면 (마무리 말, 성공 여부)를, 계속하면 None을 돌려준다."""
        action = choice.action
        if action.kind == "done":
            return action.why or "다 했어요.", True
        if action.kind == "fail":
            return action.why or CONFUSED, False
        reasons = [risk_reason(move, element_name=choice.name, foreground=snapshot.foreground) for move, _ in choice.moves]
        reason = next((reason for reason in [*reasons, "AI가 조심해야 한다고 봄" if action.risky else ""] if reason), "")
        if reason and not self._ask(f"{action.why} ({reason})"):
            return (STOPPED if self._stop.is_set() else REFUSED), False
        if self._stop.is_set():
            return STOPPED, False
        self.step.emit(action.why)
        for move, point in choice.moves:
            self._executor.perform(move, point)
            self._stop.wait(TYPE_SETTLE_SEC)
        history.append(choice.said)
        log.info("조작: %s", choice.said)
        self._stop.wait(self._settle_sec * (OPEN_SETTLE_FACTOR if action.kind == "open" else 1))
        return None

    def _aim_again(self, action: Action, point: tuple[int, int], snapshot: Snapshot) -> tuple[int, int] | None:
        """누를 자리가 쓰는 창 밖으로 빗나갔으면, 그 창만 크게 보여 주고 자리를 다시 짚게 한다.

        무엇을 누를지는 화면 전체를 보고 판단할 때가 더 맞고, 어디를 누를지는 창만 크게 볼 때가
        더 정확했다(실제 모델로 계산기 버튼 7개 중 7개). 창 안에 없다고 하면 처음 자리를 그대로 쓴다.
        """
        area = snapshot.focus_rect
        if not (snapshot.focus and area and action.target):
            return None
        if area[0] <= point[0] < area[2] and area[1] <= point[1] < area[3]:
            return None
        prompt = PROMPT.format(goal=f"'{action.target}'을(를) 눌러 줘", history="아직 없음", screens=ONE_WINDOW)
        raw = self._engine.complete_json([{"role": "user", "content": prompt, "images": [snapshot.focus]}], ACTION_FORMAT)
        try:
            again = parse_action(raw)
            if again.kind not in ("click", "double_click"):
                return None
            return to_screen([area], 0, again.x, again.y)
        except ActionError:
            return None

    def _look(self) -> Snapshot:
        """화면을 찍는다. 맨 앞 창이 아직 움직이거나 커지는 중이면 자리 잡을 때까지 다시 찍는다."""
        snapshot = self._take_snapshot(self._keywords(), self._width)
        for _ in range(MAX_SETTLE_LOOKS):
            if snapshot.skipped or self._settle_sec <= 0 or self._stop.wait(min(self._settle_sec, WINDOW_SETTLE_SEC)):
                break
            again = self._take_snapshot(self._keywords(), self._width)
            settled = again.skipped or again.focus_rect == snapshot.focus_rect
            snapshot = again
            if settled:
                break
        return snapshot

    def _ask(self, question: str) -> bool:
        self._answered.clear()
        self._allowed = False
        self.confirm_needed.emit(question)
        self._answered.wait()
        return self._allowed and not self._stop.is_set()
