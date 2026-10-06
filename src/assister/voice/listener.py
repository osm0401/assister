"""마이크를 듣다가 캐릭터에게 한 말을 골라 전한다.

마이크는 이름 부르기가 켜져 있거나 음성 입력을 시작했을 때만 연다.
들은 소리는 저장하지 않고, 받아쓴 뒤 바로 버린다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Protocol

import numpy as np
from PySide6.QtCore import QObject, Signal

from assister.voice.endpoint import EndpointConfig, Endpointer
from assister.voice.stt import SttError
from assister.voice.wake import VoiceRouter

log = logging.getLogger(__name__)

ARMED_TIMEOUT_SEC = 8.0
JOIN_TIMEOUT_SEC = 3.0


class Microphone(Protocol):
    def read(self, count: int) -> np.ndarray: ...
    def close(self) -> None: ...


class SpeechToText(Protocol):
    def load(self) -> None: ...
    def transcribe(self, samples: np.ndarray) -> str: ...


class _SoundDeviceMicrophone:
    def __init__(self, config: EndpointConfig) -> None:
        import sounddevice as sd

        self._error = sd.PortAudioError
        try:
            self._stream = sd.InputStream(
                samplerate=config.sample_rate, channels=1, dtype="float32", blocksize=config.frame_samples
            )
            self._stream.start()
        except sd.PortAudioError as error:
            raise OSError(str(error)) from error

    def read(self, count: int) -> np.ndarray:
        try:
            data, _overflowed = self._stream.read(count)
        except self._error as error:
            raise OSError(str(error)) from error
        return data[:, 0].copy()

    def close(self) -> None:
        try:
            self._stream.stop()
            self._stream.close()
        except self._error as error:
            log.warning("마이크를 닫는 중 오류: %s", error)


def open_microphone(config: EndpointConfig) -> Microphone:
    return _SoundDeviceMicrophone(config)


class MicListener(QObject):
    heard = Signal(str, str)  # 들은 말, 경로("wake" | "wake_only" | "command")
    state_changed = Signal(str)  # "off" | "waiting" | "armed" | "hearing" | "thinking"
    failed = Signal(str)

    def __init__(
        self,
        transcriber: SpeechToText,
        router: VoiceRouter,
        *,
        open_microphone: Callable[[EndpointConfig], Microphone] = open_microphone,
        config: EndpointConfig | None = None,
        armed_timeout: float = ARMED_TIMEOUT_SEC,
    ) -> None:
        super().__init__()
        self._transcriber = transcriber
        self._router = router
        self._open = open_microphone
        self._config = config or EndpointConfig()
        self._armed_timeout = armed_timeout
        self._changed = threading.Event()
        self._paused = False
        self._stopping = False
        self._armed_at = 0.0
        self._state: str | None = None
        self._thread = threading.Thread(target=self._run, name="mic-listener", daemon=True)

    @property
    def wake_enabled(self) -> bool:
        return self._router.wake_enabled

    def start(self) -> None:
        self._thread.start()

    def set_wake_enabled(self, enabled: bool) -> None:
        self._router.wake_enabled = enabled
        self._changed.set()

    def arm(self) -> None:
        """다음 한 마디를 이름 없이도 받아들인다 (단축키, 마이크 버튼)."""
        self._router.arm()
        self._armed_at = time.monotonic()
        self._changed.set()

    def pause(self) -> None:
        """캐릭터가 말하는 동안에는 자기 목소리를 듣지 않게 귀를 막는다."""
        self._paused = True

    def resume(self) -> None:
        self._paused = False

    def shutdown(self) -> None:
        self._stopping = True
        self._changed.set()
        if self._thread.is_alive():
            self._thread.join(JOIN_TIMEOUT_SEC)

    def _set_state(self, state: str) -> None:
        if state != self._state:
            self._state = state
            self.state_changed.emit(state)

    def _give_up(self, message: str) -> None:
        log.warning("%s", message)
        self.failed.emit(message)
        self._router.wake_enabled = False
        self._router.disarm()

    def _run(self) -> None:
        endpointer = Endpointer(self._config)
        microphone: Microphone | None = None
        loaded = False
        try:
            while not self._stopping:
                if not (self._router.wake_enabled or self._router.armed):
                    if microphone is not None:
                        microphone.close()
                        microphone = None
                    self._set_state("off")
                    self._changed.wait()
                    self._changed.clear()
                    continue

                if not loaded:
                    # 받아쓰기 모델을 올리는 데 몇 초 걸린다. 마이크를 열기 전에 해 둬야 소리가 밀리지 않는다.
                    try:
                        self._transcriber.load()
                    except SttError as error:
                        self._give_up(str(error))
                        continue
                    loaded = True

                if microphone is None:
                    try:
                        microphone = self._open(self._config)
                    except OSError as error:
                        self._give_up(f"마이크를 열 수 없습니다: {error}")
                        continue
                    endpointer.reset()

                try:
                    frame = microphone.read(self._config.frame_samples)
                except OSError as error:
                    microphone.close()
                    microphone = None
                    self._give_up(f"마이크에서 소리를 읽지 못했습니다: {error}")
                    continue

                if self._paused:
                    endpointer.reset()
                    continue

                waited_too_long = time.monotonic() - self._armed_at > self._armed_timeout
                if self._router.armed and not endpointer.in_speech and waited_too_long:
                    self._router.disarm()
                    continue

                utterance = endpointer.feed(frame)
                if utterance is None:
                    idle = "armed" if self._router.armed else "waiting"
                    self._set_state("hearing" if endpointer.in_speech else idle)
                    continue

                self._set_state("thinking")
                try:
                    text = self._transcriber.transcribe(utterance)
                except SttError as error:
                    self._give_up(str(error))
                    continue
                if self._paused:
                    continue
                result = self._router.on_transcript(text)
                if result is not None:
                    if result.via == "wake_only":
                        self._armed_at = time.monotonic()
                    self.heard.emit(result.text, result.via)
        finally:
            if microphone is not None:
                microphone.close()
            self._set_state("off")
