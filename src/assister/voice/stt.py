"""마이크로 들은 말을 글로 받아쓴다. 내 PC 안에서만 처리한다 (faster-whisper)."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Protocol

import numpy as np

from assister.voice.cuda import register_nvidia_dlls

log = logging.getLogger(__name__)

MAX_NO_SPEECH_PROB = 0.6
MIN_AVG_LOGPROB = -1.2
# 조용하거나 잡음뿐일 때 Whisper가 지어내곤 하는 말. 빈칸을 빼고 비교한다.
HALLUCINATIONS = (
    "시청해주셔서감사합니다",
    "구독과좋아요",
    "다음영상에서만나요",
    "MBC뉴스",
    "한글자막",
    "자막제공",
)
# 아무 소리도 없을 때 통째로 튀어나오는 말. 이 말만 있을 때에만 버린다.
SILENCE_WORDS = frozenset({"아멘"})


class SttError(Exception):
    """받아쓰기를 할 수 없을 때."""


class _Segment(Protocol):
    text: str
    no_speech_prob: float
    avg_logprob: float


def usable_text(segments: Iterable[_Segment]) -> str:
    """믿을 만한 부분만 이어 붙인다. 지어낸 말로 보이면 빈 글을 돌려준다."""
    parts = [
        segment.text.strip()
        for segment in segments
        if segment.no_speech_prob <= MAX_NO_SPEECH_PROB and segment.avg_logprob >= MIN_AVG_LOGPROB
    ]
    text = " ".join(part for part in parts if part)
    squeezed = text.replace(" ", "")
    if squeezed.strip(".!?") in SILENCE_WORDS or any(phrase in squeezed for phrase in HALLUCINATIONS):
        return ""
    return text


class Transcriber:
    def __init__(
        self, model: str = "large-v3-turbo", *, device: str = "cuda", language: str = "ko", hotwords: str = ""
    ) -> None:
        self._model_name = model
        self._device = device
        self._language = language
        self._hotwords = hotwords or None
        self._model = None

    @property
    def device(self) -> str:
        return self._device

    def load(self) -> None:
        """모델을 메모리에 올린다. 그래픽카드로 안 되면 CPU로 바꿔서 올린다."""
        from faster_whisper import WhisperModel

        if self._device == "cuda":
            register_nvidia_dlls()
            try:
                self._model = WhisperModel(self._model_name, device="cuda", compute_type="float16")
                return
            except (RuntimeError, ValueError, OSError) as error:
                log.warning("그래픽카드로 음성 인식을 올리지 못해 CPU로 바꿉니다: %s", error)
                self._device = "cpu"
        try:
            self._model = WhisperModel(self._model_name, device="cpu", compute_type="int8")
        except (RuntimeError, ValueError, OSError) as error:
            raise SttError(f"음성 인식 모델을 올리지 못했습니다: {error}") from error

    def transcribe(self, samples: np.ndarray) -> str:
        """16kHz 소리를 받아 글로 돌려준다."""
        if self._model is None:
            self.load()
        try:
            return self._run(samples)
        except RuntimeError as error:
            if self._device != "cuda":
                raise SttError(f"받아쓰기에 실패했습니다: {error}") from error
            # 그래픽카드 라이브러리는 처음 쓸 때에야 문제가 드러나기도 한다.
            log.warning("그래픽카드 받아쓰기에 실패해 CPU로 바꿉니다: %s", error)
            self._device = "cpu"
            self.load()
            try:
                return self._run(samples)
            except RuntimeError as retry_error:
                raise SttError(f"받아쓰기에 실패했습니다: {retry_error}") from retry_error

    def _run(self, samples: np.ndarray) -> str:
        segments, _ = self._model.transcribe(
            samples,
            language=self._language,
            beam_size=5,
            # 말소리가 아닌 구간을 먼저 걸러 내서, 조용할 때 말을 지어내는 일을 줄인다.
            vad_filter=True,
            hotwords=self._hotwords,
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        return usable_text(segments)
