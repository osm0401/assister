"""캐릭터 목소리. Edge TTS로 합성해서 스피커로 낸다.

Edge TTS는 읽을 문장을 Microsoft 서버로 보내 목소리를 받아 온다.
"""

from __future__ import annotations

import asyncio
import io
import logging
import queue
import threading
from collections.abc import Callable

import numpy as np
from PySide6.QtCore import QObject, Signal

from assister.character.pack import Voice

log = logging.getLogger(__name__)

SAMPLE_RATE = 24000
JOIN_TIMEOUT_SEC = 2.0


class TtsError(Exception):
    """목소리를 만들거나 낼 수 없을 때."""


Clip = tuple[np.ndarray, int]


def decode_mp3(data: bytes, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    """MP3를 한 줄짜리 소리 배열(-1~1)로 푼다."""
    import av

    resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)
    chunks: list[np.ndarray] = []
    try:
        with av.open(io.BytesIO(data), mode="r") as container:
            for frame in container.decode(audio=0):
                chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(frame))
            chunks.extend(out.to_ndarray().reshape(-1) for out in resampler.resample(None))
    except (av.error.FFmpegError, IndexError, ValueError) as error:
        raise TtsError(f"받아 온 목소리를 풀지 못했습니다: {error}") from error
    if not chunks:
        raise TtsError("받아 온 목소리가 비어 있습니다")
    return np.concatenate(chunks).astype(np.float32)


def synthesize(text: str, voice: Voice) -> Clip:
    """문장 하나를 목소리로 바꾼다. 인터넷 연결이 필요하다."""
    import aiohttp
    import edge_tts
    from edge_tts.exceptions import EdgeTTSException

    async def collect() -> bytes:
        communicate = edge_tts.Communicate(text, voice.name, rate=voice.rate, pitch=voice.pitch)
        return b"".join([chunk["data"] async for chunk in communicate.stream() if chunk["type"] == "audio"])

    try:
        mp3 = asyncio.run(collect())
    except (EdgeTTSException, aiohttp.ClientError, OSError, ValueError) as error:  # 시간 초과는 OSError에 든다
        raise TtsError(f"목소리를 받아 오지 못했습니다: {error}") from error
    if not mp3:
        raise TtsError("목소리를 받아 오지 못했습니다")
    return decode_mp3(mp3), SAMPLE_RATE


def play(samples: np.ndarray, sample_rate: int) -> None:
    """소리가 끝날 때까지 기다린다."""
    import sounddevice as sd

    try:
        sd.play(samples, sample_rate)
        sd.wait()
    except sd.PortAudioError as error:
        raise TtsError(f"스피커로 소리를 내지 못했습니다: {error}") from error


def stop_playing() -> None:
    import sounddevice as sd

    sd.stop()


class Speaker(QObject):
    """문장을 받는 대로 줄 세워 읽는다. 앞 문장을 읽는 동안 다음 문장을 미리 합성한다."""

    started = Signal()
    finished = Signal()
    failed = Signal(str)

    def __init__(
        self,
        voice: Voice,
        *,
        synthesize: Callable[[str, Voice], Clip] = synthesize,
        play: Callable[[np.ndarray, int], None] = play,
        stop: Callable[[], None] = stop_playing,
    ) -> None:
        super().__init__()
        self.voice = voice
        self._synthesize = synthesize
        self._play = play
        self._stop_playing = stop
        self._texts: queue.Queue = queue.Queue()
        self._clips: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._generation = 0  # stop()을 부르면 올라가고, 그 전에 줄 선 문장은 버려진다
        self._pending = 0
        self._speaking = False
        self._threads = [
            threading.Thread(target=self._synthesize_loop, name="tts-synthesize", daemon=True),
            threading.Thread(target=self._play_loop, name="tts-play", daemon=True),
        ]
        for thread in self._threads:
            thread.start()

    def say(self, text: str) -> None:
        with self._lock:
            self._pending += 1
            generation = self._generation
        self._texts.put((generation, text))

    def stop(self) -> None:
        """읽던 것을 멈추고 기다리던 문장을 버린다."""
        with self._lock:
            self._generation += 1
        self._stop_playing()

    def shutdown(self) -> None:
        self.stop()
        self._texts.put(None)
        for thread in self._threads:
            thread.join(JOIN_TIMEOUT_SEC)

    def _current(self, generation: int) -> bool:
        with self._lock:
            return generation == self._generation

    def _synthesize_loop(self) -> None:
        while (item := self._texts.get()) is not None:
            generation, text = item
            if not self._current(generation):
                self._one_done()
                continue
            try:
                clip = self._synthesize(text, self.voice)
            except TtsError as error:
                log.warning("%s", error)
                self.failed.emit(str(error))
                self._one_done()
                continue
            self._clips.put((generation, clip))
        self._clips.put(None)

    def _play_loop(self) -> None:
        while (item := self._clips.get()) is not None:
            generation, (samples, sample_rate) = item
            if self._current(generation):
                self._mark_started()
                try:
                    self._play(samples, sample_rate)
                except TtsError as error:
                    log.warning("%s", error)
                    self.failed.emit(str(error))
            self._one_done()

    def _mark_started(self) -> None:
        with self._lock:
            if self._speaking:
                return
            self._speaking = True
        self.started.emit()

    def _one_done(self) -> None:
        # 소리를 한 번도 내지 못했더라도(합성 실패, 중단) 줄 선 것이 다 비면 끝났다고 알린다.
        # 기다리던 쪽이 영영 멈춰 있지 않게 하기 위해서다.
        with self._lock:
            self._pending -= 1
            ended = self._pending == 0
            if ended:
                self._speaking = False
        if ended:
            self.finished.emit()
