"""AI 답변을 화면이 멈추지 않게 뒤에서 만든다."""

from __future__ import annotations

import logging
import queue
import threading

from PySide6.QtCore import QObject, Signal

from assister.ai.chat import ChatSession
from assister.ai.engine import EngineError

log = logging.getLogger(__name__)

JOIN_TIMEOUT_SEC = 3.0
UNEXPECTED = "답을 만들다가 문제가 생겼어요."
_WARM_UP = object()  # 줄에 넣는 표시. 사용자의 말이 아니라 '모델을 미리 올려라'는 뜻이다.


class ChatWorker(QObject):
    ready = Signal()  # AI 모델이 메모리에 올라옴
    warm_up_failed = Signal(str)
    started = Signal(str)  # 지금 답하려는 사용자의 말
    delta = Signal(str)  # 새로 나온 글 조각
    tool_used = Signal(str)  # 부른 도구 이름
    finished = Signal(str)  # 다 된 답
    failed = Signal(str)  # 사용자에게 보여 줄 오류 설명
    confirm_needed = Signal(str)  # 해도 되는지 사용자에게 물어볼 일
    action_taken = Signal(str)  # 실행한 명령이나 검색어. 대화 기록에 한 줄로 남긴다

    def __init__(self, session: ChatSession) -> None:
        super().__init__()
        self._session = session
        self._requests: queue.Queue[object] = queue.Queue()
        self._cancelled = False
        self._stopping = False
        self._answered = threading.Event()
        self._allowed = False
        self._thread = threading.Thread(target=self._run, name="chat-worker", daemon=True)
        self._thread.start()

    def ask(self, text: str) -> None:
        """말을 줄 세운다. 앞의 답이 끝나면 차례대로 답한다."""
        self._requests.put(text)

    def warm_up(self) -> None:
        """AI 모델을 미리 올려 둔다. 끝나면 ready, 안 되면 warm_up_failed 신호를 보낸다."""
        self._requests.put(_WARM_UP)

    def _warm_up(self) -> None:
        try:
            self._session.warm_up()
        except EngineError as error:
            log.warning("%s", error)
            self.warm_up_failed.emit(str(error))
            return
        self.ready.emit()
        try:
            # 켠 김에 지난 날의 대화를 요약해 둔다. 안 되면 다음에 켤 때 다시 한다.
            self._session.digest()
        except EngineError as error:
            log.warning("지난 대화를 요약하지 못했습니다: %s", error)

    def cancel(self) -> None:
        """지금 만들고 있는 답을 중간에 멈춘다."""
        self._cancelled = True

    def confirm(self, text: str) -> bool:
        """사용자에게 허락을 구하고 답이 올 때까지 기다린다. 답을 만드는 스레드에서 부른다."""
        self._answered.clear()
        self._allowed = False
        if self._stopping:
            return False
        self.confirm_needed.emit(text)
        self._answered.wait()
        return self._allowed and not self._stopping

    def answer(self, allowed: bool) -> None:
        self._allowed = allowed
        self._answered.set()

    def shutdown(self) -> None:
        self._stopping = True
        self._answered.set()  # 허락을 기다리던 중이면 거절로 끝낸다
        self._requests.put(None)
        self._thread.join(JOIN_TIMEOUT_SEC)

    def _run(self) -> None:
        while (text := self._requests.get()) is not None:
            if text is _WARM_UP:
                self._warm_up()
                continue
            self._cancelled = False
            self.started.emit(text)
            try:
                reply = self._session.reply(
                    text,
                    on_delta=self.delta.emit,
                    on_tool=self.tool_used.emit,
                    on_action=self.action_taken.emit,
                    should_stop=lambda: self._cancelled or self._stopping,
                )
            except EngineError as error:
                log.warning("%s", error)
                self.failed.emit(str(error))
            except Exception:  # noqa: BLE001 (여기서 놓치면 스레드가 죽어 다시는 답하지 못한다)
                log.exception("답을 만드는 중 예상하지 못한 오류")
                self.failed.emit(UNEXPECTED)
            else:
                self.finished.emit(reply)
