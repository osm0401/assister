"""마이크 소리에서 말이 시작되고 끝나는 곳을 찾는다.

소리 크기만 보는 간단한 방식이다. 주변 소음 크기를 계속 재서, 그보다 뚜렷이 큰
소리가 이어지면 말이 시작된 것으로, 조용한 시간이 이어지면 끝난 것으로 본다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class EndpointConfig:
    sample_rate: int = 16000
    frame_ms: int = 30
    start_frames: int = 3  # 이만큼 연속으로 큰 소리가 나야 말로 본다
    end_silence_ms: int = 700
    pre_roll_ms: int = 300  # 말 첫머리가 잘리지 않게 앞부분을 조금 붙인다
    min_speech_ms: int = 250
    max_speech_ms: int = 15000
    min_threshold: float = 0.008
    noise_ratio: float = 3.0

    @property
    def frame_samples(self) -> int:
        return self.sample_rate * self.frame_ms // 1000


class Endpointer:
    def __init__(self, config: EndpointConfig | None = None) -> None:
        self._config = config or EndpointConfig()
        self._noise_floor = 0.0
        self._pre_roll: deque[np.ndarray] = deque(maxlen=self._config.pre_roll_ms // self._config.frame_ms)
        self.reset()

    @property
    def in_speech(self) -> bool:
        return self._speaking

    def reset(self) -> None:
        """듣던 것을 버린다. 소음 크기는 그대로 기억한다."""
        self._speaking = False
        self._loud_run = 0
        self._silent_run = 0
        self._frames: list[np.ndarray] = []
        self._levels: list[float] = []
        self._pre_roll.clear()

    def feed(self, frame: np.ndarray) -> np.ndarray | None:
        """소리 한 토막을 받는다. 말 한 마디가 끝났으면 그 소리를 돌려준다."""
        config = self._config
        level = float(np.sqrt(np.mean(np.square(frame, dtype=np.float64))))
        loud = level >= max(config.min_threshold, self._noise_floor * config.noise_ratio)

        if not self._speaking:
            self._pre_roll.append(frame)
            if loud:
                self._loud_run += 1
            else:
                self._loud_run = 0
                self._noise_floor = 0.95 * self._noise_floor + 0.05 * level
            if self._loud_run >= config.start_frames:
                self._speaking = True
                self._frames = list(self._pre_roll)
                self._levels = [level] * self._loud_run
                self._silent_run = 0
            return None

        self._frames.append(frame)
        if loud:
            self._levels.append(level)
            self._silent_run = 0
        else:
            self._silent_run += 1

        too_long = len(self._frames) * config.frame_ms >= config.max_speech_ms
        if self._silent_run * config.frame_ms < config.end_silence_ms and not too_long:
            return None

        frames, levels = self._frames, self._levels
        self.reset()
        if too_long:
            # 끝없이 이어지는 소리는 말이 아니라 소음일 가능성이 크다. 그 크기를 소음으로 기억한다.
            self._noise_floor = max(self._noise_floor, float(np.median(levels)))
        if len(levels) * config.frame_ms < config.min_speech_ms:
            return None
        return np.concatenate(frames)
