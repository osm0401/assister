"""받아쓴 말에서 캐릭터 이름을 불렀는지 알아낸다."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# 이름 앞에 '어', '야' 같은 짧은 말은 허용한다. 긴 문장 중간에 이름이 나온 것은 부른 것이 아니다.
MAX_PREFIX_CHARS = 4
VOCATIVE = "(?:이야|야|아|님|씨)?"
BOUNDARY = r"(?:[\s,.!?~…]+|$)"


@dataclass(frozen=True)
class WakeMatch:
    command: str  # 이름 뒤에 이어서 한 말. 이름만 불렀으면 빈 글.


@dataclass(frozen=True)
class Heard:
    text: str
    via: str  # "wake": 이름과 함께 한 말, "wake_only": 이름만 부름, "command": 듣기를 켠 뒤 한 말


def _pattern(name: str) -> re.Pattern[str]:
    # 받아쓰기가 '모 모야'처럼 띄어 쓰기도 해서 글자 사이 빈칸을 허용한다.
    spaced = r"\s*".join(re.escape(char) for char in name if not char.isspace())
    return re.compile(rf"{spaced}{VOCATIVE}{BOUNDARY}")


def find_wake(text: str, names: Sequence[str]) -> WakeMatch | None:
    for name in sorted(names, key=len, reverse=True):
        match = _pattern(name).search(text)
        if match is None:
            continue
        prefix = re.sub(r"\W", "", text[: match.start()])
        if len(prefix) > MAX_PREFIX_CHARS:
            continue
        return WakeMatch(text[match.end() :].strip())
    return None


class VoiceRouter:
    """받아쓴 말을 캐릭터에게 전할지 정한다."""

    def __init__(self, names: Sequence[str], *, wake_enabled: bool) -> None:
        self._names = list(names)
        self.wake_enabled = wake_enabled
        self.armed = False

    def arm(self) -> None:
        """다음에 들리는 말은 이름을 부르지 않아도 받아들인다."""
        self.armed = True

    def disarm(self) -> None:
        self.armed = False

    def on_transcript(self, text: str) -> Heard | None:
        cleaned = text.strip()
        wake = find_wake(cleaned, self._names) if cleaned else None

        if self.armed:
            command = wake.command if wake else cleaned
            if not command:
                return None
            self.armed = False
            return Heard(command, "command")

        if not self.wake_enabled or wake is None:
            return None
        if wake.command:
            return Heard(wake.command, "wake")
        self.armed = True
        return Heard("", "wake_only")
